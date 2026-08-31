"""
版本记录：
- v1.7.0 / 2026-08-31
  - 建立单次战绩、实力勋章、成长成就、生涯成就和赛季成就五类目录。
  - 行测练习按 10 个能力项归类，申论单列为第 11 项；正确率或得分率与速度分开结算。
  - 长期实力采用五档永久勋章，成长成就使用互不重叠窗口，可重复累计次数。
- v1.6.0 / 2026-08-31
  - 用五个行测模块的 25 枚正确率与用时勋章替换模糊的“考场可用”勋章。
  - 新增练习战绩判定，并把固定勋章目录调整为 40 枚。
  - 迁移时只按可核验事实重建勋章，不保留重复的旧勋章目录。
- v1.4.1 / 2026-08-30
  - 修复旧版已掌握技能合并固定目录时未获得 legacy_status 豁免的问题。
  - 保留旧技能的掌握事实，并避免空门槛记录阻断 1.4 状态迁移。
- v1.4.0 / 2026-08-30
  - 建立 70 项标准技能与 27 枚固定勋章目录。
  - 提供目录合并和勋章进度的确定性计算，供状态迁移与 HTML 共用。

用途：集中维护不会因用户状态变化而改变的技能和勋章定义。
"""

from __future__ import annotations

import copy
from collections import Counter
from typing import Any

SKILL_GROUPS = {
    ("行测", "资料分析"): [
        "数据定位与口径识别",
        "ABRX关系识别",
        "基期量",
        "现期量",
        "增长量",
        "增长率",
        "增长率比较",
        "现期比重",
        "基期比重",
        "比重变化",
        "平均数与平均数增长",
        "倍数与年均增长",
        "贡献率与拉动增长",
    ],
    ("行测", "数量关系"): [
        "工程问题",
        "行程问题",
        "利润问题",
        "排列组合",
        "概率问题",
        "容斥问题",
        "最值问题",
        "年龄问题",
        "几何问题",
        "浓度问题",
        "数列与数字特性",
        "方程与比例",
    ],
    ("行测", "言语理解"): [
        "逻辑填空语境分析",
        "词义辨析",
        "固定搭配",
        "成语辨析",
        "中心理解",
        "细节判断",
        "意图判断",
        "标题选择",
        "语句排序",
        "语句衔接与填空",
    ],
    ("行测", "判断推理"): [
        "图形位置规律",
        "图形样式规律",
        "图形数量规律",
        "图形属性规律",
        "空间重构",
        "定义判断要素",
        "类比语义关系",
        "类比逻辑关系",
        "类比语法关系",
        "翻译推理",
        "真假推理",
        "加强论证",
        "削弱论证",
        "前提假设",
        "解释与评价",
    ],
    ("行测", "常识判断"): [
        "政治常识",
        "法律常识",
        "经济常识",
        "历史人文",
        "科技生活",
        "地理国情",
    ],
    ("申论", "申论"): [
        "题干拆解",
        "材料标记与段落主旨",
        "采点",
        "同义归并与分类",
        "概括表达与限字",
        "综合分析对象拆解",
        "原因影响关系分析",
        "对策提炼与可行性",
        "公文格式",
        "公文对象与语气",
        "公文内容结构",
        "大作文立意",
        "分论点结构",
        "论证与材料联系",
    ],
}

XINGCE_MODULE_IDS = {
    "资料分析": "data",
    "数量关系": "math",
    "言语理解": "verbal",
    "判断推理": "reasoning",
    "常识判断": "knowledge",
}

SINGLE_PERFORMANCE_TIERS = (
    ("starter", "崭露头角", 70, 90),
    ("surge", "势如破竹", 80, 80),
    ("elite", "百里挑一", 90, 70),
    ("peak", "登峰造极", 95, 60),
    ("peerless", "天下无双", 100, 55),
)
MIN_MEDAL_QUESTIONS = 10

