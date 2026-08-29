"""
版本记录：
- v1.0.0 / 2026-08-29
  - 根据保底线、目标线、冲刺线计算六段位与三星进度。
  - 数据不足时返回未定级，避免单次成绩直接晋级。

用途：为模块、行测、申论和综合段位提供统一的确定性计算。
"""

from __future__ import annotations

import argparse
import json
from typing import Any

RANK_ORDER = ("青铜", "白银", "黄金", "钻石", "大师", "王者")


class ProgressionError(ValueError):
    """段位参数缺失或相互冲突。"""


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProgressionError(f"{label} 必须是数字。")
    return float(value)


def rank_bands(
    floor_value: float,
    target_value: float,
    stretch_value: float,
) -> tuple[tuple[str, float, float], ...]:
    """返回从青铜到王者的连续分段。"""
    floor = _number(floor_value, "保底线")
    target = _number(target_value, "目标线")
    stretch = _number(stretch_value, "冲刺线")
    if not 0 <= floor < target < stretch <= 100:
        raise ProgressionError("必须满足 0 ≤ 保底线 < 目标线 < 冲刺线 ≤ 100。")

    silver_floor = max(0.0, floor - 10.0)
    midpoint = (floor + target) / 2
    return (
        ("青铜", 0.0, silver_floor),
        ("白银", silver_floor, floor),
        ("黄金", floor, midpoint),
        ("钻石", midpoint, target),
        ("大师", target, stretch),
        ("王者", stretch, 100.0),
    )


def classify_rank(
    stable_value: float | None,
    *,
    floor_value: float,
    target_value: float,
    stretch_value: float,
    qualified: bool,
) -> dict[str, Any]:
    """按稳定成绩定级；qualified=False 时不授予段位。"""
    bands = rank_bands(floor_value, target_value, stretch_value)
    if stable_value is None or not qualified:
        return {
            "rank": "未定级",
            "stars": 0,
            "stable_value": stable_value,
            "next_rank": "青铜",
            "gap_to_next": None,
        }

    value = _number(stable_value, "稳定成绩")
    if not 0 <= value <= 100:
        raise ProgressionError("稳定成绩必须位于 0 至 100。")

    selected = bands[-1]
    for band in bands:
        if value < band[2] or band[0] == "王者":
            selected = band
            break

    rank, lower, upper = selected
    width = upper - lower
    progress = 1.0 if width == 0 else min(1.0, max(0.0, (value - lower) / width))
    stars = min(3, int(progress * 3) + 1)
    rank_index = RANK_ORDER.index(rank)
    next_rank = RANK_ORDER[rank_index + 1] if rank_index < len(RANK_ORDER) - 1 else None
    gap = round(max(0.0, upper - value), 2) if next_rank else 0.0
    return {
        "rank": rank,
        "stars": stars,
        "stable_value": value,
        "next_rank": next_rank,
        "gap_to_next": gap,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="计算公考模块或科目段位")
    parser.add_argument("--stable-value", type=float)
    parser.add_argument("--floor", type=float, required=True, help="保底线")
    parser.add_argument("--target", type=float, required=True, help="目标线")
    parser.add_argument("--stretch", type=float, required=True, help="冲刺线")
    parser.add_argument(
        "--qualified",
        action="store_true",
        help="有效样本已满足定级要求",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = classify_rank(
        args.stable_value,
        floor_value=args.floor,
        target_value=args.target,
        stretch_value=args.stretch,
        qualified=args.qualified,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
