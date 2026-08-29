"""
版本记录：
- v1.0.0 / 2026-08-29
  - 验证未定级、六段位边界、三星进度和无效阈值。
"""

from __future__ import annotations

import unittest

from scripts import progression


class ProgressionTests(unittest.TestCase):
    def test_insufficient_evidence_stays_unranked(self) -> None:
        result = progression.classify_rank(
            85,
            floor_value=65,
            target_value=80,
            stretch_value=90,
            qualified=False,
        )

        self.assertEqual(result["rank"], "未定级")
        self.assertEqual(result["stars"], 0)

    def test_rank_boundaries_follow_three_locked_lines(self) -> None:
        expected = {
            54: "青铜",
            55: "白银",
            65: "黄金",
            72.5: "钻石",
            80: "大师",
            90: "王者",
        }

        for value, rank in expected.items():
            with self.subTest(value=value):
                result = progression.classify_rank(
                    value,
                    floor_value=65,
                    target_value=80,
                    stretch_value=90,
                    qualified=True,
                )
                self.assertEqual(result["rank"], rank)
                self.assertIn(result["stars"], {1, 2, 3})

    def test_invalid_lines_are_rejected(self) -> None:
        with self.assertRaisesRegex(progression.ProgressionError, "保底线"):
            progression.rank_bands(80, 75, 90)


if __name__ == "__main__":
    unittest.main()
