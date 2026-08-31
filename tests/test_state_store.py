"""
版本记录：
- v1.7.0 / 2026-08-31
  - 覆盖五类成就、分离正确率与用时、11项能力归类和1.6历史迁移。
- v1.6.0 / 2026-08-31
  - 覆盖 40 枚新勋章、练习战绩校验和旧记录严格提取。
  - 验证重复旧勋章被替换，缺少实际用时的历史证据不补发新勋章。
- v1.5.0 / 2026-08-30
  - 覆盖旧技能融合、复合战绩拆分、评分口径规范化和迁移 dry-run。
  - 验证活动目录固定为 70 项，基础练习不得冒充考场可用。
- v1.4.1 / 2026-08-30
  - 回归覆盖旧版已掌握技能缺少 legacy_status 且未设置门槛时的无损迁移。
- v1.4.0 / 2026-08-30
  - 覆盖固定技能和勋章目录、申论答题册同步、赛季重定级及旧赛季证据隔离。

- v1.3.1 / 2026-08-30
  - 验证技能近期实测快照的指标、百分值与样本字段。

- v1.3.0 / 2026-08-29
  - 覆盖 1.2 存档迁移，以及错题本、勋章和分层段位字段。

- v1.2.0 / 2026-08-24
  - 覆盖路径解析、初始化、原子提交、幂等、冲突、迁移与恢复。
  - 覆盖永久历史保护、复归语义和 AI 内部估分排位限制。
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts import state_store

TIMESTAMP = "2026-08-24T00:00:00+00:00"
FIXTURES = Path(__file__).parent / "fixtures"


class StateStoreTests(unittest.TestCase):
    def test_default_state_is_valid(self) -> None:
        state = state_store.default_state(TIMESTAMP)

        state_store.validate_state(state)

        self.assertEqual(state["schema_version"], "1.7")
        self.assertEqual(state["engine"]["ruleset_version"], "1.7.0")
        self.assertEqual(state["goal_contract"]["module_targets"], [])
        self.assertEqual(state["goal_contract"]["subject_targets"], [])
        self.assertEqual(state["wrong_answers"], [])
        self.assertEqual(state["module_rankings"], [])
        self.assertEqual(state["subject_rankings"], [])
        self.assertEqual(len(state["catalog"]), 70)
        self.assertEqual(state["practice_records"], [])
        self.assertEqual(len(state["medals"]), 234)
        self.assertTrue(all(item["status"] == "locked" for item in state["medals"]))

    def test_locked_practice_unlocks_each_reached_module_tier(self) -> None:
        state = state_store.default_state(TIMESTAMP)
        state["practice_records"].append(
            {
                "practice_id": "practice-data-1",
                "campaign_id": None,
                "season_id": None,
                "task_id": "task-data-1",
                "submission_ref": "submission-data-1",
                "date": "2026-08-31",
                "subject": "行测",
                "module": "资料分析",
                "ability_id": "data",
                "question_count": 10,
                "correct_count": 9,
                "accuracy_rate": 90,
                "duration_seconds": 700,
                "seconds_per_question": 70,
                "source": "锁定练习",
                "locked_before_start": True,
                "ruleset_version": "1.7.0",
                "record_type": "task_practice",
                "counts_for_ability": True,
            }
        )

        state_store.refresh_medals(state, TIMESTAMP)
        state_store.validate_state(state)

        unlocked = {
            item["medal_id"] for item in state["medals"] if item["status"] == "unlocked"
        }
        self.assertTrue(
            {
                "medal-data-starter",
                "medal-data-surge",
                "medal-data-elite",
            }.issubset(unlocked)
        )
        self.assertNotIn("medal-data-peak", unlocked)

    def test_practice_metrics_must_match_raw_counts_and_time(self) -> None:
        state = state_store.default_state(TIMESTAMP)
        state["practice_records"].append(
            {
                "practice_id": "practice-invalid",
                "campaign_id": None,
                "season_id": None,
                "task_id": "task-invalid",
                "submission_ref": "submission-invalid",
                "date": "2026-08-31",
                "subject": "行测",
                "module": "判断推理",
                "ability_id": "reasoning-logic",
                "question_count": 10,
                "correct_count": 8,
                "accuracy_rate": 90,
                "duration_seconds": 800,
                "seconds_per_question": 80,
                "source": "锁定练习",
                "locked_before_start": True,
                "ruleset_version": "1.7.0",
                "record_type": "task_practice",
                "counts_for_ability": True,
            }
        )

        with self.assertRaisesRegex(state_store.StateError, "正确率与题量不一致"):
            state_store.validate_state(state)

    def test_growth_uses_non_overlapping_windows_and_can_repeat(self) -> None:
        state = state_store.default_state(TIMESTAMP)
        accuracies = (50, 50, 65, 65, 55, 55, 70, 70)
        for index, accuracy in enumerate(accuracies, start=1):
            state["practice_records"].append(
                {
                    "practice_id": f"practice-growth-{index}",
                    "campaign_id": None,
                    "season_id": None,
                    "task_id": None,
                    "submission_ref": f"submission-growth-{index}",
                    "date": f"2026-08-{index:02d}",
                    "subject": "行测",
                    "module": "言语理解",
                    "ability_id": "verbal-reading",
                    "question_count": 20,
                    "correct_count": accuracy // 5,
                    "accuracy_rate": accuracy,
                    "duration_seconds": None,
                    "seconds_per_question": None,
                    "source": "自主练习",
                    "locked_before_start": False,
                    "ruleset_version": "1.7.0",
                    "record_type": "free_practice",
                    "counts_for_ability": True,
                }
            )

        state_store.refresh_medals(state, TIMESTAMP)
        state_store.validate_state(state)

        growth = {
            item["medal_id"]: item
            for item in state["medals"]
            if item["medal_id"].startswith("growth-verbal-reading-accuracy")
        }
        self.assertEqual(growth["growth-verbal-reading-accuracy-1"]["times_earned"], 2)
        self.assertEqual(growth["growth-verbal-reading-accuracy-2"]["times_earned"], 2)
        self.assertEqual(growth["growth-verbal-reading-accuracy-3"]["times_earned"], 2)

    def test_v15_migration_extracts_only_complete_practice_metrics(self) -> None:
        old = state_store.default_state(TIMESTAMP)
        old["schema_version"] = "1.5"
        old["engine"]["ruleset_version"] = "1.5.0"
        old["season"]["ruleset_version"] = "1.5.0"
        old.pop("practice_records")
        old["medals"] = [
            {
                "medal_id": "medal-reasoning-first",
                "name": "判断推理·初次上阵",
                "category": "模块",
                "description": "旧规则",
                "status": "unlocked",
                "condition": {"type": "module_usable", "target": 1},
                "progress_current": 1,
                "progress_target": 1,
                "progress_unit": "项",
                "evidence_refs": ["evidence-complete"],
                "unlocked_at": TIMESTAMP,
            }
        ]
        skill = old["catalog"][0]
        skill["evidence"] = [
            {
                "evidence_id": "evidence-complete",
                "campaign_id": None,
                "season_id": None,
                "task_id": "task-complete",
                "submission_ref": "submission-complete",
                "tested_at": "2026-08-30T08:00:00+08:00",
                "result": {
                    "question_count": 10,
                    "correct": 8,
                    "duration_seconds": 800,
                },
                "forms_supported": [],
            },
            {
                "evidence_id": "evidence-without-time",
                "campaign_id": None,
                "season_id": None,
                "task_id": "task-without-time",
                "submission_ref": "submission-without-time",
                "tested_at": "2026-08-30T09:00:00+08:00",
                "result": {"question_count": 10, "correct": 9},
                "forms_supported": [],
            },
        ]
        old["task_history"] = [
            {
                "task_id": "task-complete",
                "campaign_id": None,
                "season_id": None,
                "date": "2026-08-30",
                "status": "revealed",
                "locked_conditions": {
                    "source": "旧练习",
                    "ruleset_version": "1.5.0",
                },
                "submission_refs": ["submission-complete"],
                "verification": {},
                "reward_id": None,
            }
        ]

        migrated = state_store.migrate_state(old, TIMESTAMP)
        state_store.validate_state(migrated)

        self.assertEqual(len(migrated["practice_records"]), 2)
        self.assertNotIn(
            "medal-reasoning-first",
            {item["medal_id"] for item in migrated["medals"]},
        )
        unlocked = {
            item["medal_id"]
            for item in migrated["medals"]
            if item["status"] == "unlocked"
        }
        self.assertTrue({"medal-data-starter", "medal-data-surge"}.issubset(unlocked))
        report = migrated["engine"]["migration_history"][-1]["practice_records"]
        self.assertEqual(report["practice_records_extracted"], 2)
        self.assertEqual(report["skipped_missing_duration_seconds"], 1)
        self.assertEqual(report["accuracy_only_records"], 1)

    def test_v12_migration_adds_progression_collections(self) -> None:
        old = state_store.default_state(TIMESTAMP)
        old["schema_version"] = "1.2"
        old["engine"]["ruleset_version"] = "1.2.0"
        old["season"]["ruleset_version"] = "1.2.0"
        old["goal_contract"].pop("module_targets")
        old["goal_contract"].pop("subject_targets")
        for key in ("wrong_answers", "module_rankings", "subject_rankings", "medals"):
            old.pop(key)

        migrated = state_store.migrate_state(old, TIMESTAMP)

        state_store.validate_state(migrated)
        self.assertEqual(migrated["schema_version"], "1.7")
        self.assertEqual(migrated["engine"]["ruleset_version"], "1.7.0")
        self.assertEqual(migrated["season"]["ruleset_version"], "1.7.0")
        self.assertEqual(migrated["wrong_answers"], [])

    def test_rank_lines_must_be_ordered(self) -> None:
        state = state_store.default_state(TIMESTAMP)
        state["goal_contract"]["module_targets"] = [
            {
                "subject": "行测",
                "module": "资料分析",
                "metric": "accuracy",
                "total_points": 20,
                "floor_value": 80,
                "target_value": 75,
                "stretch_value": 90,
                "time_limit_minutes": 25,
            }
        ]

        with self.assertRaisesRegex(state_store.StateError, "保底线"):
            state_store.validate_state(state)

    def test_path_resolution_prefers_single_existing_legacy_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            legacy = home / ".hermes" / "data" / "gongkao-season-coach" / "state.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("{}", encoding="utf-8")

            resolved, source = state_store.resolve_state_path(
                home=home,
                environ={},
                system_name="linux",
            )

            self.assertEqual(resolved, legacy.resolve())
            self.assertEqual(source, "legacy-hermes")

    def test_path_resolution_rejects_two_master_states(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            hermes = home / ".hermes" / "data" / "gongkao-season-coach" / "state.json"
            codex = home / ".codex" / "data" / "gongkao-season-coach" / "state.json"
            for path in (hermes, codex):
                path.parent.mkdir(parents=True)
                path.write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(state_store.StateError, "多个候选主状态"):
                state_store.resolve_state_path(
                    home=home,
                    environ={},
                    system_name="linux",
                )

    def test_commit_is_atomic_idempotent_and_revision_guarded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            initialized = state_store.initialize_file(state_path, TIMESTAMP)
            candidate = state_store.read_current_state(state_path)
            candidate["profile"]["daily_minutes"] = 45

            committed = state_store.commit_candidate(
                state_path,
                candidate,
                expected_revision=0,
                event_id="profile:set-daily-minutes",
                timestamp=TIMESTAMP,
            )
            repeated = state_store.commit_candidate(
                state_path,
                candidate,
                expected_revision=0,
                event_id="profile:set-daily-minutes",
                timestamp=TIMESTAMP,
            )

            self.assertEqual(initialized["status"], "initialized")
            self.assertEqual(committed["state_revision"], 1)
            self.assertEqual(repeated["status"], "idempotent")
            self.assertEqual(repeated["state_revision"], 1)
            self.assertTrue((state_path.parent / "state.backup.json").exists())
            with self.assertRaises(state_store.RevisionConflict):
                state_store.commit_candidate(
                    state_path,
                    state_store.read_current_state(state_path),
                    expected_revision=0,
                    event_id="profile:stale-write",
                    timestamp=TIMESTAMP,
                )

    def test_commit_does_not_allow_permanent_history_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state_store.initialize_file(state_path, TIMESTAMP)
            first = state_store.read_current_state(state_path)
            first["assessments"].append(
                {
                    "assessment_id": "assessment-permanent",
                    "campaign_id": None,
                    "season_id": None,
                    "date": "2026-08-24",
                    "subject": "行测",
                    "scope": "历史基线",
                    "ranked": False,
                    "conditions": {},
                    "score": 63.5,
                    "score_max": 100,
                    "score_rate": 63.5,
                    "normalization_status": "exact",
                    "score_source": "official",
                    "evidence_refs": [],
                    "rank_delta": 0,
                    "ruleset_version": "1.5.0",
                }
            )
            state_store.commit_candidate(
                state_path,
                first,
                expected_revision=0,
                event_id="assessment:add-permanent",
                timestamp=TIMESTAMP,
            )
            second = state_store.read_current_state(state_path)
            second["assessments"] = []

            with self.assertRaisesRegex(state_store.StateError, "删除了永久历史"):
                state_store.commit_candidate(
                    state_path,
                    second,
                    expected_revision=1,
                    event_id="assessment:delete-permanent",
                    timestamp=TIMESTAMP,
                )

    def test_migration_preserves_facts_and_normalizes_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            old = state_store.default_state(TIMESTAMP)
            old["schema_version"] = "1.1"
            old["engine"]["ruleset_version"] = "1.1.0"
            old["season"]["ruleset_version"] = "1.1.0"
            old["engine"].pop("migration_history")
            old["profile"]["exam_type"] = "山东省考"
            old["campaign"].pop("campaign_id")
            old["campaign"].pop("status")
            old["campaign"].pop("completed_at")
            old["season"].pop("season_id")
            old["season"].pop("campaign_id")
            old.pop("campaign_history")
            old.pop("goal_contract_history")
            old["goal_contract"].pop("contract_id")
            old["goal_contract"].pop("campaign_id")
            old["attendance"]["records"] = [
                {"date": "2026-08-23", "status": "recovery"}
            ]
            state_path.write_text(
                json.dumps(old, ensure_ascii=False),
                encoding="utf-8",
            )

            result = state_store.migrate_file(state_path, TIMESTAMP)
            migrated = state_store.read_current_state(state_path)

            self.assertEqual(result["status"], "migrated")
            self.assertEqual(migrated["profile"]["exam_type"], "山东省考")
            self.assertTrue(migrated["campaign"]["campaign_id"])
            self.assertTrue(migrated["season"]["season_id"])
            self.assertTrue(migrated["attendance"]["records"][0]["counts_as_effective"])
            self.assertEqual(migrated["season"]["ruleset_version"], "1.7.0")

    def test_actual_v1_fixture_migrates_without_rejudging(self) -> None:
        old = json.loads((FIXTURES / "state-v1.0.json").read_text(encoding="utf-8"))

        migrated = state_store.migrate_state(old, TIMESTAMP)
        state_store.validate_state(migrated)

        self.assertEqual(migrated["schema_version"], "1.7")
        self.assertEqual(migrated["season"]["status"], "preseason")
        self.assertEqual(migrated["season"]["phase"], "calibration")
        self.assertEqual(migrated["season"]["ruleset_version"], "1.7.0")
        self.assertEqual(
            migrated["engine"]["migration_history"][0]["previous_season_ruleset"],
            "legacy-1.0",
        )
        self.assertFalse(
            migrated["engine"]["migration_history"][0]["historical_results_rejudged"]
        )

    def test_current_rules_do_not_accept_direct_rank_delta(self) -> None:
        state = state_store.default_state(TIMESTAMP)
        assessment = {
            "assessment_id": "assessment-1",
            "campaign_id": None,
            "season_id": None,
            "date": "2026-08-24",
            "subject": "申论",
            "scope": "完整任务",
            "ranked": True,
            "conditions": {},
            "score": 70,
            "score_max": 100,
            "score_rate": 70,
            "normalization_status": "exact",
            "rank_delta": 1,
            "score_source": "ai_internal",
            "evidence_refs": [],
            "ruleset_version": "1.7.0",
        }
        state["assessments"].append(assessment)

        with self.assertRaisesRegex(state_store.StateError, "rank_delta"):
            state_store.validate_state(state)

    def test_skill_recent_performance_is_validated(self) -> None:
        state = state_store.default_state(TIMESTAMP)
        skill = state["catalog"][0]
        skill.update(
            {
                "status": "discovered",
                "forms": {"base": True},
                "thresholds": {"正确率": "80%"},
                "recent_performance": {
                    "metric": "accuracy",
                    "value": 82.5,
                    "sample_count": 3,
                    "question_count": 42,
                    "window_label": "最近 3 次同口径练习",
                    "updated_at": TIMESTAMP,
                },
            }
        )
        state_store.validate_state(state)

        skill["recent_performance"]["value"] = 101
        with self.assertRaisesRegex(state_store.StateError, "value 必须位于"):
            state_store.validate_state(state)

    def test_default_catalog_has_locked_module_counts(self) -> None:
        state = state_store.default_state(TIMESTAMP)
        counts: dict[str, int] = {}
        for skill in state["catalog"]:
            counts[skill["module"]] = counts.get(skill["module"], 0) + 1

        self.assertEqual(
            counts,
            {
                "资料分析": 13,
                "数量关系": 12,
                "言语理解": 10,
                "判断推理": 15,
                "常识判断": 6,
                "申论": 14,
            },
        )

    def test_verified_shenlun_commit_writes_portfolio_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state_store.initialize_file(state_path, TIMESTAMP)
            candidate = state_store.read_current_state(state_path)
            candidate["task_history"].append(
                {
                    "task_id": "shenlun-1",
                    "campaign_id": None,
                    "season_id": None,
                    "date": "2026-08-30",
                    "status": "verified",
                    "locked_conditions": {
                        "subject": "申论",
                        "task_type": "归纳概括",
                        "prompt_ref": "prompt-1",
                        "ruleset_version": "1.5.0",
                    },
                    "submission_refs": ["submission-1"],
                    "verification": {
                        "task_result": "partial",
                        "answer_text": "这是我的申论答案。",
                        "score": 12,
                        "score_max": 20,
                        "score_source": "teacher",
                        "feedback": "分类还可再清楚。",
                        "word_count": 10,
                        "time_minutes": 18,
                        "dimensions": {"采点": 6},
                    },
                    "reward_id": None,
                }
            )

            state_store.commit_candidate(
                state_path,
                candidate,
                expected_revision=0,
                event_id="verify:shenlun-1",
                timestamp=TIMESTAMP,
            )
            saved = state_store.read_current_state(state_path)

            self.assertEqual(len(saved["shenlun_portfolio"]), 1)
            self.assertEqual(
                saved["shenlun_portfolio"][0]["portfolio_id"],
                "portfolio:submission-1",
            )
            self.assertEqual(
                saved["task_history"][0]["verification"]["portfolio_changes"],
                ["portfolio:submission-1"],
            )
            self.assertEqual(saved["shenlun_portfolio"][0]["score_rate"], 60)

    def test_verified_shenlun_without_answer_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state_store.initialize_file(state_path, TIMESTAMP)
            candidate = state_store.read_current_state(state_path)
            candidate["task_history"].append(
                {
                    "task_id": "shenlun-missing",
                    "campaign_id": None,
                    "season_id": None,
                    "date": "2026-08-30",
                    "status": "verified",
                    "locked_conditions": {
                        "subject": "申论",
                        "ruleset_version": "1.5.0",
                    },
                    "submission_refs": ["missing-answer"],
                    "verification": {"task_result": "fail"},
                    "reward_id": None,
                }
            )

            with self.assertRaisesRegex(state_store.StateError, "作答原文"):
                state_store.commit_candidate(
                    state_path,
                    candidate,
                    expected_revision=0,
                    event_id="verify:shenlun-missing",
                    timestamp=TIMESTAMP,
                )

    def test_new_season_preserves_ability_and_resets_rank(self) -> None:
        state = state_store.default_state(TIMESTAMP)
        state["campaign"]["campaign_id"] = "campaign-1"
        state["season"].update(
            {
                "season_id": "campaign-1:season-1",
                "campaign_id": "campaign-1",
                "status": "active",
                "rank": "钻石",
                "stars": 2,
                "highest_rank": "大师",
            }
        )
        state["catalog"][0]["status"] = "mastered"
        state["catalog"][0]["thresholds"] = {"accuracy": 80}
        state["catalog"][0]["forms"].update(
            {"timed": True, "mixed": True, "retained": True}
        )
        state["medals"][0].update(
            {"status": "unlocked", "unlocked_at": TIMESTAMP, "evidence_refs": ["x"]}
        )
        state["attendance"]["records"] = [
            {
                "date": "2026-08-29",
                "campaign_id": "campaign-1",
                "season_id": "campaign-1:season-1",
                "status": "effective",
                "counts_as_effective": True,
                "task_id": None,
                "submission_refs": [],
                "recorded_at": TIMESTAMP,
            }
        ]
        four_sims = next(
            item for item in state["medals"] if item["medal_id"] == "season-mocks"
        )
        four_sims["progress_current"] = 3

        next_state = state_store.start_new_season(
            state,
            start_date="2026-09-01",
            end_date="2026-09-28",
            theme="限时稳定",
            timestamp=TIMESTAMP,
        )

        self.assertEqual(next_state["season"]["rank"], "未定级")
        self.assertEqual(next_state["season"]["previous_rank"], "钻石")
        self.assertEqual(next_state["season"]["highest_rank"], "大师")
        self.assertEqual(next_state["catalog"][0]["status"], "mastered")
        self.assertEqual(next_state["medals"][0]["status"], "unlocked")
        self.assertEqual(
            next(
                item
                for item in next_state["medals"]
                if item["medal_id"] == "season-mocks"
            )["progress_current"],
            0,
        )
        self.assertEqual(
            next(
                item
                for item in next_state["medals"]
                if item["medal_id"] == "career-first-result"
            )["progress_current"],
            1,
        )
        self.assertEqual(len(next_state["season_history"]), 1)
        self.assertEqual(next_state["module_rankings"], [])
        self.assertEqual(next_state["subject_rankings"], [])

    def test_new_task_medal_targets_are_optional_but_must_be_known(self) -> None:
        state = state_store.default_state(TIMESTAMP)
        state["daily_quest"].update(
            {
                "status": "offered",
                "offer_id": "offer-1",
                "options": [{"offer_id": "offer-1", "task_id": "task-1"}],
            }
        )
        state_store.validate_state(state)

        state["daily_quest"]["options"][0]["medal_targets"] = ["unknown-medal"]
        with self.assertRaisesRegex(state_store.StateError, "medal_targets 无效"):
            state_store.validate_state(state)

    def test_v13_migration_replaces_legacy_medal_and_keeps_open_task(self) -> None:
        old = state_store.default_state(TIMESTAMP)
        old["schema_version"] = "1.3"
        old["engine"]["ruleset_version"] = "1.3.0"
        old["season"]["ruleset_version"] = "1.3.0"
        old["medals"].append(
            {
                "medal_id": "legacy-medal",
                "name": "旧赛季纪念",
                "description": "旧版已经获得",
                "status": "unlocked",
                "condition": {},
                "evidence_refs": ["legacy-evidence"],
                "unlocked_at": TIMESTAMP,
            }
        )
        old["daily_quest"].update(
            {
                "status": "offered",
                "offer_id": "old-offer",
                "options": [{"offer_id": "old-offer", "task_id": "old-task"}],
            }
        )

        migrated = state_store.migrate_state(old, TIMESTAMP)
        state_store.validate_state(migrated)

        self.assertNotIn(
            "legacy-medal", {item["medal_id"] for item in migrated["medals"]}
        )
        self.assertEqual(len(migrated["medals"]), 234)
        self.assertEqual(
            migrated["daily_quest"]["options"][0]["ruleset_version"],
            "1.3.0",
        )

    def test_v13_migration_downgrades_unverified_owned_skill_to_practicing(
        self,
    ) -> None:
        old = state_store.default_state(TIMESTAMP)
        old["schema_version"] = "1.3"
        old["engine"]["ruleset_version"] = "1.3.0"
        old["season"]["ruleset_version"] = "1.3.0"
        skill = next(item for item in old["catalog"] if item["name"] == "翻译推理")
        skill["status"] = "owned"
        skill["thresholds"] = {}
        skill.pop("legacy_status")

        migrated = state_store.migrate_state(old, TIMESTAMP)
        state_store.validate_state(migrated)

        migrated_skill = next(
            item for item in migrated["catalog"] if item["name"] == "翻译推理"
        )
        self.assertEqual(migrated_skill["status"], "discovered")
        self.assertEqual(migrated_skill["thresholds"], {})
        self.assertFalse(migrated_skill["legacy_status"])

    def test_v14_production_shape_is_normalized_without_losing_evidence(
        self,
    ) -> None:
        old = state_store.default_state(TIMESTAMP)
        old["schema_version"] = "1.4"
        old["engine"]["ruleset_version"] = "1.4.0"
        old["season"]["ruleset_version"] = "1.4.0"

        translation = next(item for item in old["catalog"] if item["id"] == "skill-45")
        translation.update(
            {
                "status": "owned",
                "legacy_status": True,
                "forms": {
                    "base": True,
                    "timed": False,
                    "mixed": False,
                    "retained": False,
                },
                "thresholds": {},
                "evidence": [
                    {
                        "evidence_id": "evidence-translation",
                        "campaign_id": "campaign-1",
                        "season_id": "season-1",
                        "task_id": "task-translation",
                        "submission_ref": "translation-15",
                        "tested_at": TIMESTAMP,
                        "result": {"total": 15, "correct": 10, "accuracy": 0.66},
                        "forms_supported": ["base"],
                    }
                ],
            }
        )

        custom_specs = (
            ("xingce-ziliao-abrx", "行测", "资料分析", "资料分析·ABRX基础", 80),
            (
                "xingce-judge-jiaqiang-xueruo",
                "行测",
                "判断推理",
                "判断推理·加强/削弱",
                70,
            ),
            (
                "xingce-yanyu-pianduan",
                "行测",
                "言语理解",
                "言语理解·片段阅读(中心理解)",
                60,
            ),
            ("shenlun-gaikuo", "申论", "归纳概括", "申论·归纳概括(单一题)", 86),
            ("shenlun-zonghe", "申论", "综合分析", "申论·综合分析(词句理解)", 83),
        )
        for skill_id, subject, module, name, score in custom_specs:
            result = (
                {"score": score, "dims": {"coverage": score}}
                if subject == "申论"
                else {"total": 10, "correct": score // 10, "accuracy": score / 100}
            )
            if skill_id == "xingce-judge-jiaqiang-xueruo":
                result["sub"] = {"加强": ["0.6"], "削弱": ["0.8"]}
            old["catalog"].append(
                {
                    "id": skill_id,
                    "subject": subject,
                    "module": module,
                    "name": name,
                    "tier": "custom",
                    "status": "owned",
                    "legacy_status": True,
                    "forms": {"base": True},
                    "thresholds": {"base": {"minimum": 60}},
                    "evidence": [
                        {
                            "evidence_id": f"evidence-{skill_id}",
                            "campaign_id": "campaign-1",
                            "season_id": "season-1",
                            "task_id": f"task-{skill_id}",
                            "submission_ref": f"submission-{skill_id}",
                            "tested_at": TIMESTAMP,
                            "result": result,
                            "forms_supported": ["base"],
                        }
                    ],
                    "last_tested_at": TIMESTAMP,
                    "next_review_at": None,
                    "needs_retest": False,
                }
            )

        old["assessments"] = [
            {
                "assessment_id": "assessment-legacy",
                "campaign_id": "campaign-1",
                "season_id": "season-1",
                "date": "2026-省考",
                "type": "free_exam",
                "source": "用户提供(2026年省考成绩)",
                "xingce": 63.5,
                "shenlun": 61.5,
                "total": 125,
                "grade": "未定级",
                "subject": None,
                "scope": None,
                "ranked": False,
                "conditions": {},
                "score": None,
                "score_source": None,
                "evidence_refs": [],
                "rank_delta": 0,
                "ruleset_version": "legacy-1.1",
            }
        ]
        old["shenlun_portfolio"] = [
            {
                "portfolio_id": f"portfolio-{score}",
                "campaign_id": "campaign-1",
                "season_id": "season-1",
                "date": "2026-08-28",
                "task_type": "申论单题",
                "prompt_ref": f"prompt-{score}",
                "submission_ref": f"submission-{score}",
                "score": score,
                "score_source": "ai_internal",
                "dimensions": {"coverage": score},
                "answer_text": None,
                "feedback": None,
                "word_count": None,
                "time_minutes": None,
            }
            for score in (86, 83)
        ]

        migrated = state_store.migrate_state(old, TIMESTAMP)
        state_store.validate_state(migrated)

        self.assertEqual(len(migrated["catalog"]), 70)
        practiced_ids = {
            item["id"] for item in migrated["catalog"] if item["status"] == "discovered"
        }
        self.assertEqual(
            practiced_ids,
            {
                "skill-02",
                "skill-30",
                "skill-45",
                "skill-47",
                "skill-48",
                "skill-59",
                "skill-60",
                "skill-61",
                "skill-62",
            },
        )
        self.assertTrue(
            all(not item.get("legacy_status") for item in migrated["catalog"])
        )
        skill_by_id = {item["id"]: item for item in migrated["catalog"]}
        self.assertEqual(skill_by_id["skill-47"]["recent_performance"]["value"], 60)
        self.assertEqual(skill_by_id["skill-48"]["recent_performance"]["value"], 80)
        self.assertEqual(
            [(item["subject"], item["score"]) for item in migrated["assessments"]],
            [("行测", 63.5), ("申论", 61.5)],
        )
        self.assertTrue(
            all(item["score_max"] == 100 for item in migrated["assessments"])
        )
        self.assertEqual(
            [item["score_rate"] for item in migrated["shenlun_portfolio"]],
            [86.0, 83.0],
        )
        report = migrated["engine"]["migration_history"][-1]["normalization"]
        self.assertEqual(len(report["skills"]["removed_custom_skill_ids"]), 5)
        self.assertEqual(
            report["assessments"]["split_assessment_ids"], ["assessment-legacy"]
        )

    def test_migrate_dry_run_does_not_write_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            old = state_store.default_state(TIMESTAMP)
            old["schema_version"] = "1.4"
            old["engine"]["ruleset_version"] = "1.4.0"
            old["season"]["ruleset_version"] = "1.4.0"
            original = json.dumps(old, ensure_ascii=False)
            state_path.write_text(original, encoding="utf-8")

            result = state_store.migrate_file(
                state_path,
                TIMESTAMP,
                dry_run=True,
            )

            self.assertEqual(result["status"], "dry-run")
            self.assertEqual(result["schema_version"], "1.7")
            self.assertEqual(result["counts"]["skills"], 70)
            self.assertEqual(state_path.read_text(encoding="utf-8"), original)
            self.assertFalse((state_path.parent / "state.backup.json").exists())

    def test_v14_unknown_historical_score_keeps_raw_value_for_review(self) -> None:
        old = state_store.default_state(TIMESTAMP)
        old["schema_version"] = "1.4"
        old["engine"]["ruleset_version"] = "1.4.0"
        old["season"]["ruleset_version"] = "1.4.0"
        old["assessments"].append(
            {
                "assessment_id": "assessment-unknown-scale",
                "campaign_id": None,
                "season_id": None,
                "date": "2026-08-20",
                "subject": "申论",
                "scope": "历史记录",
                "ranked": False,
                "conditions": {},
                "score": 12,
                "score_source": "teacher",
                "evidence_refs": [],
                "rank_delta": 0,
                "ruleset_version": "legacy-1.3",
            }
        )

        migrated = state_store.migrate_state(old, TIMESTAMP)
        item = migrated["assessments"][0]

        self.assertEqual(item["score"], 12)
        self.assertIsNone(item["score_max"])
        self.assertIsNone(item["score_rate"])
        self.assertEqual(item["normalization_status"], "needs_review")

    def test_recovery_keeps_corrupt_copy_and_restores_valid_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state_store.initialize_file(state_path, TIMESTAMP)
            candidate = state_store.read_current_state(state_path)
            candidate["profile"]["daily_minutes"] = 30
            state_store.commit_candidate(
                state_path,
                candidate,
                expected_revision=0,
                event_id="profile:set-30",
                timestamp=TIMESTAMP,
            )
            state_path.write_text("{broken", encoding="utf-8")

            result = state_store.recover_from_backup(
                state_path,
                event_id="recover:test",
                timestamp=TIMESTAMP,
            )
            recovered = state_store.read_current_state(state_path)

            self.assertEqual(result["status"], "recovered")
            self.assertIn("recover:test", recovered["engine"]["processed_event_ids"])
            self.assertEqual(recovered["engine"]["state_revision"], 1)
            corrupt_copies = list(state_path.parent.glob("state.corrupt.*.json"))
            self.assertEqual(len(corrupt_copies), 1)

    def test_history_preservation_does_not_mutate_input(self) -> None:
        state = state_store.default_state(TIMESTAMP)
        original = copy.deepcopy(state)

        state_store.validate_state(state)

        self.assertEqual(state, original)


if __name__ == "__main__":
    unittest.main()
