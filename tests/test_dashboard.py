"""
版本记录：
- v4.0.0 / 2026-08-31
  - 验证量化技能页、练习战绩和 40 枚新勋章目录。
- v3.2.0 / 2026-08-31
  - 验证总览每天 08:00 更新一次，普通访问不重建，手动刷新立即生效。
- v3.1.0 / 2026-08-30
  - 验证历史考试基线与 AI 内部单题评分使用明确的满分和中文来源。
- v3.0.0 / 2026-08-30
  - 验证两排总览、70 项技能、27 枚勋章和赛季定级信息。

- v2.1.0 / 2026-08-30
  - 验证技能实测指标、样本依据和只读 HTTP 服务的按需刷新与无缓存响应。

- v2.0.0 / 2026-08-29
  - 验证六栏目、技能熟练度、分层段位和 HTML 转义。
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from scripts import dashboard, state_store


class DashboardTests(unittest.TestCase):
    def test_dashboard_shows_six_sections_and_skill_progress(self) -> None:
        state = state_store.default_state("2026-08-29T00:00:00+00:00")
        first, second = state["catalog"][:2]
        state["season"]["locked_catalog_ids"] = [first["id"]]
        first.update(
            {
                "subject": "行测",
                "module": "资料分析",
                "name": "基期量",
                "status": "discovered",
                "forms": {"base": True, "timed": False},
                "thresholds": {"正确率": "80%"},
                "recent_performance": {
                    "metric": "accuracy",
                    "value": 82.5,
                    "sample_count": 3,
                    "question_count": 42,
                    "window_label": "最近 3 次同口径练习",
                    "updated_at": "2026-08-30T08:00:00+08:00",
                },
                "evidence": [],
                "last_tested_at": None,
                "next_review_at": None,
            }
        )
        second.update(
            {
                "subject": "申论",
                "module": "归纳概括",
                "name": "分类归纳",
                "status": "owned",
                "forms": {"base": True},
                "thresholds": {},
                "evidence": [],
                "last_tested_at": None,
                "next_review_at": None,
            }
        )

        state["subject_rankings"] = [
            {
                "ranking_id": "rank-xingce",
                "campaign_id": None,
                "season_id": None,
                "subject": "行测",
                "metric": "score",
                "stable_value": 72,
                "rank": "钻石",
                "stars": 2,
                "next_rank": "大师",
                "gap_to_next": 3,
                "sample_size": 3,
                "assessment_refs": [],
                "updated_at": None,
            }
        ]
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
                "question_count": 10,
                "correct_count": 9,
                "accuracy_rate": 90,
                "duration_seconds": 700,
                "seconds_per_question": 70,
                "source": "测试练习",
                "locked_before_start": True,
                "ruleset_version": "1.6.0",
            }
        )

        report = dashboard.render_html(
            state,
            source_path=Path("state.json"),
        )

        for section in ("技能总览", "错题本", "易错点", "战绩", "申论答题册", "勋章墙"):
            self.assertIn(section, report)
        self.assertIn("有实测", report)
        self.assertIn("待记录", report)
        self.assertIn("最近正确率", report)
        self.assertIn("82.5%", report)
        self.assertIn("最近 3 次同口径练习 · 共 42 题", report)
        self.assertIn("最近得分率", report)
        self.assertIn("实测数据不足", report)
        self.assertNotIn("熟练度检查", report)
        self.assertIn('data-current="true"', report)
        self.assertIn("技能总数", report)
        self.assertIn("全部 70", report)
        self.assertIn("待记录 69", report)
        self.assertIn("全部 40", report)
        self.assertIn("数据口径", report)
        self.assertIn("练习战绩", report)
        self.assertIn("9/10", report)
        self.assertIn("70 秒", report)
        self.assertIn("资料分析·百里挑一", report)
        self.assertIn("上赛季", report)
        self.assertIn("历史最高", report)
        self.assertIn("行测 0/2", report)
        self.assertIn("钻石 ★★☆", report)

    def test_dashboard_distinguishes_exam_baseline_and_single_question(self) -> None:
        state = state_store.default_state("2026-08-30T00:00:00+00:00")
        state["assessments"].append(
            {
                "assessment_id": "baseline-xingce",
                "campaign_id": None,
                "season_id": None,
                "date": None,
                "subject": "行测",
                "scope": "历史考试基线",
                "ranked": False,
                "conditions": {"exam_label": "2026-省考"},
                "score": 63.5,
                "score_max": 100,
                "score_rate": 63.5,
                "normalization_status": "exact",
                "score_source": "official",
                "evidence_refs": [],
                "rank_delta": 0,
                "ruleset_version": "legacy-1.1",
            }
        )
        state["shenlun_portfolio"].append(
            {
                "portfolio_id": "portfolio-1",
                "campaign_id": None,
                "season_id": None,
                "date": "2026-08-28",
                "task_type": "归纳概括-单题",
                "prompt_ref": "风林村变化",
                "submission_ref": "submission-1",
                "score": 86,
                "score_max": 100,
                "score_rate": 86,
                "normalization_status": "exact",
                "score_source": "ai_internal",
                "dimensions": {"覆盖": 26},
                "answer_text": None,
                "feedback": None,
                "word_count": None,
                "time_minutes": None,
            }
        )

        report = dashboard.render_html(state, source_path=Path("state.json"))

        self.assertIn("2026-省考", report)
        self.assertIn("63.5/100", report)
        self.assertIn("正式考试", report)
        self.assertIn("AI内部单题评分：86/100", report)
        self.assertIn("未保存原文", report)

    def test_build_report_escapes_user_content(self) -> None:
        state = state_store.default_state("2026-08-29T00:00:00+00:00")
        state["catalog"][0]["name"] = "<script>alert(1)</script>"
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            output_path = Path(temp_dir) / "dashboard.html"
            state_path.write_text("{}", encoding="utf-8")
            output_path.write_text(
                dashboard.render_html(state, source_path=state_path),
                encoding="utf-8",
            )

            report = output_path.read_text(encoding="utf-8")

        self.assertNotIn("<script>alert(1)</script>", report)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", report)

    def test_server_refreshes_daily_at_eight_and_on_manual_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            output_path = Path(temp_dir) / "dashboard.html"
            state = state_store.default_state("2026-08-30T08:00:00+08:00")
            state_path.write_text(
                json.dumps(state, ensure_ascii=False),
                encoding="utf-8",
            )
            current_time = [datetime.fromisoformat("2026-08-31T07:59:00+08:00")]
            server = dashboard.create_server(
                state_path,
                output_path,
                "127.0.0.1",
                0,
                now_provider=lambda: current_time[0],
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]

            try:
                with urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
                    first = response.read().decode("utf-8")
                    self.assertEqual(
                        response.headers["Cache-Control"],
                        "no-store, max-age=0",
                    )
                self.assertIn("2026-08-30T08:00:00+08:00", first)
                self.assertNotIn('http-equiv="refresh"', first)
                self.assertIn('id="manual-refresh"', first)

                state["engine"]["updated_at"] = "2026-08-30T09:30:00+08:00"
                state_path.write_text(
                    json.dumps(state, ensure_ascii=False),
                    encoding="utf-8",
                )
                with urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
                    before_eight = response.read().decode("utf-8")
                self.assertNotIn("2026-08-30T09:30:00+08:00", before_eight)

                current_time[0] = datetime.fromisoformat("2026-08-31T08:00:00+08:00")
                with urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
                    daily = response.read().decode("utf-8")
                self.assertIn("2026-08-30T09:30:00+08:00", daily)

                state["engine"]["updated_at"] = "2026-08-30T10:15:00+08:00"
                state_path.write_text(
                    json.dumps(state, ensure_ascii=False),
                    encoding="utf-8",
                )
                with urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
                    same_day = response.read().decode("utf-8")
                self.assertNotIn("2026-08-30T10:15:00+08:00", same_day)

                with urlopen(
                    f"http://127.0.0.1:{port}/?refresh=1", timeout=2
                ) as response:
                    manual = response.read().decode("utf-8")
                self.assertIn("2026-08-30T10:15:00+08:00", manual)
                with self.assertRaises(HTTPError) as error:
                    urlopen(f"http://127.0.0.1:{port}/state.json", timeout=2)
                self.assertEqual(error.exception.code, 404)
            finally:
                server.shutdown()
                thread.join(timeout=2)
                server.server_close()


if __name__ == "__main__":
    unittest.main()
