"""
版本记录：
- v1.4.0 / 2026-08-30
  - 状态结构升级到 1.4，初始化 70 项技能和 27 枚固定勋章。
  - 修复申论验证未同步写入答题册，并增加跨记录一致性校验。
  - 增加赛季归档与新赛季重新定级命令，保留长期学习事实。

- v1.3.1 / 2026-08-30
  - 校验技能的可选近期实测快照，区分行测正确率、申论得分率与熟练度检查项。

- v1.3.0 / 2026-08-29
  - 状态结构升级到 1.3，新增模块目标、错题本、勋章和分层段位记录。
  - 支持从 1.2 无损迁移，不追溯重判既有技能、成绩或段位。

- v1.2.1 / 2026-08-29
  - 用户可见错误信息改用“调整点”，内部字段保持兼容。

- v1.2.0 / 2026-08-24
  - 新增 Hermes 与 Codex 共用的状态路径解析。
  - 新增状态初始化、校验、迁移、幂等提交、冲突检测和备份恢复。
  - 使用文件锁、同目录临时文件与原子替换保护学习记录。

用途：为 gongkao-season-coach 提供确定性的状态持久化底座。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Iterable, Mapping
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.catalogs import (
        default_medals,
        default_skills,
        merge_default_catalogs,
        refresh_medals,
    )
except ModuleNotFoundError:  # 直接执行 scripts/state_store.py
    from catalogs import (
        default_medals,
        default_skills,
        merge_default_catalogs,
        refresh_medals,
    )

try:
    from scripts.rankings import refresh_rankings
except ModuleNotFoundError:  # 直接执行 scripts/state_store.py
    from rankings import refresh_rankings

SCHEMA_VERSION = "1.4"
RULESET_VERSION = "1.4.0"
STATE_ENV_VAR = "GONGKAO_SEASON_COACH_STATE"
LOCK_TIMEOUT_SECONDS = 5.0

ATTENDANCE_STATUSES = {
    "not_started",
    "started",
    "effective",
    "planned_rest",
    "missed",
    "recovery",
}
DAILY_QUEST_STATUSES = {
    "not_generated",
    "offered",
    "accepted",
    "submitted",
    "verified",
    "reward_ready",
    "revealed",
}
REWARD_STATUSES = {"unrevealed", "revealed"}
SUPPORTED_OLD_SCHEMAS = {"0.1", "0.1.1", "1.0", "1.1", "1.2", "1.3"}


class StateError(RuntimeError):
    """状态无效、路径冲突或持久化失败。"""


class RevisionConflict(StateError):
    """正式状态已被其他操作更新。"""


def now_iso() -> str:
    """返回带时区、精确到秒的 UTC 时间戳。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_state(timestamp: str | None = None) -> dict[str, Any]:
    """创建 schema 1.4 的空状态。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "engine": {
            "ruleset_version": RULESET_VERSION,
            "state_revision": 0,
            "created_at": timestamp,
            "updated_at": timestamp,
            "last_local_date": None,
            "processed_event_ids": [],
            "migration_history": [],
            "last_error": None,
        },
        "profile": {
            "exam_type": None,
            "paper_type": None,
            "target_position": None,
            "exam_date": None,
            "timezone": "Asia/Shanghai",
            "daily_minutes": None,
            "weekend_minutes": None,
            "planned_study_days_per_week": None,
            "planned_weekdays": [],
            "task_delivery_mode": "pull",
            "task_delivery_time": None,
        },
        "goal_contract": {
            "contract_id": None,
            "campaign_id": None,
            "xingce_target": None,
            "shenlun_target": None,
            "total_target": None,
            "target_basis": None,
            "module_targets": [],
            "subject_targets": [],
            "confirmed_at": None,
            "locked_until": None,
        },
        "goal_contract_history": [],
        "campaign": {
            "campaign_id": None,
            "status": "calibrating",
            "started_at": None,
            "completed_at": None,
            "days_to_exam": None,
            "readiness_status": "calibrating",
            "readiness_percent": None,
            "readiness_components": {},
            "career_best": {},
        },
        "campaign_history": [],
        "season": {
            "season_id": None,
            "campaign_id": None,
            "number": 1,
            "status": "preseason",
            "phase": "calibration",
            "ruleset_version": RULESET_VERSION,
            "start_date": None,
            "end_date": None,
            "length_days": 7,
            "theme": None,
            "rank": "未定级",
            "stars": 0,
            "highest_rank": "未定级",
            "previous_rank": "未定级",
            "previous_stars": 0,
            "season_effective_days": 0,
            "season_completed_tasks": 0,
            "challenge_progress": {},
            "placement_progress": {
                "xingce_current": 0,
                "xingce_target": 2,
                "shenlun_current": 0,
                "shenlun_target": 2,
            },
            "ranking_mode": "season_only",
            "locked_catalog_ids": [],
            "locked_reward_catalog": [],
            "catalog_locked_at": None,
            "reward_catalog_locked_at": None,
            "revenge_quest": None,
        },
        "catalog": default_skills(),
        "wrong_answers": [],
        "error_hunts": [],
        "shenlun_portfolio": [],
        "assessments": [],
        "module_rankings": [],
        "subject_rankings": [],
        "medals": default_medals(),
        "review_queue": [],
        "attendance": {
            "today_status": "not_started",
            "momentum_level": 0,
            "current_effective_streak": 0,
            "longest_effective_streak": 0,
            "weekly_planned": 0,
            "weekly_effective": 0,
            "weekly_rate": None,
            "records": [],
        },
        "daily_quest": {
            "date": None,
            "status": "not_generated",
            "offer_id": None,
            "options": [],
            "accepted_task_id": None,
            "accepted_at": None,
            "locked_conditions": None,
            "submission_refs": [],
            "verification": None,
            "reward_bundle_id": None,
            "rerolls_used": 0,
        },
        "economy": {
            "command_points": 0,
            "command_points_cap": 6,
            "reward_bundles": [],
            "transactions": [],
        },
        "weekly_settlements": [],
        "task_history": [],
        "season_history": [],
        "rule_change_proposals": [],
    }


def canonical_state_path(
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    system_name: str | None = None,
) -> Path:
    """返回跨运行时共享的系统数据目录。"""
    env = os.environ if environ is None else environ
    user_home = Path.home() if home is None else home
    system = sys.platform if system_name is None else system_name

    if system.startswith("win"):
        base = Path(env.get("LOCALAPPDATA", user_home / "AppData" / "Local"))
    elif system == "darwin":
        base = user_home / "Library" / "Application Support"
    else:
        base = Path(env.get("XDG_DATA_HOME", user_home / ".local" / "share"))
    return base / "gongkao-season-coach" / "state.json"


def resolve_state_path(
    explicit_path: str | os.PathLike[str] | None = None,
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    system_name: str | None = None,
) -> tuple[Path, str]:
    """解析唯一主状态；发现多个旧主状态时拒绝猜测。"""
    env = os.environ if environ is None else environ
    user_home = Path.home() if home is None else home

    if explicit_path is not None:
        return Path(explicit_path).expanduser().resolve(), "explicit"
    if env.get(STATE_ENV_VAR):
        return Path(env[STATE_ENV_VAR]).expanduser().resolve(), "environment"

    canonical = canonical_state_path(
        home=user_home,
        environ=env,
        system_name=system_name,
    ).resolve()
    candidates = {
        "canonical": canonical,
        "legacy-hermes": (
            user_home / ".hermes" / "data" / "gongkao-season-coach" / "state.json"
        ).resolve(),
        "legacy-codex": (
            user_home / ".codex" / "data" / "gongkao-season-coach" / "state.json"
        ).resolve(),
    }
    existing = [(source, path) for source, path in candidates.items() if path.exists()]
    unique_existing = {path for _, path in existing}
    if len(unique_existing) > 1:
        listed = "\n".join(f"- {source}: {path}" for source, path in existing)
        raise StateError(
            "检测到多个候选主状态，已停止以避免覆盖。请使用 --state-path 或设置 "
            f"{STATE_ENV_VAR} 明确指定：\n{listed}"
        )
    if existing:
        return existing[0][1], existing[0][0]
    return canonical, "default-canonical"


def read_json(path: Path) -> dict[str, Any]:
    """以 UTF-8 读取 JSON 对象。"""
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise StateError(f"状态文件不存在：{path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"状态文件无法读取或解析：{path}；{exc}") from exc
    if not isinstance(value, dict):
        raise StateError("状态根节点必须是 JSON 对象。")
    return value


def _fill_missing(target: dict[str, Any], defaults: Mapping[str, Any]) -> None:
    for key, default_value in defaults.items():
        if key not in target:
            target[key] = copy.deepcopy(default_value)
        elif isinstance(target[key], dict) and isinstance(default_value, Mapping):
            _fill_missing(target[key], default_value)


def _stable_legacy_id(prefix: str, value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return f"{prefix}-legacy-{hashlib.sha256(encoded).hexdigest()[:12]}"


def _ensure_item_ids(items: Any, key: str, prefix: str) -> None:
    if not isinstance(items, list):
        return
    for index, item in enumerate(items):
        if isinstance(item, dict) and not item.get(key):
            item[key] = _stable_legacy_id(prefix, {"index": index, "item": item})


def _fill_item_defaults(items: Any, defaults: Mapping[str, Any]) -> None:
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        for key, value in defaults.items():
            item.setdefault(key, copy.deepcopy(value))


def migrate_state(
    state: Mapping[str, Any], timestamp: str | None = None
) -> dict[str, Any]:
    """把旧状态迁移到 1.4，保留历史事实并补齐固定目录。"""
    old_version = str(state.get("schema_version", ""))
    if old_version == SCHEMA_VERSION:
        migrated = copy.deepcopy(dict(state))
        validate_state(migrated)
        return migrated
    if old_version not in SUPPORTED_OLD_SCHEMAS:
        raise StateError(f"不支持的状态结构版本：{old_version or '缺失'}")

    old_season_ruleset = None
    if isinstance(state.get("season"), Mapping):
        old_season_ruleset = state["season"].get("ruleset_version")

    migrated = copy.deepcopy(dict(state))
    _fill_missing(migrated, default_state())
    migrated["schema_version"] = SCHEMA_VERSION
    engine = migrated["engine"]
    engine["ruleset_version"] = RULESET_VERSION
    engine.setdefault("migration_history", [])

    has_campaign_facts = any(
        (
            migrated["profile"].get("exam_type"),
            migrated["profile"].get("exam_date"),
            migrated["campaign"].get("started_at"),
            migrated.get("catalog"),
            migrated.get("task_history"),
        )
    )
    campaign_id = migrated["campaign"].get("campaign_id")
    if has_campaign_facts and not campaign_id:
        campaign_id = _stable_legacy_id(
            "campaign",
            {
                "profile": migrated["profile"],
                "started_at": migrated["campaign"].get("started_at"),
            },
        )
        migrated["campaign"]["campaign_id"] = campaign_id

    season = migrated["season"]
    if season.get("phase") == "preseason":
        season["status"] = "preseason"
        season["phase"] = "calibration"
    if not season.get("campaign_id"):
        season["campaign_id"] = campaign_id
    if campaign_id and not season.get("season_id"):
        season["season_id"] = f"{campaign_id}:season-{season.get('number', 1)}"
    previous_season_ruleset = old_season_ruleset or (
        "1.1.0" if old_version == "1.1" else f"legacy-{old_version}"
    )
    season["ruleset_version"] = RULESET_VERSION
    season["ranking_mode"] = "legacy_current"
    season_id = season.get("season_id")
    quest = migrated["daily_quest"]
    for option in quest.get("options", []):
        if isinstance(option, dict):
            option.setdefault("ruleset_version", previous_season_ruleset)
    if isinstance(quest.get("locked_conditions"), dict):
        quest["locked_conditions"].setdefault(
            "ruleset_version", previous_season_ruleset
        )
    for historical_task in migrated.get("task_history", []):
        if isinstance(historical_task, dict) and isinstance(
            historical_task.get("locked_conditions"), dict
        ):
            historical_task["locked_conditions"].setdefault(
                "ruleset_version", previous_season_ruleset
            )

    goal_contract = migrated["goal_contract"]
    if not goal_contract.get("campaign_id"):
        goal_contract["campaign_id"] = campaign_id
    if goal_contract.get("confirmed_at") and not goal_contract.get("contract_id"):
        goal_contract["contract_id"] = _stable_legacy_id("contract", goal_contract)

    for record in migrated["attendance"].get("records", []):
        if not isinstance(record, dict):
            continue
        status = record.get("status")
        record.setdefault("counts_as_effective", status in {"effective", "recovery"})
        record.setdefault("campaign_id", campaign_id)
        record.setdefault("season_id", season_id)
        record.setdefault("task_id", None)
        record.setdefault("submission_refs", [])
        record.setdefault("recorded_at", None)

    item_defaults = {
        "catalog": {
            "subject": None,
            "module": None,
            "name": None,
            "tier": None,
            "status": None,
            "forms": {},
            "thresholds": {},
            "evidence": [],
            "last_tested_at": None,
            "next_review_at": None,
            "needs_retest": False,
        },
        "wrong_answers": {
            "date": None,
            "subject": None,
            "module": None,
            "question_ref": None,
            "user_answer": None,
            "correct_answer": None,
            "error_hunt_id": None,
            "correction": None,
            "status": "recorded",
            "next_review_at": None,
        },
        "error_hunts": {
            "subject": None,
            "module": None,
            "mechanism": None,
            "status": None,
            "evidence": [],
            "next_review_at": None,
        },
        "shenlun_portfolio": {
            "date": None,
            "task_type": None,
            "prompt_ref": None,
            "submission_ref": None,
            "score": None,
            "score_source": None,
            "dimensions": {},
            "answer_text": None,
            "feedback": None,
            "word_count": None,
            "time_minutes": None,
        },
        "assessments": {
            "date": None,
            "subject": None,
            "scope": None,
            "ranked": False,
            "conditions": {},
            "score": None,
            "score_source": None,
            "evidence_refs": [],
            "rank_delta": 0,
            "ruleset_version": f"legacy-{old_version}",
        },
        "module_rankings": {
            "subject": None,
            "module": None,
            "metric": None,
            "stable_value": None,
            "rank": "未定级",
            "stars": 0,
            "next_rank": None,
            "gap_to_next": None,
            "sample_size": 0,
            "assessment_refs": [],
            "updated_at": None,
        },
        "subject_rankings": {
            "subject": None,
            "metric": None,
            "stable_value": None,
            "rank": "未定级",
            "stars": 0,
            "next_rank": None,
            "gap_to_next": None,
            "sample_size": 0,
            "assessment_refs": [],
            "updated_at": None,
        },
        "medals": {
            "name": None,
            "category": "历史",
            "description": None,
            "status": "locked",
            "condition": {},
            "progress_current": 0,
            "progress_target": 1,
            "progress_unit": "项",
            "evidence_refs": [],
            "unlocked_at": None,
        },
        "review_queue": {
            "target_type": None,
            "target_id": None,
            "due_at": None,
            "status": None,
            "source_evidence_id": None,
        },
        "task_history": {
            "date": None,
            "status": None,
            "locked_conditions": None,
            "submission_refs": [],
            "verification": None,
            "reward_id": None,
        },
        "weekly_settlements": {
            "revision": 1,
            "period_start": None,
            "period_end": None,
            "metrics": {},
            "reward_ids": [],
            "created_at": None,
        },
        "season_history": {
            "number": None,
            "ruleset_version": f"legacy-{old_version}",
            "start_date": None,
            "end_date": None,
            "rank": None,
            "stars": None,
            "trophy": None,
            "settled_at": None,
        },
        "campaign_history": {
            "exam_profile": {},
            "goal_contract_summary": {},
            "started_at": None,
            "completed_at": None,
            "final_scores": {},
            "season_ids": [],
        },
        "goal_contract_history": {
            "goal": {},
            "basis": None,
            "confirmed_at": None,
            "expired_at": None,
        },
        "rule_change_proposals": {
            "proposed_at": None,
            "reason": None,
            "expected_benefit": None,
            "side_effects": None,
            "decision": None,
            "decided_at": None,
        },
    }
    for collection_name, defaults in item_defaults.items():
        _fill_item_defaults(migrated.get(collection_name), defaults)

    for collection_name in (
        "catalog",
        "wrong_answers",
        "error_hunts",
        "shenlun_portfolio",
        "assessments",
        "module_rankings",
        "subject_rankings",
        "medals",
        "review_queue",
        "task_history",
        "weekly_settlements",
        "season_history",
        "rule_change_proposals",
        "goal_contract_history",
        "campaign_history",
    ):
        collection = migrated.get(collection_name, [])
        if not isinstance(collection, list):
            continue
        for item in collection:
            if isinstance(item, dict):
                item.setdefault("campaign_id", campaign_id)
                if collection_name not in {"campaign_history", "goal_contract_history"}:
                    item.setdefault("season_id", season_id)

    id_specs = (
        ("catalog", "id", "card"),
        ("wrong_answers", "wrong_id", "wrong"),
        ("error_hunts", "error_hunt_id", "error-hunt"),
        ("shenlun_portfolio", "portfolio_id", "portfolio"),
        ("assessments", "assessment_id", "assessment"),
        ("module_rankings", "ranking_id", "module-ranking"),
        ("subject_rankings", "ranking_id", "subject-ranking"),
        ("medals", "medal_id", "medal"),
        ("review_queue", "review_id", "review"),
        ("weekly_settlements", "week_key", "week"),
        ("season_history", "season_id", "season"),
        ("rule_change_proposals", "proposal_id", "proposal"),
        ("goal_contract_history", "contract_id", "contract"),
        ("campaign_history", "campaign_id", "campaign"),
    )
    for collection_name, id_key, prefix in id_specs:
        _ensure_item_ids(migrated.get(collection_name), id_key, prefix)

    economy = migrated["economy"]
    _fill_item_defaults(
        economy.get("reward_bundles"),
        {
            "campaign_id": campaign_id,
            "season_id": season_id,
            "date": None,
            "task_id": None,
            "submission_refs": [],
            "task_result": None,
            "attendance_awarded": False,
            "ability_changes": [],
            "error_hunt_changes": [],
            "ranked": False,
            "rank_delta": 0,
            "command_points_delta": 0,
            "set_progress": [],
            "status": "unrevealed",
            "created_at": None,
            "revealed_at": None,
        },
    )
    _fill_item_defaults(
        economy.get("transactions"),
        {
            "campaign_id": campaign_id,
            "season_id": season_id,
            "event_id": None,
            "date": None,
            "type": None,
            "delta": 0,
            "balance_after": None,
            "reason": None,
        },
    )
    _ensure_item_ids(economy.get("reward_bundles"), "reward_id", "reward")
    _ensure_item_ids(economy.get("transactions"), "transaction_id", "transaction")
    for reward in economy.get("reward_bundles", []):
        if isinstance(reward, dict):
            reward.setdefault("campaign_id", campaign_id)
            reward.setdefault("season_id", season_id)
            if reward.get("rank_delta", 0):
                reward["ranked"] = True

    migration_time = timestamp or now_iso()
    merge_default_catalogs(migrated)
    repaired, unresolved = _sync_shenlun_portfolio(migrated, strict=False)
    refresh_medals(migrated, migration_time)
    engine["migration_history"].append(
        {
            "from_schema": old_version,
            "to_schema": SCHEMA_VERSION,
            "migrated_at": migration_time,
            "historical_results_rejudged": False,
            "previous_season_ruleset": previous_season_ruleset,
            "shenlun_portfolio_repaired": repaired,
            "shenlun_portfolio_unresolved": unresolved,
        }
    )
    return migrated


def _verified_shenlun_tasks(state: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    tasks = [
        item for item in state.get("task_history", []) if isinstance(item, Mapping)
    ]
    quest = state.get("daily_quest")
    if isinstance(quest, Mapping) and quest.get("verification"):
        tasks.append(quest)
    return [
        item
        for item in tasks
        if isinstance(item.get("locked_conditions"), Mapping)
        and item["locked_conditions"].get("subject") == "申论"
        and item.get("verification")
        and item.get("submission_refs")
    ]


def _sync_shenlun_portfolio(
    state: dict[str, Any], *, strict: bool
) -> tuple[int, list[str]]:
    """把已验证申论提交同步进答题册，并报告缺少原文的旧记录。"""
    portfolio = state.setdefault("shenlun_portfolio", [])
    by_submission = {
        item.get("submission_ref"): item
        for item in portfolio
        if isinstance(item, dict) and item.get("submission_ref")
    }
    repaired = 0
    unresolved: list[str] = []
    for task in _verified_shenlun_tasks(state):
        locked = task["locked_conditions"]
        verification = task.get("verification") or {}
        if not isinstance(verification, Mapping):
            verification = {}
        answer_text = verification.get("answer_text") or verification.get(
            "submission_text"
        )
        for submission_ref in task.get("submission_refs", []):
            if not isinstance(submission_ref, str) or not submission_ref:
                continue
            item = by_submission.get(submission_ref)
            if item is None and (
                not isinstance(answer_text, str) or not answer_text.strip()
            ):
                unresolved.append(submission_ref)
                continue
            if item is None:
                item = {
                    "portfolio_id": f"portfolio:{submission_ref}",
                    "campaign_id": task.get("campaign_id")
                    or state.get("campaign", {}).get("campaign_id"),
                    "season_id": task.get("season_id")
                    or state.get("season", {}).get("season_id"),
                    "date": task.get("date"),
                    "task_type": locked.get("task_type") or locked.get("type"),
                    "prompt_ref": locked.get("prompt_ref"),
                    "submission_ref": submission_ref,
                    "score": verification.get("score"),
                    "score_source": verification.get("score_source"),
                    "dimensions": copy.deepcopy(verification.get("dimensions", {})),
                    "answer_text": answer_text,
                    "feedback": verification.get("feedback"),
                    "word_count": verification.get("word_count"),
                    "time_minutes": verification.get("time_minutes"),
                }
                portfolio.append(item)
                by_submission[submission_ref] = item
                repaired += 1
            elif isinstance(answer_text, str):
                item["answer_text"] = answer_text
                for key in (
                    "feedback",
                    "word_count",
                    "time_minutes",
                    "score",
                    "score_source",
                    "dimensions",
                ):
                    if verification.get(key) is not None:
                        item[key] = copy.deepcopy(verification[key])
            if isinstance(task.get("verification"), dict):
                changes = task["verification"].setdefault("portfolio_changes", [])
                if item["portfolio_id"] not in changes:
                    changes.append(item["portfolio_id"])
    if strict and unresolved:
        raise StateError(
            f"已验证申论任务缺少可写入答题册的作答原文：{sorted(set(unresolved))}"
        )
    return repaired, sorted(set(unresolved))


def start_new_season(
    state: Mapping[str, Any],
    *,
    start_date: str,
    end_date: str,
    theme: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """归档当前赛季并建立一个从未定级开始的新赛季。"""
    next_state = copy.deepcopy(dict(state))
    validate_state(next_state)
    season = next_state["season"]
    old_season_id = season.get("season_id")
    if old_season_id and season.get("status") != "preseason":
        if any(
            item.get("season_id") == old_season_id
            for item in next_state["season_history"]
        ):
            raise StateError(f"赛季已经归档：{old_season_id}")
        next_state["season_history"].append(
            {
                "season_id": old_season_id,
                "campaign_id": season.get("campaign_id"),
                "number": season.get("number"),
                "ruleset_version": season.get("ruleset_version"),
                "start_date": season.get("start_date"),
                "end_date": season.get("end_date"),
                "rank": season.get("rank"),
                "stars": season.get("stars"),
                "trophy": {
                    "module_rankings": copy.deepcopy(next_state["module_rankings"]),
                    "subject_rankings": copy.deepcopy(next_state["subject_rankings"]),
                },
                "settled_at": timestamp or now_iso(),
            }
        )
    previous_rank = season.get("rank", "未定级")
    previous_stars = season.get("stars", 0)
    current_number = int(season.get("number") or 1)
    number = (
        current_number if season.get("status") == "preseason" else current_number + 1
    )
    campaign_id = next_state.get("campaign", {}).get("campaign_id")
    new_season_id = f"{campaign_id or 'campaign'}:season-{number}"
    season.update(
        {
            "season_id": new_season_id,
            "campaign_id": campaign_id,
            "number": number,
            "status": "active",
            "phase": "placement",
            "ruleset_version": RULESET_VERSION,
            "start_date": start_date,
            "end_date": end_date,
            "theme": theme,
            "rank": "未定级",
            "stars": 0,
            "previous_rank": previous_rank,
            "previous_stars": previous_stars,
            "season_effective_days": 0,
            "season_completed_tasks": 0,
            "challenge_progress": {},
            "placement_progress": {
                "xingce_current": 0,
                "xingce_target": 2,
                "shenlun_current": 0,
                "shenlun_target": 2,
            },
            "ranking_mode": "season_only",
            "locked_catalog_ids": [],
            "locked_reward_catalog": [],
            "catalog_locked_at": None,
            "reward_catalog_locked_at": None,
            "revenge_quest": None,
        }
    )
    next_state["module_rankings"] = []
    next_state["subject_rankings"] = []
    next_state["daily_quest"] = copy.deepcopy(default_state()["daily_quest"])
    try:
        season_start = datetime.fromisoformat(start_date).date()
    except ValueError as exc:
        raise StateError("新赛季 start_date 必须使用 YYYY-MM-DD。") from exc
    try:
        season_end = datetime.fromisoformat(end_date).date()
    except ValueError as exc:
        raise StateError("新赛季 end_date 必须使用 YYYY-MM-DD。") from exc
    if season_end < season_start:
        raise StateError("新赛季 end_date 不能早于 start_date。")
    season["length_days"] = (season_end - season_start).days + 1
    for skill in next_state["catalog"]:
        last_tested_at = skill.get("last_tested_at")
        needs_retest = skill.get("status") in {"owned", "mastered"}
        if isinstance(last_tested_at, str):
            try:
                tested_date = datetime.fromisoformat(
                    last_tested_at.replace("Z", "+00:00")
                ).date()
                needs_retest = (season_start - tested_date).days > 30
            except ValueError:
                needs_retest = True
        skill["needs_retest"] = needs_retest
    refresh_rankings(next_state, timestamp or now_iso())
    refresh_medals(next_state, timestamp or now_iso())
    validate_state(next_state)
    return next_state


def _require_type(value: Any, expected: type | tuple[type, ...], label: str) -> None:
    if not isinstance(value, expected):
        raise StateError(f"{label} 类型无效。")


def _require_unique(items: Iterable[Any], label: str) -> None:
    values = list(items)
    if len(values) != len(set(values)):
        raise StateError(f"{label} 存在重复值。")


def _validate_iso_timestamp(value: Any, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise StateError(f"{label} 必须是带时区的 ISO 8601 字符串或 null。")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StateError(f"{label} 不是有效 ISO 8601 时间戳。") from exc
    if parsed.tzinfo is None:
        raise StateError(f"{label} 必须包含时区。")


def _collection_ids(
    items: Any,
    key: str,
    label: str,
    *,
    allow_null: bool = False,
) -> list[str]:
    _require_type(items, list, label)
    identifiers: list[str] = []
    for index, item in enumerate(items):
        _require_type(item, dict, f"{label}[{index}]")
        identifier = item.get(key)
        if identifier is None and allow_null:
            continue
        if not isinstance(identifier, str) or not identifier:
            raise StateError(f"{label}[{index}].{key} 缺失或无效。")
        identifiers.append(identifier)
    _require_unique(identifiers, f"{label}.{key}")
    return identifiers


def _require_item_fields(items: Any, fields: Iterable[str], label: str) -> None:
    _require_type(items, list, label)
    required = set(fields)
    for index, item in enumerate(items):
        _require_type(item, dict, f"{label}[{index}]")
        missing = sorted(required - set(item))
        if missing:
            raise StateError(f"{label}[{index}] 缺少字段：{missing}")


def validate_state(state: Mapping[str, Any]) -> None:
    """校验 schema 1.4 的关键类型、唯一约束与业务不变量。"""
    _require_type(state, Mapping, "state")
    if state.get("schema_version") != SCHEMA_VERSION:
        raise StateError(
            f"状态结构必须为 {SCHEMA_VERSION}，当前为 {state.get('schema_version')}。"
        )

    required_dicts = (
        "engine",
        "profile",
        "goal_contract",
        "campaign",
        "season",
        "attendance",
        "daily_quest",
        "economy",
    )
    required_lists = (
        "goal_contract_history",
        "campaign_history",
        "catalog",
        "wrong_answers",
        "error_hunts",
        "shenlun_portfolio",
        "assessments",
        "module_rankings",
        "subject_rankings",
        "medals",
        "review_queue",
        "weekly_settlements",
        "task_history",
        "season_history",
        "rule_change_proposals",
    )
    for key in required_dicts:
        _require_type(state.get(key), dict, key)
    for key in required_lists:
        _require_type(state.get(key), list, key)

    field_contracts = {
        "catalog": (
            "id",
            "subject",
            "module",
            "name",
            "tier",
            "status",
            "forms",
            "thresholds",
            "evidence",
            "last_tested_at",
            "next_review_at",
        ),
        "wrong_answers": (
            "wrong_id",
            "campaign_id",
            "season_id",
            "date",
            "subject",
            "module",
            "question_ref",
            "user_answer",
            "correct_answer",
            "error_hunt_id",
            "correction",
            "status",
            "next_review_at",
        ),
        "error_hunts": (
            "error_hunt_id",
            "campaign_id",
            "season_id",
            "subject",
            "module",
            "mechanism",
            "status",
            "evidence",
            "next_review_at",
        ),
        "shenlun_portfolio": (
            "portfolio_id",
            "campaign_id",
            "season_id",
            "date",
            "task_type",
            "prompt_ref",
            "submission_ref",
            "score",
            "score_source",
            "dimensions",
            "answer_text",
            "feedback",
            "word_count",
            "time_minutes",
        ),
        "assessments": (
            "assessment_id",
            "campaign_id",
            "season_id",
            "date",
            "subject",
            "scope",
            "ranked",
            "conditions",
            "score",
            "score_source",
            "evidence_refs",
            "rank_delta",
            "ruleset_version",
        ),
        "module_rankings": (
            "ranking_id",
            "campaign_id",
            "season_id",
            "subject",
            "module",
            "metric",
            "stable_value",
            "rank",
            "stars",
            "next_rank",
            "gap_to_next",
            "sample_size",
            "assessment_refs",
            "updated_at",
        ),
        "subject_rankings": (
            "ranking_id",
            "campaign_id",
            "season_id",
            "subject",
            "metric",
            "stable_value",
            "rank",
            "stars",
            "next_rank",
            "gap_to_next",
            "sample_size",
            "assessment_refs",
            "updated_at",
        ),
        "medals": (
            "medal_id",
            "name",
            "category",
            "description",
            "status",
            "condition",
            "progress_current",
            "progress_target",
            "progress_unit",
            "evidence_refs",
            "unlocked_at",
        ),
        "review_queue": (
            "review_id",
            "campaign_id",
            "season_id",
            "target_type",
            "target_id",
            "due_at",
            "status",
            "source_evidence_id",
        ),
        "task_history": (
            "task_id",
            "campaign_id",
            "season_id",
            "date",
            "status",
            "locked_conditions",
            "submission_refs",
            "verification",
            "reward_id",
        ),
        "weekly_settlements": (
            "week_key",
            "campaign_id",
            "season_id",
            "revision",
            "period_start",
            "period_end",
            "metrics",
            "reward_ids",
            "created_at",
        ),
        "season_history": (
            "season_id",
            "campaign_id",
            "number",
            "ruleset_version",
            "start_date",
            "end_date",
            "rank",
            "stars",
            "trophy",
            "settled_at",
        ),
        "campaign_history": (
            "campaign_id",
            "exam_profile",
            "goal_contract_summary",
            "started_at",
            "completed_at",
            "final_scores",
            "season_ids",
        ),
        "goal_contract_history": (
            "contract_id",
            "campaign_id",
            "goal",
            "basis",
            "confirmed_at",
            "expired_at",
        ),
        "rule_change_proposals": (
            "proposal_id",
            "campaign_id",
            "season_id",
            "proposed_at",
            "reason",
            "expected_benefit",
            "side_effects",
            "decision",
            "decided_at",
        ),
    }
    for collection_name, fields in field_contracts.items():
        _require_item_fields(state[collection_name], fields, collection_name)

    module_targets = state["goal_contract"].get("module_targets")
    _require_type(module_targets, list, "goal_contract.module_targets")
    _require_item_fields(
        module_targets,
        (
            "subject",
            "module",
            "metric",
            "total_points",
            "floor_value",
            "target_value",
            "stretch_value",
            "time_limit_minutes",
        ),
        "goal_contract.module_targets",
    )
    target_keys: list[tuple[Any, Any]] = []
    for index, target in enumerate(module_targets):
        subject = target.get("subject")
        module = target.get("module")
        if not isinstance(subject, str) or not subject:
            raise StateError(f"module_targets[{index}].subject 缺失。")
        if not isinstance(module, str) or not module:
            raise StateError(f"module_targets[{index}].module 缺失。")
        target_keys.append((subject, module))
        values = (
            target.get("floor_value"),
            target.get("target_value"),
            target.get("stretch_value"),
        )
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in values
        ):
            raise StateError(f"module_targets[{index}] 三条定级线必须是数字。")
        floor, goal, stretch = values
        if not 0 <= floor < goal < stretch <= 100:
            raise StateError(
                f"module_targets[{index}] 必须满足 0 ≤ 保底线 < 目标线 < 冲刺线 ≤ 100。"
            )
        if target.get("metric") not in {"accuracy", "score_rate"}:
            raise StateError(f"module_targets[{index}].metric 无效。")
        for field in ("total_points", "time_limit_minutes"):
            value = target.get(field)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value <= 0
            ):
                raise StateError(f"module_targets[{index}].{field} 无效。")
    _require_unique(target_keys, "goal_contract.module_targets 科目与模块")

    subject_targets = state["goal_contract"].get("subject_targets")
    _require_type(subject_targets, list, "goal_contract.subject_targets")
    _require_item_fields(
        subject_targets,
        ("subject", "metric", "floor_value", "target_value", "stretch_value"),
        "goal_contract.subject_targets",
    )
    subjects: list[str] = []
    for index, target in enumerate(subject_targets):
        subject = target.get("subject")
        if not isinstance(subject, str) or not subject:
            raise StateError(f"subject_targets[{index}].subject 缺失。")
        subjects.append(subject)
        values = (
            target.get("floor_value"),
            target.get("target_value"),
            target.get("stretch_value"),
        )
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in values
        ):
            raise StateError(f"subject_targets[{index}] 三条定级线必须是数字。")
        floor, goal, stretch = values
        if not 0 <= floor < goal < stretch <= 100:
            raise StateError(
                f"subject_targets[{index}] 必须满足 "
                "0 ≤ 保底线 < 目标线 < 冲刺线 ≤ 100。"
            )
        if target.get("metric") != "score":
            raise StateError(f"subject_targets[{index}].metric 必须为 score。")
    _require_unique(subjects, "goal_contract.subject_targets 科目")

    engine = state["engine"]
    revision = engine.get("state_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise StateError("engine.state_revision 必须是非负整数。")
    if engine.get("ruleset_version") != RULESET_VERSION:
        raise StateError(f"engine.ruleset_version 必须为 {RULESET_VERSION}。")
    _validate_iso_timestamp(engine.get("created_at"), "engine.created_at")
    _validate_iso_timestamp(engine.get("updated_at"), "engine.updated_at")
    _require_type(engine.get("processed_event_ids"), list, "processed_event_ids")
    _require_unique(engine["processed_event_ids"], "processed_event_ids")
    if not all(
        isinstance(item, str) and item for item in engine["processed_event_ids"]
    ):
        raise StateError("processed_event_ids 只能包含非空字符串。")
    _require_type(engine.get("migration_history"), list, "migration_history")

    readiness = state["campaign"].get("readiness_percent")
    if readiness is not None and (
        isinstance(readiness, bool)
        or not isinstance(readiness, (int, float))
        or not 0 <= readiness <= 100
    ):
        raise StateError("campaign.readiness_percent 必须为空或位于 0–100。")

    campaign_id = state["campaign"].get("campaign_id")
    season_id = state["season"].get("season_id")
    if campaign_id is not None and (
        not isinstance(campaign_id, str) or not campaign_id
    ):
        raise StateError("campaign.campaign_id 必须是非空字符串或 null。")
    if season_id is not None and (not isinstance(season_id, str) or not season_id):
        raise StateError("season.season_id 必须是非空字符串或 null。")
    if season_id and not campaign_id:
        raise StateError("存在 season_id 时必须存在 campaign_id。")
    if state["season"].get("campaign_id") != campaign_id:
        raise StateError("season.campaign_id 必须与当前 campaign_id 一致。")

    attendance = state["attendance"]
    if attendance.get("today_status") not in ATTENDANCE_STATUSES:
        raise StateError("attendance.today_status 无效。")
    momentum = attendance.get("momentum_level")
    if (
        isinstance(momentum, bool)
        or not isinstance(momentum, int)
        or not 0 <= momentum <= 3
    ):
        raise StateError("attendance.momentum_level 必须为 0–3 的整数。")
    records = attendance.get("records")
    _require_type(records, list, "attendance.records")
    _require_item_fields(
        records,
        (
            "date",
            "campaign_id",
            "season_id",
            "status",
            "counts_as_effective",
            "task_id",
            "submission_refs",
            "recorded_at",
        ),
        "attendance.records",
    )
    record_dates: list[str] = []
    for index, record in enumerate(records):
        _require_type(record, dict, f"attendance.records[{index}]")
        date = record.get("date")
        if not isinstance(date, str) or not date:
            raise StateError(f"attendance.records[{index}].date 缺失。")
        record_dates.append(date)
        status = record.get("status")
        if status not in ATTENDANCE_STATUSES - {"not_started", "started"}:
            raise StateError(f"attendance.records[{index}].status 无效。")
        counts = record.get("counts_as_effective")
        if not isinstance(counts, bool):
            raise StateError(
                f"attendance.records[{index}].counts_as_effective 必须是布尔值。"
            )
        if status == "recovery" and not counts:
            raise StateError("recovery 必须设置 counts_as_effective=true。")
        if status in {"missed", "planned_rest"} and counts:
            raise StateError(f"{status} 不得计为有效出勤。")
    _require_unique(record_dates, "attendance.records.date")

    quest = state["daily_quest"]
    if quest.get("status") not in DAILY_QUEST_STATUSES:
        raise StateError("daily_quest.status 无效。")
    _require_type(quest.get("options"), list, "daily_quest.options")
    option_ids: list[str] = []
    for index, option in enumerate(quest["options"]):
        _require_type(option, dict, f"daily_quest.options[{index}]")
        task_id = option.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise StateError(f"daily_quest.options[{index}].task_id 缺失。")
        option_ids.append(task_id)
        if option.get("offer_id") != quest.get("offer_id"):
            raise StateError("任务选项的 offer_id 必须等于 daily_quest.offer_id。")
    _require_unique(option_ids, "daily_quest.options.task_id")
    fixed_medal_ids = {item["medal_id"] for item in default_medals()}
    locked_medal_ids = {
        item.get("medal_id")
        for item in state["medals"]
        if item.get("status") == "locked" and item.get("medal_id") in fixed_medal_ids
    }
    if locked_medal_ids:
        for index, option in enumerate(quest["options"]):
            option_ruleset = option.get("ruleset_version") or state["season"].get(
                "ruleset_version"
            )
            if option_ruleset != RULESET_VERSION:
                continue
            targets = option.get("medal_targets")
            if not isinstance(targets, list) or not targets:
                raise StateError(
                    f"daily_quest.options[{index}] 必须推进至少一枚未点亮勋章。"
                )
            unknown = set(targets) - locked_medal_ids
            if unknown:
                raise StateError(
                    f"daily_quest.options[{index}].medal_targets 无效：{sorted(unknown)}"
                )
    accepted_task_id = quest.get("accepted_task_id")
    if accepted_task_id is not None:
        if accepted_task_id not in option_ids:
            raise StateError("accepted_task_id 必须来自当前 options。")
        if not isinstance(quest.get("locked_conditions"), dict):
            raise StateError("已接取任务必须保存 locked_conditions。")

    economy = state["economy"]
    _require_item_fields(
        economy.get("reward_bundles"),
        (
            "reward_id",
            "campaign_id",
            "season_id",
            "date",
            "task_id",
            "submission_refs",
            "task_result",
            "attendance_awarded",
            "ability_changes",
            "error_hunt_changes",
            "ranked",
            "rank_delta",
            "command_points_delta",
            "set_progress",
            "status",
            "created_at",
            "revealed_at",
        ),
        "economy.reward_bundles",
    )
    _require_item_fields(
        economy.get("transactions"),
        (
            "transaction_id",
            "campaign_id",
            "season_id",
            "event_id",
            "date",
            "type",
            "delta",
            "balance_after",
            "reason",
        ),
        "economy.transactions",
    )
    points = economy.get("command_points")
    cap = economy.get("command_points_cap")
    if any(
        isinstance(value, bool) or not isinstance(value, int) for value in (points, cap)
    ):
        raise StateError("调整点与上限必须是整数。")
    if cap < 0 or not 0 <= points <= cap:
        raise StateError("调整点必须位于 0 与上限之间。")

    task_history_ids = _collection_ids(
        state["task_history"],
        "task_id",
        "task_history",
    )
    known_task_ids = set(option_ids) | set(task_history_ids)
    if accepted_task_id:
        known_task_ids.add(accepted_task_id)

    reward_ids = _collection_ids(
        economy.get("reward_bundles"),
        "reward_id",
        "economy.reward_bundles",
    )
    rewarded_task_ids: list[str] = []
    for index, reward in enumerate(economy["reward_bundles"]):
        task_id = reward.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise StateError(f"reward_bundles[{index}].task_id 缺失。")
        rewarded_task_ids.append(task_id)
        if task_id not in known_task_ids:
            raise StateError(f"奖励引用了不存在的任务：{task_id}")
        if reward.get("status") not in REWARD_STATUSES:
            raise StateError(f"reward_bundles[{index}].status 无效。")
        rank_delta = reward.get("rank_delta", 0)
        if rank_delta and not reward.get("ranked", False):
            raise StateError("非 ranked 奖励的 rank_delta 必须为 0。")
    _require_unique(rewarded_task_ids, "reward_bundles.task_id")
    if quest.get("reward_bundle_id") not in {None, *reward_ids}:
        raise StateError("daily_quest.reward_bundle_id 引用了不存在的奖励。")

    _collection_ids(
        economy.get("transactions"),
        "transaction_id",
        "economy.transactions",
    )
    _collection_ids(state["catalog"], "id", "catalog")
    _collection_ids(state["wrong_answers"], "wrong_id", "wrong_answers")
    error_hunt_ids = _collection_ids(
        state["error_hunts"], "error_hunt_id", "error_hunts"
    )
    portfolio_ids = _collection_ids(
        state["shenlun_portfolio"],
        "portfolio_id",
        "shenlun_portfolio",
    )
    portfolio_submission_refs = [
        item.get("submission_ref")
        for item in state["shenlun_portfolio"]
        if item.get("submission_ref") is not None
    ]
    _require_unique(portfolio_submission_refs, "shenlun_portfolio.submission_ref")
    _collection_ids(state["module_rankings"], "ranking_id", "module_rankings")
    _collection_ids(state["subject_rankings"], "ranking_id", "subject_rankings")
    _collection_ids(state["medals"], "medal_id", "medals")
    _collection_ids(state["review_queue"], "review_id", "review_queue")
    _collection_ids(
        state["weekly_settlements"],
        "week_key",
        "weekly_settlements",
    )
    _collection_ids(state["season_history"], "season_id", "season_history")
    _collection_ids(
        state["campaign_history"],
        "campaign_id",
        "campaign_history",
    )
    _collection_ids(
        state["goal_contract_history"],
        "contract_id",
        "goal_contract_history",
    )
    _collection_ids(
        state["rule_change_proposals"],
        "proposal_id",
        "rule_change_proposals",
    )

    assessment_ids = _collection_ids(
        state["assessments"],
        "assessment_id",
        "assessments",
    )
    standard_skills = [
        item for item in state["catalog"] if item.get("tier") == "standard"
    ]
    expected_skill_ids = {item["id"] for item in default_skills()}
    if {item.get("id") for item in standard_skills} != expected_skill_ids:
        raise StateError("catalog 必须完整包含 70 项标准技能。")
    expected_medal_ids = {item["medal_id"] for item in default_medals()}
    if not expected_medal_ids.issubset(
        {item.get("medal_id") for item in state["medals"]}
    ):
        raise StateError("medals 必须完整包含 27 枚固定勋章。")
    for index, skill in enumerate(state["catalog"]):
        status = skill.get("status")
        if status not in {"silhouette", "discovered", "owned", "mastered"}:
            raise StateError(f"catalog[{index}].status 无效。")
        if (
            skill.get("tier") == "standard"
            and not skill.get("legacy_status", False)
            and status in {"owned", "mastered"}
        ):
            if not skill.get("thresholds"):
                raise StateError(
                    f"catalog[{index}] 未锁定熟练度门槛，最高只能为练习中。"
                )
            forms = skill.get("forms", {})
            exam_ready = (
                forms.get("transfer") is True
                if skill.get("subject") == "申论"
                else forms.get("timed") is True and forms.get("mixed") is True
            )
            if not exam_ready:
                raise StateError(f"catalog[{index}] 尚未满足考场可用检查项。")
            if status == "mastered" and forms.get("retained") is not True:
                raise StateError(f"catalog[{index}] 未通过延迟复测，不能稳定掌握。")
        performance = skill.get("recent_performance")
        if performance is None:
            continue
        if not isinstance(performance, dict):
            raise StateError(f"catalog[{index}].recent_performance 必须是对象。")
        required_fields = {
            "metric",
            "value",
            "sample_count",
            "question_count",
            "window_label",
            "updated_at",
        }
        missing = required_fields - performance.keys()
        if missing:
            raise StateError(
                f"catalog[{index}].recent_performance 缺少字段：{sorted(missing)}"
            )
        metric = performance.get("metric")
        if metric not in {"accuracy", "score_rate"}:
            raise StateError(f"catalog[{index}].recent_performance.metric 无效。")
        value = performance.get("value")
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 <= value <= 100
        ):
            raise StateError(
                f"catalog[{index}].recent_performance.value 必须位于 0 至 100。"
            )
        for field in ("sample_count", "question_count"):
            field_value = performance.get(field)
            if field_value is not None and (
                isinstance(field_value, bool)
                or not isinstance(field_value, int)
                or field_value < 0
            ):
                raise StateError(
                    f"catalog[{index}].recent_performance.{field} 必须是非负整数或 null。"
                )
        for field in ("window_label", "updated_at"):
            field_value = performance.get(field)
            if field_value is not None and not isinstance(field_value, str):
                raise StateError(
                    f"catalog[{index}].recent_performance.{field} 必须是字符串或 null。"
                )
    for index, assessment in enumerate(state["assessments"]):
        rank_delta = assessment.get("rank_delta", 0)
        if rank_delta and not assessment.get("ranked", False):
            raise StateError(f"assessments[{index}] 非 ranked 但改变了星级。")
        if assessment.get("ruleset_version") == RULESET_VERSION and rank_delta:
            raise StateError("1.4 规则使用本赛季战绩计算段位，rank_delta 必须为 0。")
    _require_unique(assessment_ids, "assessments.assessment_id")

    for index, wrong in enumerate(state["wrong_answers"]):
        if wrong.get("status") not in {
            "recorded",
            "corrected",
            "review_due",
            "resolved",
        }:
            raise StateError(f"wrong_answers[{index}].status 无效。")
        error_hunt_id = wrong.get("error_hunt_id")
        if error_hunt_id is not None and error_hunt_id not in error_hunt_ids:
            raise StateError(
                f"wrong_answers[{index}].error_hunt_id 引用了不存在的易错点。"
            )
    for index, error_hunt in enumerate(state["error_hunts"]):
        if error_hunt.get("status") not in {
            "spotted",
            "identified",
            "countered",
            "sealed",
        }:
            raise StateError(f"error_hunts[{index}].status 无效。")
    valid_ranks = {"未定级", "青铜", "白银", "黄金", "钻石", "大师", "王者"}
    module_ranking_keys: list[tuple[Any, Any]] = []
    subject_ranking_keys: list[Any] = []
    for collection_name in ("module_rankings", "subject_rankings"):
        for index, ranking in enumerate(state[collection_name]):
            if ranking.get("rank") not in valid_ranks:
                raise StateError(f"{collection_name}[{index}].rank 无效。")
            stars = ranking.get("stars")
            if (
                isinstance(stars, bool)
                or not isinstance(stars, int)
                or not 0 <= stars <= 3
            ):
                raise StateError(f"{collection_name}[{index}].stars 必须位于 0 至 3。")
            if ranking.get("rank") == "未定级" and stars != 0:
                raise StateError(f"{collection_name}[{index}] 未定级时星数必须为 0。")
            if ranking.get("rank") != "未定级" and stars == 0:
                raise StateError(
                    f"{collection_name}[{index}] 已定级时星数必须为 1 至 3。"
                )
            if ranking.get("metric") not in {"accuracy", "score", "score_rate"}:
                raise StateError(f"{collection_name}[{index}].metric 无效。")
            next_rank = ranking.get("next_rank")
            if next_rank is not None and next_rank not in valid_ranks - {"未定级"}:
                raise StateError(f"{collection_name}[{index}].next_rank 无效。")
            gap_to_next = ranking.get("gap_to_next")
            if gap_to_next is not None and (
                isinstance(gap_to_next, bool)
                or not isinstance(gap_to_next, (int, float))
                or gap_to_next < 0
            ):
                raise StateError(f"{collection_name}[{index}].gap_to_next 无效。")
            stable_value = ranking.get("stable_value")
            if stable_value is not None and (
                isinstance(stable_value, bool)
                or not isinstance(stable_value, (int, float))
                or not 0 <= stable_value <= 100
            ):
                raise StateError(
                    f"{collection_name}[{index}].stable_value 必须位于 0 至 100。"
                )
            sample_size = ranking.get("sample_size")
            if (
                isinstance(sample_size, bool)
                or not isinstance(sample_size, int)
                or sample_size < 0
            ):
                raise StateError(
                    f"{collection_name}[{index}].sample_size 必须是非负整数。"
                )
            unknown_assessments = set(ranking.get("assessment_refs", [])) - set(
                assessment_ids
            )
            if unknown_assessments:
                raise StateError(
                    f"{collection_name}[{index}] 引用了不存在的战绩："
                    f"{sorted(unknown_assessments)}"
                )
            current_refs = {
                item["assessment_id"]
                for item in state["assessments"]
                if item.get("season_id") == state["season"].get("season_id")
            }
            if set(ranking.get("assessment_refs", [])) - current_refs:
                raise StateError(f"{collection_name}[{index}] 不得使用旧赛季战绩定级。")
            if collection_name == "module_rankings":
                module_ranking_keys.append(
                    (ranking.get("subject"), ranking.get("module"))
                )
            else:
                if ranking.get("subject") not in {"行测", "申论"}:
                    raise StateError(
                        f"subject_rankings[{index}].subject 必须为行测或申论。"
                    )
                subject_ranking_keys.append(ranking.get("subject"))
    _require_unique(module_ranking_keys, "module_rankings 科目与模块")
    _require_unique(subject_ranking_keys, "subject_rankings 科目")
    for index, medal in enumerate(state["medals"]):
        if medal.get("status") not in {"locked", "unlocked"}:
            raise StateError(f"medals[{index}].status 无效。")
        for field in ("progress_current", "progress_target"):
            value = medal.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise StateError(f"medals[{index}].{field} 必须是非负整数。")

    portfolio_id_set = set(portfolio_ids)
    portfolio_by_id = {
        item["portfolio_id"]: item for item in state["shenlun_portfolio"]
    }
    for task in _verified_shenlun_tasks(state):
        locked = task["locked_conditions"]
        verification = task.get("verification") or {}
        changes = verification.get("portfolio_changes", [])
        for submission_ref in task.get("submission_refs", []):
            expected_id = f"portfolio:{submission_ref}"
            if expected_id not in portfolio_id_set:
                raise StateError(f"已验证申论任务缺少答题册记录：{submission_ref}")
            task_ruleset = locked.get("ruleset_version") or state["season"].get(
                "ruleset_version"
            )
            if task_ruleset == RULESET_VERSION and expected_id not in changes:
                raise StateError(f"申论验证结果未声明 portfolio_changes：{expected_id}")
            answer_text = portfolio_by_id[expected_id].get("answer_text")
            if task_ruleset == RULESET_VERSION and (
                not isinstance(answer_text, str) or not answer_text.strip()
            ):
                raise StateError(f"申论答题册缺少作答原文：{expected_id}")


class FileLock(AbstractContextManager["FileLock"]):
    """只依赖标准库的跨平台独占文件锁。"""

    def __init__(self, path: Path, timeout: float = LOCK_TIMEOUT_SECONDS) -> None:
        self.path = path
        self.timeout = timeout
        self.handle: Any = None

    def __enter__(self) -> FileLock:  # noqa: PYI034 - 保持 Python 3.10 兼容
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"\0")
            self.handle.flush()

        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._lock()
                return self
            except OSError as exc:
                if time.monotonic() >= deadline:
                    self.handle.close()
                    self.handle = None
                    raise StateError(f"等待状态锁超时：{self.path}") from exc
                time.sleep(0.05)

    def _lock(self) -> None:
        self.handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def __exit__(self, *exc_info: object) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def _backup_path(state_path: Path) -> Path:
    return state_path.with_name("state.backup.json")


def _lock_path(state_path: Path) -> Path:
    return state_path.with_name("state.lock")


def _atomic_write(state_path: Path, state: Mapping[str, Any], *, backup: bool) -> None:
    validate_state(state)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if backup and state_path.exists():
        shutil.copy2(state_path, _backup_path(state_path))

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix="state.",
            suffix=".tmp",
            dir=state_path.parent,
            delete=False,
        ) as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)

        candidate = read_json(temp_path)
        validate_state(candidate)
        os.replace(temp_path, state_path)
        temp_path = None
        saved = read_json(state_path)
        validate_state(saved)
        if saved["engine"]["state_revision"] != state["engine"]["state_revision"]:
            raise StateError("原子替换后的 revision 与候选状态不一致。")
    except OSError as exc:
        raise StateError(f"状态原子保存失败：{exc}") from exc
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def _summary(state_path: Path, state: Mapping[str, Any], status: str) -> dict[str, Any]:
    return {
        "status": status,
        "path": str(state_path),
        "schema_version": state["schema_version"],
        "ruleset_version": state["engine"]["ruleset_version"],
        "state_revision": state["engine"]["state_revision"],
        "campaign_id": state["campaign"].get("campaign_id"),
        "season_id": state["season"].get("season_id"),
    }


def initialize_file(state_path: Path, timestamp: str | None = None) -> dict[str, Any]:
    """幂等初始化状态文件。"""
    with FileLock(_lock_path(state_path)):
        if state_path.exists():
            state = read_json(state_path)
            if state.get("schema_version") != SCHEMA_VERSION:
                raise StateError("状态已存在但需要迁移；请先运行 migrate。")
            validate_state(state)
            return _summary(state_path, state, "exists")
        state = default_state(timestamp or now_iso())
        _atomic_write(state_path, state, backup=False)
        return _summary(state_path, state, "initialized")


def read_current_state(state_path: Path) -> dict[str, Any]:
    state = read_json(state_path)
    if state.get("schema_version") != SCHEMA_VERSION:
        raise StateError("状态需要先迁移；运行 migrate 后再读取和修改。")
    validate_state(state)
    return state


def _identifier_set(
    state: Mapping[str, Any], path: tuple[str, ...], key: str
) -> set[str]:
    value: Any = state
    for part in path:
        value = value[part]
    return {
        item[key]
        for item in value
        if isinstance(item, dict) and isinstance(item.get(key), str)
    }


def _ensure_history_preserved(
    current: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> None:
    permanent = (
        (("catalog",), "id"),
        (("wrong_answers",), "wrong_id"),
        (("error_hunts",), "error_hunt_id"),
        (("shenlun_portfolio",), "portfolio_id"),
        (("assessments",), "assessment_id"),
        (("weekly_settlements",), "week_key"),
        (("task_history",), "task_id"),
        (("season_history",), "season_id"),
        (("campaign_history",), "campaign_id"),
        (("goal_contract_history",), "contract_id"),
        (("medals",), "medal_id"),
        (("economy", "reward_bundles"), "reward_id"),
        (("economy", "transactions"), "transaction_id"),
    )
    for path, key in permanent:
        old_ids = _identifier_set(current, path, key)
        new_ids = _identifier_set(candidate, path, key)
        missing = sorted(old_ids - new_ids)
        if missing:
            label = ".".join(path)
            raise StateError(f"候选状态删除了永久历史 {label}: {missing}")


def commit_candidate(
    state_path: Path,
    candidate: Mapping[str, Any],
    *,
    expected_revision: int,
    event_id: str,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """在 revision 和幂等检查后原子提交完整候选状态。"""
    if not event_id.strip():
        raise StateError("event_id 不能为空。")
    with FileLock(_lock_path(state_path)):
        current = read_current_state(state_path)
        processed = current["engine"]["processed_event_ids"]
        if event_id in processed:
            return _summary(state_path, current, "idempotent")
        current_revision = current["engine"]["state_revision"]
        if current_revision != expected_revision:
            raise RevisionConflict(
                f"revision 冲突：期望 {expected_revision}，正式状态为 {current_revision}。"
            )

        next_state = copy.deepcopy(dict(candidate))
        _fill_missing(next_state, default_state())
        next_state["schema_version"] = SCHEMA_VERSION
        next_engine = next_state["engine"]
        next_engine["ruleset_version"] = RULESET_VERSION
        next_engine["state_revision"] = current_revision + 1
        next_engine["created_at"] = current["engine"].get("created_at")
        next_engine["updated_at"] = timestamp or now_iso()
        next_engine["last_error"] = None
        next_engine["processed_event_ids"] = list(
            dict.fromkeys(
                [
                    *processed,
                    *next_engine.get("processed_event_ids", []),
                    event_id,
                ]
            )
        )
        next_engine["migration_history"] = copy.deepcopy(
            current["engine"].get("migration_history", [])
        )

        merge_default_catalogs(next_state)
        _sync_shenlun_portfolio(next_state, strict=True)
        current_season_id = next_state["season"].get("season_id")
        next_state["season"]["season_effective_days"] = sum(
            record.get("season_id") == current_season_id
            and record.get("counts_as_effective")
            for record in next_state["attendance"].get("records", [])
        )
        next_state["season"]["season_completed_tasks"] = sum(
            task.get("season_id") == current_season_id
            and bool(task.get("verification"))
            for task in next_state["task_history"]
        )
        if next_state["season"].get("ranking_mode") == "season_only":
            refresh_rankings(next_state, next_engine["updated_at"])
        refresh_medals(next_state, next_engine["updated_at"])
        _ensure_history_preserved(current, next_state)
        validate_state(next_state)
        _atomic_write(state_path, next_state, backup=True)
        return _summary(state_path, next_state, "committed")


def migrate_file(state_path: Path, timestamp: str | None = None) -> dict[str, Any]:
    """备份后把旧结构迁移到 1.4。"""
    with FileLock(_lock_path(state_path)):
        current = read_json(state_path)
        if current.get("schema_version") == SCHEMA_VERSION:
            validate_state(current)
            return _summary(state_path, current, "current")
        old_revision = current.get("engine", {}).get("state_revision", 0)
        if isinstance(old_revision, bool) or not isinstance(old_revision, int):
            old_revision = 0
        migrated = migrate_state(current, timestamp)
        migrated["engine"]["state_revision"] = old_revision + 1
        migrated["engine"]["updated_at"] = timestamp or now_iso()
        if migrated["engine"].get("created_at") is None:
            migrated["engine"]["created_at"] = migrated["engine"]["updated_at"]
        migration_event = f"migrate:{current.get('schema_version')}:{SCHEMA_VERSION}"
        migrated["engine"]["processed_event_ids"] = list(
            dict.fromkeys(
                [*migrated["engine"].get("processed_event_ids", []), migration_event]
            )
        )
        validate_state(migrated)
        _atomic_write(state_path, migrated, backup=True)
        return _summary(state_path, migrated, "migrated")


def start_new_season_file(
    state_path: Path,
    *,
    start_date: str,
    end_date: str,
    theme: str | None,
    event_id: str,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """原子归档当前赛季并开启下一赛季。"""
    current = read_current_state(state_path)
    candidate = start_new_season(
        current,
        start_date=start_date,
        end_date=end_date,
        theme=theme,
        timestamp=timestamp,
    )
    return commit_candidate(
        state_path,
        candidate,
        expected_revision=current["engine"]["state_revision"],
        event_id=event_id,
        timestamp=timestamp,
    )


def recover_from_backup(
    state_path: Path,
    *,
    event_id: str,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """仅在正式状态损坏时恢复备份，并保留损坏副本。"""
    if not event_id.strip():
        raise StateError("恢复操作必须提供 event_id。")
    backup_path = _backup_path(state_path)
    with FileLock(_lock_path(state_path)):
        if not backup_path.exists():
            raise StateError(f"备份不存在：{backup_path}")
        if state_path.exists():
            try:
                valid_current = read_current_state(state_path)
            except StateError:
                valid_current = None
            if valid_current is not None:
                if event_id in valid_current["engine"]["processed_event_ids"]:
                    return _summary(state_path, valid_current, "idempotent")
                raise StateError("正式状态仍然有效，拒绝用旧备份覆盖。")

        recovered = read_json(backup_path)
        if recovered.get("schema_version") != SCHEMA_VERSION:
            recovered = migrate_state(recovered, timestamp)
        validate_state(recovered)
        stamp = (timestamp or now_iso()).replace(":", "-")
        if state_path.exists():
            corrupt_path = state_path.with_name(f"state.corrupt.{stamp}.json")
            shutil.copy2(state_path, corrupt_path)

        recovered["engine"]["state_revision"] += 1
        recovered["engine"]["updated_at"] = timestamp or now_iso()
        recovered["engine"]["last_error"] = {
            "type": "recovered_from_backup",
            "event_id": event_id,
            "recovered_at": recovered["engine"]["updated_at"],
        }
        recovered["engine"]["processed_event_ids"] = list(
            dict.fromkeys([*recovered["engine"]["processed_event_ids"], event_id])
        )
        _atomic_write(state_path, recovered, backup=False)
        return _summary(state_path, recovered, "recovered")


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="公考赛季教练状态管理器")
    parser.add_argument(
        "--state-path",
        help=f"显式状态路径；也可设置环境变量 {STATE_ENV_VAR}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("resolve-path", help="显示实际采用的唯一主状态路径")
    subparsers.add_parser("init", help="不存在时初始化状态")
    subparsers.add_parser("read", help="校验并输出正式状态")
    subparsers.add_parser("validate", help="只校验正式状态")
    subparsers.add_parser("migrate", help="备份并迁移旧状态")

    season_parser = subparsers.add_parser("new-season", help="归档当前赛季并重新定级")
    season_parser.add_argument("--start-date", required=True)
    season_parser.add_argument("--end-date", required=True)
    season_parser.add_argument("--theme")
    season_parser.add_argument("--event-id", required=True)

    commit_parser = subparsers.add_parser("commit", help="原子提交完整候选状态")
    commit_parser.add_argument("--input", required=True, help="候选 JSON 文件")
    commit_parser.add_argument("--expected-revision", required=True, type=int)
    commit_parser.add_argument("--event-id", required=True)

    recover_parser = subparsers.add_parser("recover", help="从备份恢复损坏状态")
    recover_parser.add_argument("--event-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        state_path, source = resolve_state_path(args.state_path)
        if args.command == "resolve-path":
            _print_json({"path": str(state_path), "source": source})
        elif args.command == "init":
            _print_json(initialize_file(state_path))
        elif args.command == "read":
            _print_json(read_current_state(state_path))
        elif args.command == "validate":
            state = read_current_state(state_path)
            _print_json(_summary(state_path, state, "valid"))
        elif args.command == "migrate":
            _print_json(migrate_file(state_path))
        elif args.command == "new-season":
            _print_json(
                start_new_season_file(
                    state_path,
                    start_date=args.start_date,
                    end_date=args.end_date,
                    theme=args.theme,
                    event_id=args.event_id,
                )
            )
        elif args.command == "commit":
            candidate = read_json(Path(args.input).expanduser().resolve())
            _print_json(
                commit_candidate(
                    state_path,
                    candidate,
                    expected_revision=args.expected_revision,
                    event_id=args.event_id,
                )
            )
        elif args.command == "recover":
            _print_json(
                recover_from_backup(
                    state_path,
                    event_id=args.event_id,
                )
            )
        return 0
    except RevisionConflict as exc:
        print(f"REVISION_CONFLICT: {exc}", file=sys.stderr)
        return 3
    except StateError as exc:
        print(f"STATE_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
