"""
版本记录：
- v1.4.0 / 2026-08-30
  - 按当前赛季证据重算模块、科目与综合段位。
  - 新赛季只使用本赛季排位，行测和申论各需两份有效定级证据。

用途：实现赛季重新定级规则，避免旧赛季战绩混入当前段位。
"""

from __future__ import annotations

import statistics
from typing import Any

try:
    from scripts.progression import RANK_ORDER, classify_rank
except ModuleNotFoundError:  # 直接执行 scripts 下脚本
    from progression import RANK_ORDER, classify_rank


def _target_map(items: list[dict[str, Any]], key: str) -> dict[Any, dict[str, Any]]:
    return {item.get(key): item for item in items if item.get(key)}


def _score(item: dict[str, Any]) -> float | None:
    value = item.get("score")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _ranking(
    ranking_id: str,
    state: dict[str, Any],
    target: dict[str, Any],
    evidence: list[dict[str, Any]],
    *,
    subject: str,
    module: str | None,
    qualified: bool,
    timestamp: str | None,
) -> dict[str, Any]:
    scores = [score for item in evidence if (score := _score(item)) is not None]
    stable = statistics.median(scores) if scores else None
    result = classify_rank(
        stable,
        floor_value=target["floor_value"],
        target_value=target["target_value"],
        stretch_value=target["stretch_value"],
        qualified=qualified,
    )
    ranking = {
        "ranking_id": ranking_id,
        "campaign_id": state["campaign"].get("campaign_id"),
        "season_id": state["season"].get("season_id"),
        "subject": subject,
        "metric": target.get("metric", "score"),
        "sample_size": len(evidence),
        "assessment_refs": [item["assessment_id"] for item in evidence],
        "updated_at": timestamp,
        **result,
    }
    if module is not None:
        ranking["module"] = module
    return ranking


def refresh_rankings(state: dict[str, Any], timestamp: str | None = None) -> None:
    """只根据当前 season_id 下的有效排位战绩重算段位。"""
    season_id = state["season"].get("season_id")
    assessments = [
        item
        for item in state.get("assessments", [])
        if item.get("season_id") == season_id
        and item.get("ranked")
        and _score(item) is not None
    ]
    module_targets = _target_map(
        state["goal_contract"].get("module_targets", []), "module"
    )
    subject_targets = _target_map(
        state["goal_contract"].get("subject_targets", []), "subject"
    )

    module_rankings = []
    for module, target in module_targets.items():
        evidence = [
            item
            for item in assessments
            if item.get("conditions", {}).get("module") == module
            and item.get("conditions", {}).get("timed")
            and item.get("conditions", {}).get("comparable", True)
        ]
        question_count = sum(
            int(item.get("conditions", {}).get("question_count") or 0)
            for item in evidence
        )
        module_rankings.append(
            _ranking(
                f"module-ranking:{season_id}:{module}",
                state,
                target,
                evidence,
                subject=target.get("subject", "行测"),
                module=module,
                qualified=len(evidence) >= 3 and question_count >= 30,
                timestamp=timestamp,
            )
        )
    state["module_rankings"] = module_rankings

    xingce = [
        item
        for item in assessments
        if item.get("subject") == "行测"
        and item.get("conditions", {}).get("full_paper")
        and item.get("conditions", {}).get("timed")
        and item.get("conditions", {}).get("comparable", True)
    ]
    shenlun = [
        item
        for item in assessments
        if item.get("subject") == "申论"
        and item.get("conditions", {}).get("complete_answer")
        and item.get("conditions", {}).get("comparable", True)
    ]
    subject_rankings = []
    if "行测" in subject_targets:
        subject_rankings.append(
            _ranking(
                f"subject-ranking:{season_id}:xingce",
                state,
                subject_targets["行测"],
                xingce,
                subject="行测",
                module=None,
                qualified=len(xingce) >= 2,
                timestamp=timestamp,
            )
        )
    if "申论" in subject_targets:
        external = [
            item for item in shenlun if item.get("score_source") != "ai_internal"
        ]
        subject_rankings.append(
            _ranking(
                f"subject-ranking:{season_id}:shenlun",
                state,
                subject_targets["申论"],
                shenlun,
                subject="申论",
                module=None,
                qualified=len(shenlun) >= 2 and bool(external),
                timestamp=timestamp,
            )
        )
    state["subject_rankings"] = subject_rankings
    state["season"]["placement_progress"] = {
        "xingce_current": min(len(xingce), 2),
        "xingce_target": 2,
        "shenlun_current": min(len(shenlun), 2),
        "shenlun_target": 2,
    }

    by_subject = {item["subject"]: item for item in subject_rankings}
    if all(
        subject in by_subject and by_subject[subject].get("rank") != "未定级"
        for subject in ("行测", "申论")
    ):
        lower = min(
            (by_subject[subject] for subject in ("行测", "申论")),
            key=lambda item: RANK_ORDER.index(item["rank"]),
        )
        state["season"]["rank"] = lower["rank"]
        state["season"]["stars"] = lower["stars"]
        highest = state["season"].get("highest_rank", "未定级")
        if highest == "未定级" or RANK_ORDER.index(lower["rank"]) > RANK_ORDER.index(
            highest
        ):
            state["season"]["highest_rank"] = lower["rank"]
    else:
        state["season"]["rank"] = "未定级"
        state["season"]["stars"] = 0