TIER_NAMES = ("起步", "稳定", "熟练", "精准", "极境")
ACCURACY_CUTS = (60, 70, 80, 90, 95)
SHENLUN_SCORE_CUTS = (60, 65, 70, 75, 80)
ABILITY_SPECS = (
    ("data", "资料分析", "整体模块", (120, 105, 90, 75, 60), 30),
    ("math", "数量关系", "整体模块", (120, 105, 90, 75, 60), 30),
    ("knowledge", "常识判断", "整体模块", (45, 40, 35, 30, 25), 30),
    ("verbal-fill", "逻辑填空", "言语理解", (55, 50, 45, 40, 35), 20),
    ("verbal-reading", "片段阅读", "言语理解", (75, 68, 62, 58, 55), 20),
    ("verbal-sentence", "语句表达", "言语理解", (65, 60, 55, 50, 45), 20),
    ("reasoning-graphic", "图形推理", "判断推理", (60, 55, 50, 45, 40), 20),
    ("reasoning-definition", "定义判断", "判断推理", (60, 55, 50, 45, 40), 20),
    ("reasoning-analogy", "类比推理", "判断推理", (45, 40, 35, 30, 25), 20),
    ("reasoning-logic", "逻辑判断", "判断推理", (90, 80, 70, 60, 55), 20),
    ("shenlun", "申论作答", "申论", (35, 32, 29, 26, 23), 3),
)
ABILITY_BY_ID = {item[0]: item for item in ABILITY_SPECS}


def infer_ability_id(module: str | None, skill_name: str | None = None) -> str | None:
    """把固定模块或技能名归入 11 个能力项。"""
    if module == "资料分析":
        return "data"
    if module == "数量关系":
        return "math"
    if module == "常识判断":
        return "knowledge"
    if module == "申论":
        return "shenlun"
    name = skill_name or ""
    if module == "言语理解":
        if any(word in name for word in ("填空", "词义", "搭配", "成语")):
            return "verbal-fill"
        if any(word in name for word in ("排序", "衔接", "语句")):
            return "verbal-sentence"
        return "verbal-reading"
    if module == "判断推理":
        if "图形" in name or "空间" in name:
            return "reasoning-graphic"
        if "定义" in name:
            return "reasoning-definition"
        if "类比" in name:
            return "reasoning-analogy"
        return "reasoning-logic"
    return None


def _slug(index: int) -> str:
    return f"skill-{index:02d}"


def default_skills() -> list[dict[str, Any]]:
    skills: list[dict[str, Any]] = []
    index = 1
    for (subject, module), names in SKILL_GROUPS.items():
        for name in names:
            forms = (
                {
                    "base": False,
                    "structure": False,
                    "compressed": False,
                    "transfer": False,
                    "retained": False,
                }
                if subject == "申论"
                else {"base": False, "timed": False, "mixed": False, "retained": False}
            )
            skills.append(
                {
                    "id": _slug(index),
                    "subject": subject,
                    "module": module,
                    "name": name,
                    "tier": "standard",
                    "status": "silhouette",
                    "forms": forms,
                    "thresholds": {},
                    "evidence": [],
                    "last_tested_at": None,
                    "next_review_at": None,
                    "needs_retest": False,
                    "legacy_status": False,
                }
            )
            index += 1
    return skills


