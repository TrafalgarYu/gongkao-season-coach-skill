"""
版本记录：
- v1.0.0 / 2026-08-30
  - 把旧版复合考试成绩拆成行测、申论两条历史基线战绩。
  - 将五项旧技能证据融合进固定 70 项技能，并纠正基础练习的熟练度。
  - 为历史申论单题评分补充满分、得分率和口径状态。

用途：在状态迁移期间一次性清理 1.4 及更早版本留下的已知数据形态。
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

LEGACY_SKILL_TARGETS = {
    "xingce-ziliao-abrx": ("skill-02",),
    "xingce-judge-jiaqiang-xueruo": ("skill-47", "skill-48"),
    "xingce-yanyu-pianduan": ("skill-30",),
    "shenlun-gaikuo": ("skill-59", "skill-60", "skill-61"),
    "shenlun-zonghe": ("skill-62",),
}
TARGET_SUBRESULT_KEYS = {
    "skill-47": "加强",
    "skill-48": "削弱",
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _score_rate(score: Any, score_max: Any) -> float | None:
    numeric_score = _number(score)
    numeric_max = _number(score_max)
    if numeric_score is None or numeric_max is None or numeric_max <= 0:
        return None
    return round(numeric_score / numeric_max * 100, 2)


def _evidence_key(item: Mapping[str, Any]) -> str:
    return str(
        item.get("evidence_id")
        or item.get("submission_ref")
        or item.get("task_id")
        or repr(sorted(item.items()))
    )


def _merge_skill(source: Mapping[str, Any], target: dict[str, Any]) -> None:
    existing = {
        _evidence_key(item)
        for item in target.get("evidence", [])
        if isinstance(item, Mapping)
    }
    for evidence in source.get("evidence", []):
        if not isinstance(evidence, Mapping):
            continue
        key = _evidence_key(evidence)
        if key not in existing:
            target.setdefault("evidence", []).append(copy.deepcopy(dict(evidence)))
            existing.add(key)

    if target.get("evidence") and target.get("status") == "silhouette":
        target["status"] = "discovered"
    if source.get("forms", {}).get("base") is True:
        target.setdefault("forms", {})["base"] = True
    if not target.get("thresholds") and source.get("thresholds"):
        target["thresholds"] = copy.deepcopy(source["thresholds"])

    tested = [
        value
        for value in (target.get("last_tested_at"), source.get("last_tested_at"))
        if isinstance(value, str) and value
    ]
    if tested:
        target["last_tested_at"] = max(tested)


def _performance_from_evidence(skill: Mapping[str, Any]) -> dict[str, Any] | None:
    evidence = [item for item in skill.get("evidence", []) if isinstance(item, Mapping)]
    if not evidence:
        return None

    score_values: list[float] = []
    total = 0
    correct = 0
    tested_at: list[str] = []
    sub_values: list[float] = []
    sub_key = TARGET_SUBRESULT_KEYS.get(str(skill.get("id")))
    for item in evidence:
        result = item.get("result")
        if not isinstance(result, Mapping):
            continue
        if sub_key and isinstance(result.get("sub"), Mapping):
            raw_sub_value = result["sub"].get(sub_key)
            raw_values = (
                raw_sub_value if isinstance(raw_sub_value, list) else [raw_sub_value]
            )
            numeric_values = [
                value
                for value in (_number(raw) for raw in raw_values)
                if value is not None
            ]
            if numeric_values:
                sub_values.append(sum(numeric_values) / len(numeric_values))
        item_total = _number(result.get("total"))
        item_correct = _number(result.get("correct"))
        if item_total is not None and item_total > 0 and item_correct is not None:
            total += int(item_total)
            correct += int(item_correct)
        item_score = _number(result.get("score"))
        if item_score is not None:
            score_values.append(item_score)
        if isinstance(item.get("tested_at"), str):
            tested_at.append(item["tested_at"])

    if sub_values:
        raw_value = sum(sub_values) / len(sub_values)
        value = round(raw_value * 100 if raw_value <= 1 else raw_value, 2)
        metric = "accuracy"
        question_count = None
    elif total:
        value = round(correct / total * 100, 2)
        metric = "accuracy"
        question_count: int | None = total
    elif score_values:
        value = round(sum(score_values) / len(score_values), 2)
        metric = "score_rate"
        question_count = None
    else:
        return None

    return {
        "metric": metric,
        "value": value,
        "sample_count": len(evidence),
        "question_count": question_count,
        "window_label": "历史基础练习，不代表考场条件",
        "updated_at": max(tested_at) if tested_at else None,
    }


def normalize_legacy_skills(state: dict[str, Any]) -> dict[str, Any]:
    catalog = state.get("catalog", [])
    by_id = {item.get("id"): item for item in catalog if isinstance(item, dict)}
    custom_ids = {
        item.get("id")
        for item in catalog
        if isinstance(item, dict) and item.get("tier") == "custom"
    }
    unknown = sorted(
        str(item) for item in custom_ids - set(LEGACY_SKILL_TARGETS) if item
    )
    if unknown:
        return {
            "removed_custom_skill_ids": [],
            "mapped_standard_skill_ids": [],
            "downgraded_standard_skill_ids": [],
            "unresolved_custom_skill_ids": unknown,
        }

    mapped: set[str] = set()
    for source_id, target_ids in LEGACY_SKILL_TARGETS.items():
        source = by_id.get(source_id)
        if not isinstance(source, Mapping):
            continue
        for target_id in target_ids:
            target = by_id.get(target_id)
            if not isinstance(target, dict):
                continue
            _merge_skill(source, target)
            mapped.add(target_id)

    removed = sorted(str(item) for item in custom_ids if item)
    state["catalog"] = [
        item
        for item in catalog
        if not (isinstance(item, dict) and item.get("tier") == "custom")
    ]

    downgraded: list[str] = []
    for skill in state["catalog"]:
        if not isinstance(skill, dict) or skill.get("tier") != "standard":
            continue
        forms = skill.get("forms", {})
        exam_ready = (
            forms.get("transfer") is True
            if skill.get("subject") == "申论"
            else forms.get("timed") is True and forms.get("mixed") is True
        )
        if skill.get("status") in {"owned", "mastered"} and not exam_ready:
            skill["status"] = "discovered"
            downgraded.append(str(skill.get("id")))
        skill["legacy_status"] = False
        performance = _performance_from_evidence(skill)
        if performance is not None and skill.get("id") in mapped | set(downgraded):
            skill["recent_performance"] = performance

    return {
        "removed_custom_skill_ids": removed,
        "mapped_standard_skill_ids": sorted(mapped),
        "downgraded_standard_skill_ids": sorted(set(downgraded)),
        "unresolved_custom_skill_ids": [],
    }


def normalize_legacy_assessments(state: dict[str, Any]) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    split_ids: list[str] = []
    for item in state.get("assessments", []):
        if not isinstance(item, dict):
            continue
        xingce = _number(item.get("xingce"))
        shenlun = _number(item.get("shenlun"))
        if item.get("subject") is None and xingce is not None and shenlun is not None:
            source_id = str(item.get("assessment_id"))
            shared_conditions = {
                "historical_baseline": True,
                "full_paper": True,
                "source_assessment_id": source_id,
                "exam_label": item.get("date"),
                "reported_total": item.get("total"),
                "source_label": item.get("source"),
                "original_grade": item.get("grade"),
            }
            for subject, score, suffix in (
                ("行测", xingce, "xingce"),
                ("申论", shenlun, "shenlun"),
            ):
                normalized.append(
                    {
                        "assessment_id": f"{source_id}:{suffix}",
                        "campaign_id": item.get("campaign_id"),
                        "season_id": item.get("season_id"),
                        "date": None,
                        "subject": subject,
                        "scope": "历史考试基线",
                        "ranked": False,
                        "conditions": copy.deepcopy(shared_conditions),
                        "score": score,
                        "score_max": 100,
                        "score_rate": score,
                        "normalization_status": "exact",
                        "score_source": "official",
                        "evidence_refs": list(item.get("evidence_refs", [])),
                        "rank_delta": 0,
                        "ruleset_version": item.get("ruleset_version"),
                    }
                )
            split_ids.append(source_id)
            continue

        current = copy.deepcopy(item)
        current.setdefault("score_max", None)
        current.setdefault("score_rate", None)
        if current.get("score") is None:
            current["score_max"] = None
            current["score_rate"] = None
            current["normalization_status"] = "not_scored"
        elif current.get("score_max") is not None:
            current["score_rate"] = _score_rate(current["score"], current["score_max"])
            current["normalization_status"] = "exact"
        else:
            current["score_rate"] = None
            current["normalization_status"] = "needs_review"
        normalized.append(current)
    state["assessments"] = normalized
    return {"split_assessment_ids": split_ids}


def normalize_portfolio_scores(state: dict[str, Any]) -> dict[str, Any]:
    normalized: list[str] = []
    unresolved: list[str] = []
    for item in state.get("shenlun_portfolio", []):
        if not isinstance(item, dict):
            continue
        item.setdefault("score_max", None)
        item.setdefault("score_rate", None)
        score = _number(item.get("score"))
        dimensions = item.get("dimensions")
        dimension_total = None
        if isinstance(dimensions, Mapping):
            values = [_number(value) for value in dimensions.values()]
            if values and all(value is not None for value in values):
                dimension_total = sum(value for value in values if value is not None)
        if (
            score is not None
            and item.get("score_source") == "ai_internal"
            and 0 <= score <= 100
            and dimension_total == score
        ):
            item["score_max"] = 100
            item["score_rate"] = score
            item["normalization_status"] = "exact"
            normalized.append(str(item.get("portfolio_id")))
        elif score is None:
            item["normalization_status"] = "not_scored"
        elif item.get("score_max") is not None:
            item["score_rate"] = _score_rate(item["score"], item["score_max"])
            item["normalization_status"] = "exact"
        else:
            item["normalization_status"] = "needs_review"
            unresolved.append(str(item.get("portfolio_id")))
    return {
        "normalized_portfolio_ids": normalized,
        "unresolved_portfolio_ids": unresolved,
    }


def normalize_legacy_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "skills": normalize_legacy_skills(state),
        "assessments": normalize_legacy_assessments(state),
        "portfolio": normalize_portfolio_scores(state),
    }
