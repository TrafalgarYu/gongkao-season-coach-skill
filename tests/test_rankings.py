"""
版本记录：
- v1.1.0 / 2026-08-30
  - 为排位样本补充原始满分、得分率和 1.5 评分口径。
- v1.0.0 / 2026-08-30
  - 验证新赛季排位样本门槛和旧赛季战绩隔离。
"""

from __future__ import annotations

import unittest

from scripts import rankings, state_store


class RankingEngineTests(unittest.TestCase):
    def _assessment(
        self,
        assessment_id: str,
        subject: str,
        score: float,
        conditions: dict[str, object],
        *,
        season_id: str = "season-2",
        source: str = "official",
    ) -> dict[str, object]:
        return {
            "assessment_id": assessment_id,
            "campaign_id": "campaign-1",
            "season_id": season_id,
            "date": "2026-09-02",
            "subject": subject,
            "scope": "定级",
            "ranked": True,
            "conditions": conditions,
            "score": score,
            "score_max": 100,
            "score_rate": score,
            "normalization_status": "exact",
            "score_source": source,
            "evidence_refs": [],
            "rank_delta": 0,
            "ruleset_version": "1.5.0",
        }

    def test_current_season_placement_ignores_old_results(self) -> None:
        state = state_store.default_state("2026-08-30T00:00:00+00:00")
        state["campaign"]["campaign_id"] = "campaign-1"
        state["season"].update(
            {"campaign_id": "campaign-1", "season_id": "season-2", "status": "active"}
        )
        state["goal_contract"]["subject_targets"] = [
            {
                "subject": "行测",
                "metric": "score",
                "floor_value": 60,
                "target_value": 75,
                "stretch_value": 85,
            },
            {
                "subject": "申论",
                "metric": "score",
                "floor_value": 55,
                "target_value": 70,
                "stretch_value": 80,
            },
        ]
        state["assessments"] = [
            self._assessment(
                "old-x",
                "行测",
                99,
                {"full_paper": True, "timed": True},
                season_id="season-1",
            ),
            self._assessment("x-1", "行测", 72, {"full_paper": True, "timed": True}),
            self._assessment("x-2", "行测", 76, {"full_paper": True, "timed": True}),
            self._assessment("s-1", "申论", 68, {"complete_answer": True}),
            self._assessment(
                "s-2", "申论", 72, {"complete_answer": True}, source="teacher"
            ),
        ]

        rankings.refresh_rankings(state, "2026-09-02T00:00:00+00:00")

        self.assertEqual(state["season"]["placement_progress"]["xingce_current"], 2)
        self.assertEqual(state["season"]["placement_progress"]["shenlun_current"], 2)
        self.assertNotEqual(state["season"]["rank"], "未定级")
        xingce = next(
            item for item in state["subject_rankings"] if item["subject"] == "行测"
        )
        self.assertNotIn("old-x", xingce["assessment_refs"])

    def test_module_requires_three_tests_and_thirty_questions(self) -> None:
        state = state_store.default_state("2026-08-30T00:00:00+00:00")
        state["season"]["season_id"] = "season-2"
        state["goal_contract"]["module_targets"] = [
            {
                "subject": "行测",
                "module": "资料分析",
                "metric": "accuracy",
                "floor_value": 70,
                "target_value": 80,
                "stretch_value": 90,
            }
        ]
        state["assessments"] = [
            self._assessment(
                f"m-{index}",
                "行测",
                80 + index,
                {"module": "资料分析", "timed": True, "question_count": 10},
            )
            for index in range(3)
        ]

        rankings.refresh_rankings(state)

        self.assertNotEqual(state["module_rankings"][0]["rank"], "未定级")


if __name__ == "__main__":
    unittest.main()