def _medal(
    medal_id: str,
    name: str,
    category: str,
    description: str,
    condition_type: str,
    target: int,
    *,
    scope: str = "career",
    module: str | None = None,
    condition_extra: dict[str, Any] | None = None,
    progress_unit: str = "项",
    repeatable: bool = False,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    condition = {"type": condition_type, "target": target, "scope": scope}
    if module:
        condition["module"] = module
    if condition_extra:
        condition.update(condition_extra)
    item = {
        "medal_id": medal_id,
        "name": name,
        "category": category,
        "description": description,
        "status": "locked",
        "condition": condition,
        "progress_current": 0,
        "progress_target": target,
        "progress_unit": progress_unit,
        "evidence_refs": [],
        "unlocked_at": None,
        "repeatable": repeatable,
        "times_earned": 0,
    }
    if metadata:
        item.update(metadata)
    return item


def default_medals() -> list[dict[str, Any]]:
    medals: list[dict[str, Any]] = []
    # 单次战绩保留原五模块、五档体系，任意一次提交即可触发并累计次数。
    for module, key in XINGCE_MODULE_IDS.items():
        for tier_id, tier_name, accuracy_min, seconds_max in SINGLE_PERFORMANCE_TIERS:
            medals.append(
                _medal(
                    f"medal-{key}-{tier_id}",
                    f"{module}·{tier_name}",
                    "单次战绩",
                    (
                        f"单次至少 {MIN_MEDAL_QUESTIONS} 题，正确率不低于 "
                        f"{accuracy_min}%，平均每题不超过 {seconds_max} 秒"
                    ),
                    "module_performance",
                    1,
                    module=module,
                    condition_extra={
                        "accuracy_min": accuracy_min,
                        "seconds_per_question_max": seconds_max,
                        "question_count_min": MIN_MEDAL_QUESTIONS,
                        "tier": tier_id,
                    },
                    progress_unit="次达标",
                    repeatable=True,
                    metadata={"tier": tier_id, "module": module},
                )
            )

    # 实力勋章按 11 项能力、正确率或得分率与速度两条战线分别设置五档。
    for ability_id, name, group, time_cuts, window in ABILITY_SPECS:
        score_cuts = SHENLUN_SCORE_CUTS if ability_id == "shenlun" else ACCURACY_CUTS
        for metric, cuts, unit in (
            ("score" if ability_id == "shenlun" else "accuracy", score_cuts, "%"),
            ("speed", time_cuts, "分钟/题" if ability_id == "shenlun" else "秒/题"),
        ):
            for level, (tier_name, cut) in enumerate(zip(TIER_NAMES, cuts), start=1):
                direction = "不低于" if metric != "speed" else "不超过"
                medals.append(
                    _medal(
                        f"strength-{ability_id}-{metric}-{level}",
                        f"{name}·{tier_name}",
                        "实力勋章",
                        f"{name}{'评分' if metric == 'score' else '正确率' if metric == 'accuracy' else '平均用时'}{direction}{cut}{unit}",
                        "strength_level",
                        1,
                        condition_extra={"ability_id": ability_id, "metric": metric, "cut": cut, "level": level, "sample_min": window},
                        metadata={"ability_id": ability_id, "ability_name": name, "ability_group": group, "metric": metric, "level": level},
                    )
                )

    # 成长成就使用两段互不重叠窗口，可在不同历史区间反复获得。
    growth_specs = {
        "accuracy": ((5, "初见起色"), (10, "明显突破"), (15, "弱项逆转")),
        "speed": ((10, "开始提速"), (20, "节奏突破"), (30, "疾速进阶")),
    }
    for ability_id, name, _group, _time_cuts, window in ABILITY_SPECS:
        for metric, tiers in growth_specs.items():
            for level, (cut, tier_name) in enumerate(tiers, start=1):
                medals.append(
                    _medal(
                        f"growth-{ability_id}-{metric}-{level}",
                        f"{name}·{tier_name}",
                        "成长成就",
                        f"两个互不重叠的有效窗口中，{('得分率提升' if ability_id == 'shenlun' else '正确率提升') if metric == 'accuracy' else '平均用时降低'}至少 {cut}{'个百分点' if metric == 'accuracy' else '%'}",
                        "growth_window",
                        1,
                        condition_extra={"ability_id": ability_id, "metric": metric, "cut": cut, "window": window, "level": level},
                        progress_unit="次",
                        repeatable=True,
                        metadata={"ability_id": ability_id, "ability_name": name, "metric": metric, "level": level},
                    )
                )

    repeatables = (
        ("career-hundred-questions", "百题行者", "practice_questions", 100, "题"),
        ("career-ten-wrongs", "错题十清", "resolved_wrongs", 10, "题"),
        ("career-ten-essays", "申论十篇", "shenlun_answers", 10, "篇"),
        ("career-attendance-week", "有效出勤周", "effective_days", 6, "天"),
        ("career-five-mocks", "模考征程", "full_simulations_all", 5, "次"),
        ("career-ten-retests", "复测闭环", "resolved_retests", 10, "题"),
    )
    for medal_id, name, kind, target, unit in repeatables:
        medals.append(_medal(medal_id, name, "生涯成就", f"每累计 {target}{unit}增加 1 星", kind, target, progress_unit=unit, repeatable=True, metadata={"career_kind": "repeatable"}))

    milestones = (
        ("first-result", "第一份战果", "effective_days", 1), ("first-task", "任务开张", "completed_tasks", 1),
        ("first-free-practice", "自主出击", "free_practices", 1), ("first-correction", "第一次订正", "resolved_wrongs", 1),
        ("first-retest", "延迟复测", "completed_retests", 1), ("first-counter", "反制形成", "countered_errors", 1),
        ("first-seal", "弱点封印", "sealed_errors", 1), ("first-essay", "申论落笔", "shenlun_answers", 1),
        ("first-mock", "第一次全卷", "full_simulations_all", 1), ("first-ranked", "首次排位", "ranked_assessments", 1),
        ("first-record", "刷新纪录", "personal_records", 1), ("first-strength", "第一枚勋章", "strength_unlocked", 1),
        ("first-growth", "初见起色", "growth_earned", 1), ("five-modules", "五域留痕", "measured_modules", 5),
        ("eleven-abilities", "十一项建档", "measured_abilities", 11), ("first-skill-module", "模块全解锁", "complete_skill_modules", 1),
        ("all-xingce-skills", "行测技能贯通", "xingce_skills", 56), ("all-shenlun-skills", "申论技能贯通", "shenlun_skills", 14),
        ("first-return", "重新出发", "recoveries", 1), ("both-targets", "双科达标", "subjects_on_target", 2),
    )
    for key, name, kind, target in milestones:
        medals.append(_medal(f"career-{key}", name, "生涯成就", f"满足里程碑：{name}", kind, target, metadata={"career_kind": "milestone"}))

    season_specs = (
        ("attendance", "赛季出勤", "season_attendance_rate", 80, "%"), ("tasks", "主任务推进", "season_completed_tasks", 10, "项"),
        ("mocks", "赛季模考", "season_full_simulations", 4, "次"), ("weakness", "核心弱点解决", "season_sealed_errors", 1, "项"),
        ("rank", "赛季段位", "season_ranked", 1, "次"), ("recovery", "赛季复归", "season_recoveries", 1, "次"),
        ("growth", "赛季最大进步", "season_growth", 1, "项"),
    )
    for key, name, kind, target, unit in season_specs:
        medals.append(_medal(f"season-{key}", name, "赛季成就", f"当前赛季达到{name}条件", kind, target, scope="season", progress_unit=unit))
    return medals


def merge_default_catalogs(state: dict[str, Any]) -> None:
    """补齐固定目录，同时保留已有事实和自定义项目。"""
    existing_skills = state.get("catalog", [])
    by_id = {item.get("id"): item for item in existing_skills if isinstance(item, dict)}
    by_name = {
        (item.get("subject"), item.get("module"), item.get("name")): item
        for item in existing_skills
        if isinstance(item, dict)
    }
    merged_skills: list[dict[str, Any]] = []
    consumed: set[int] = set()
    for default in default_skills():
        current = by_id.get(default["id"]) or by_name.get(
            (default["subject"], default["module"], default["name"])
        )
        if current is None:
            merged_skills.append(copy.deepcopy(default))
            continue
        item = copy.deepcopy(default)
        item.update(copy.deepcopy(current))
        item["id"] = default["id"]
        item["tier"] = "standard"
        item.setdefault("needs_retest", False)
        if "legacy_status" not in current:
            item["legacy_status"] = item.get("status") in {
                "owned",
                "mastered",
            } and not item.get("thresholds")
        merged_skills.append(item)
        consumed.add(id(current))
    for item in existing_skills:
        if id(item) not in consumed:
            custom = copy.deepcopy(item)
            custom["tier"] = "custom"
            custom.setdefault("needs_retest", False)
            custom.setdefault("legacy_status", True)
            merged_skills.append(custom)
    state["catalog"] = merged_skills

    existing_medals = {
        item.get("medal_id"): item
        for item in state.get("medals", [])
        if isinstance(item, dict)
    }
    merged_medals = []
    for default in default_medals():
        current = existing_medals.get(default["medal_id"])
        item = copy.deepcopy(default)
        if current:
            was_unlocked = current.get("status") == "unlocked"
            for key in (
                "status",
                "progress_current",
                "evidence_refs",
                "unlocked_at",
                "times_earned",
            ):
                if key in current:
                    item[key] = copy.deepcopy(current[key])
            if was_unlocked:
                item["status"] = "unlocked"
        merged_medals.append(item)
    state["medals"] = merged_medals


def _assessment_refs(items: list[dict[str, Any]]) -> list[str]:
    return [
        str(item.get("assessment_id")) for item in items if item.get("assessment_id")
    ]


def _window_blocks(
    rows: list[dict[str, Any]], window_size: int, *, shenlun: bool = False
) -> list[list[dict[str, Any]]]:
    """按时间生成互不重叠的完整窗口；行测每窗至少包含两次练习。"""
    ordered = sorted(
        rows,
        key=lambda item: (
            str(item.get("date") or item.get("submitted_at") or ""),
            str(item.get("practice_id") or item.get("portfolio_id") or ""),
        ),
    )
    blocks: list[list[dict[str, Any]]] = []
    block: list[dict[str, Any]] = []
    volume = 0
    for item in ordered:
        block.append(item)
        volume += 1 if shenlun else int(item.get("question_count") or 0)
        minimum_sessions = 1 if shenlun else 2
        if volume >= window_size and len(block) >= minimum_sessions:
            blocks.append(block)
            block = []
            volume = 0
    return blocks


def _growth_results(
    ability_practices: list[dict[str, Any]],
    portfolio: list[dict[str, Any]],
) -> dict[tuple[str, str, int], tuple[int, list[str]]]:
    """结算互不复用证据的相邻窗口提升次数。"""
    results: dict[tuple[str, str, int], tuple[int, list[str]]] = {}
    for ability_id, _name, _group, _time_cuts, window_size in ABILITY_SPECS:
        shenlun = ability_id == "shenlun"
        if shenlun:
            rows = [
                item
                for item in portfolio
                if isinstance(item.get("score_rate"), (int, float))
                and item.get("score_source") != "ai_internal"
                and item.get("normalization_status") == "exact"
            ]
        else:
            rows = [
                item
                for item in ability_practices
                if item.get("ability_id") == ability_id
                and int(item.get("question_count") or 0) > 0
            ]
        blocks = _window_blocks(rows, window_size, shenlun=shenlun)
        hits = {(metric, level): [] for metric in ("accuracy", "speed") for level in range(1, 4)}
        for index in range(0, len(blocks) - 1, 2):
            previous, current = blocks[index], blocks[index + 1]
            pair = [*previous, *current]
            ref_key = "portfolio_id" if shenlun else "practice_id"
            refs = [str(item.get(ref_key)) for item in pair if item.get(ref_key)]
            if shenlun:
                old_accuracy = sum(float(item["score_rate"]) for item in previous) / len(previous)
                new_accuracy = sum(float(item["score_rate"]) for item in current) / len(current)
                timed = all(isinstance(item.get("time_minutes"), (int, float)) for item in pair)
                old_speed = sum(float(item["time_minutes"]) for item in previous) / len(previous) if timed else None
                new_speed = sum(float(item["time_minutes"]) for item in current) / len(current) if timed else None
            else:
                old_questions = sum(int(item["question_count"]) for item in previous)
                new_questions = sum(int(item["question_count"]) for item in current)
                old_accuracy = sum(int(item["correct_count"]) for item in previous) / old_questions * 100
                new_accuracy = sum(int(item["correct_count"]) for item in current) / new_questions * 100
                timed = all(isinstance(item.get("duration_seconds"), (int, float)) for item in pair)
                old_speed = sum(float(item["duration_seconds"]) for item in previous) / old_questions if timed else None
                new_speed = sum(float(item["duration_seconds"]) for item in current) / new_questions if timed else None
            accuracy_gain = round(new_accuracy - old_accuracy, 6)
            for level, cut in enumerate((5, 10, 15), start=1):
                if accuracy_gain >= cut:
                    hits[("accuracy", level)].extend(refs)
            if old_speed and new_speed is not None and new_accuracy >= old_accuracy - 5:
                speed_gain = round((old_speed - new_speed) / old_speed * 100, 6)
                for level, cut in enumerate((10, 20, 30), start=1):
                    if speed_gain >= cut:
                        hits[("speed", level)].extend(refs)
        for (metric, level), refs in hits.items():
            # 每个达标窗口对贡献一次；每对证据至少有两个引用。
            pair_size = window_size * 2 if shenlun else None
            if shenlun:
                count = len(refs) // pair_size
            else:
                qualifying_refs = set(refs)
                count = sum(
                    1
                    for index in range(0, len(blocks) - 1, 2)
                    if {
                        str(item.get("practice_id"))
                        for item in [*blocks[index], *blocks[index + 1]]
                        if item.get("practice_id")
                    }.issubset(qualifying_refs)
                )
            results[(ability_id, metric, level)] = (count, list(dict.fromkeys(refs)))
    return results


def refresh_medals(state: dict[str, Any], timestamp: str | None = None) -> None:
    """按事实刷新五类成就；永久档位不回退，重复成就累计次数或星级。"""
    merge_default_catalogs(state)
    season_id = state.get("season", {}).get("season_id")
    practices = [item for item in state.get("practice_records", []) if isinstance(item, dict)]
    ability_practices = [item for item in practices if item.get("counts_for_ability", True)]
    assessments = [item for item in state.get("assessments", []) if item.get("ranked")]
    season_assessments = [item for item in assessments if item.get("season_id") == season_id]
    attendance = [item for item in state.get("attendance", {}).get("records", []) if isinstance(item, dict)]
    effective = [item for item in attendance if item.get("counts_as_effective")]
    resolved = [item for item in state.get("wrong_answers", []) if item.get("status") == "resolved"]
    sealed = [item for item in state.get("error_hunts", []) if item.get("status") == "sealed"]
    countered = [item for item in state.get("error_hunts", []) if item.get("status") in {"countered", "sealed"}]
    portfolio = [item for item in state.get("shenlun_portfolio", []) if isinstance(item, dict)]

    ability_stats: dict[str, dict[str, Any]] = {}
    for ability_id, _name, _group, _cuts, _window in ABILITY_SPECS:
        rows = [item for item in ability_practices if item.get("ability_id") == ability_id]
        refs = [str(item.get("practice_id")) for item in rows if item.get("practice_id")]
        questions = sum(int(item.get("question_count") or 0) for item in rows)
        correct = sum(int(item.get("correct_count") or 0) for item in rows)
        timed = [item for item in rows if isinstance(item.get("duration_seconds"), (int, float))]
        timed_questions = sum(int(item.get("question_count") or 0) for item in timed)
        duration = sum(float(item["duration_seconds"]) for item in timed)
        ability_stats[ability_id] = {
            "accuracy": round(correct / questions * 100, 2) if questions else None,
            "speed": round(duration / timed_questions, 2) if timed_questions else None,
            "questions": questions,
            "timed_questions": timed_questions,
            "refs": refs,
        }
    scored = [
        item
        for item in portfolio
        if isinstance(item.get("score_rate"), (int, float))
        and item.get("score_source") != "ai_internal"
        and item.get("normalization_status") == "exact"
    ]
    timed_essays = [item for item in portfolio if isinstance(item.get("time_minutes"), (int, float))]
    ability_stats["shenlun"] = {
        "score": round(sum(float(item["score_rate"]) for item in scored) / len(scored), 2) if scored else None,
        "speed": round(sum(float(item["time_minutes"]) for item in timed_essays) / len(timed_essays), 2) if timed_essays else None,
        "questions": len(scored), "timed_questions": len(timed_essays),
        "refs": [str(item.get("portfolio_id")) for item in portfolio if item.get("portfolio_id")],
    }
    growth_results = _growth_results(ability_practices, portfolio)

    single_matches: dict[str, tuple[int, list[str]]] = {}
    for medal in state["medals"]:
        condition = medal["condition"]
        if condition["type"] != "module_performance":
            continue
        matches = [item for item in practices if item.get("module") == condition["module"] and int(item.get("question_count") or 0) >= condition["question_count_min"] and float(item.get("accuracy_rate") or 0) >= condition["accuracy_min"] and isinstance(item.get("seconds_per_question"), (int, float)) and float(item["seconds_per_question"]) <= condition["seconds_per_question_max"]]
        single_matches[medal["medal_id"]] = (len(matches), [str(item.get("practice_id")) for item in matches if item.get("practice_id")])

    # 先结算单次和实力，以便生涯里程碑引用结果。
    for medal in state["medals"]:
        condition = medal["condition"]
        kind = condition["type"]
        if kind == "module_performance":
            current, refs = single_matches[medal["medal_id"]]
        elif kind == "strength_level":
            stats = ability_stats[condition["ability_id"]]
            value = stats.get(condition["metric"])
            sample_key = "timed_questions" if condition["metric"] == "speed" else "questions"
            enough_samples = stats[sample_key] >= condition["sample_min"]
            reached = enough_samples and value is not None and (value <= condition["cut"] if condition["metric"] == "speed" else value >= condition["cut"])
            current, refs = int(reached), stats["refs"] if reached else []
        else:
            continue
        previous_refs = medal.get("evidence_refs", []) if medal.get("status") == "unlocked" else []
        medal["progress_current"] = current
        medal["progress_target"] = condition["target"]
        medal["times_earned"] = current if medal.get("repeatable") else int(medal.get("status") == "unlocked" or current >= condition["target"])
        medal["evidence_refs"] = list(dict.fromkeys([*previous_refs, *refs]))
        if medal.get("status") != "unlocked" and current >= condition["target"] and refs:
            medal["status"] = "unlocked"
            medal["unlocked_at"] = timestamp

    strength_unlocked = sum(item.get("status") == "unlocked" for item in state["medals"] if item.get("category") == "实力勋章")
    growth_earned = sum(int(item.get("times_earned", 0)) for item in state["medals"] if item.get("category") == "成长成就")
    full_sims_all = [item for item in state.get("assessments", []) if item.get("conditions", {}).get("full_simulation")]
    season_full_sims = [item for item in full_sims_all if item.get("season_id") == season_id]
    planned = [item for item in attendance if item.get("status") != "planned_rest"]
    season_planned = [item for item in planned if item.get("season_id") == season_id]
    season_effective = [item for item in effective if item.get("season_id") == season_id]
    completed_tasks = len([item for item in state.get("task_history", []) if item.get("status") in {"verified", "reward_ready", "revealed"}]) + int(state.get("daily_quest", {}).get("status") in {"verified", "reward_ready", "revealed"})
    unlocked_skills = [item for item in state.get("catalog", []) if item.get("status") in {"discovered", "owned", "mastered"}]
    facts = {
        "effective_days": len(effective), "resolved_wrongs": len(resolved), "shenlun_answers": len(portfolio),
        "practice_questions": sum(int(item.get("question_count") or 0) for item in practices), "full_simulations_all": len(full_sims_all),
        "resolved_retests": len([item for item in resolved if item.get("status") == "resolved"]), "completed_tasks": completed_tasks,
        "free_practices": len([item for item in practices if item.get("record_type") == "free_practice"]), "completed_retests": len([item for item in practices if item.get("record_type") == "retest"]),
        "countered_errors": len(countered), "sealed_errors": len(sealed), "ranked_assessments": len(assessments), "personal_records": 0,
        "strength_unlocked": strength_unlocked, "growth_earned": growth_earned, "measured_modules": len({item.get("module") for item in practices}),
        "measured_abilities": len([stats for stats in ability_stats.values() if stats.get("questions")]),
        "complete_skill_modules": 0, "xingce_skills": len([item for item in unlocked_skills if item.get("subject") == "行测"]),
        "shenlun_skills": len([item for item in unlocked_skills if item.get("subject") == "申论"]), "recoveries": len([item for item in attendance if item.get("status") == "recovery"]),
        "subjects_on_target": 0, "season_attendance_rate": round(len(season_effective) / len(season_planned) * 100) if season_planned else 0,
        "season_completed_tasks": int(state.get("season", {}).get("season_completed_tasks", 0)), "season_full_simulations": len(season_full_sims),
        "season_sealed_errors": len([item for item in sealed if item.get("season_id") == season_id]), "season_ranked": int(state.get("season", {}).get("rank") != "未定级"),
        "season_recoveries": len([item for item in attendance if item.get("season_id") == season_id and item.get("status") == "recovery"]), "season_growth": int(growth_earned > 0),
    }
    for module in {item.get("module") for item in state.get("catalog", [])}:
        module_items = [item for item in state.get("catalog", []) if item.get("module") == module]
        if module_items and all(item.get("status") in {"discovered", "owned", "mastered"} for item in module_items):
            facts["complete_skill_modules"] += 1

    for medal in state["medals"]:
        condition = medal["condition"]
        kind = condition["type"]
        if kind in {"module_performance", "strength_level"}:
            continue
        if kind == "growth_window":
            current, refs = growth_results.get(
                (condition["ability_id"], condition["metric"], condition["level"]),
                (0, []),
            )
        else:
            current, refs = int(facts.get(kind, 0)), []
        if kind == "effective_days": refs = [f"attendance:{item.get('date')}" for item in effective]
        elif kind in {"resolved_wrongs", "resolved_retests"}: refs = [str(item.get("wrong_id")) for item in resolved]
        elif kind in {"sealed_errors", "season_sealed_errors"}: refs = [str(item.get("error_hunt_id")) for item in sealed]
        elif kind in {"shenlun_answers"}: refs = [str(item.get("portfolio_id")) for item in portfolio]
        elif kind in {"ranked_assessments", "full_simulations_all", "season_full_simulations"}: refs = _assessment_refs(assessments if kind == "ranked_assessments" else full_sims_all)
        elif current: refs = [f"fact:{kind}:{current}"]
        target = int(condition["target"])
        previous_refs = medal.get("evidence_refs", []) if medal.get("status") == "unlocked" else []
        medal["progress_target"] = target
        if medal.get("repeatable"):
            medal["times_earned"] = current // target
            medal["progress_current"] = current % target
            reached = medal["times_earned"] > 0
        else:
            medal["progress_current"] = min(current, target)
            medal["times_earned"] = int(medal.get("status") == "unlocked" or current >= target)
            reached = current >= target
        medal["evidence_refs"] = list(dict.fromkeys([*previous_refs, *refs]))
        if medal.get("status") != "unlocked" and reached and refs:
            medal["status"] = "unlocked"
            medal["unlocked_at"] = timestamp


def catalog_counts() -> dict[str, int]:
    return Counter(item["module"] for item in default_skills())


def rebuild_medals(state: dict[str, Any], timestamp: str | None = None) -> None:
    """丢弃旧目录状态，只按当前固定目录和可核验事实重算。"""
    state["medals"] = default_medals()
    refresh_medals(state, timestamp)
