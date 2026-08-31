"""
版本记录：
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

PERFORMANCE_TIERS = (
    ("starter", "崭露头角", 70, 90),
    ("surge", "势如破竹", 80, 80),
    ("elite", "百里挑一", 90, 70),
    ("peak", "登峰造极", 95, 60),
    ("peerless", "天下无双", 100, 55),
)
MIN_MEDAL_QUESTIONS = 10


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
) -> dict[str, Any]:
    condition = {"type": condition_type, "target": target, "scope": scope}
    if module:
        condition["module"] = module
    if condition_extra:
        condition.update(condition_extra)
    return {
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
    }


def default_medals() -> list[dict[str, Any]]:
    medals = [
        _medal(
            "medal-first-result",
            "第一份战果",
            "起步",
            "完成第一次有效验证入账",
            "effective_days",
            1,
            scope="career",
        ),
        _medal(
            "medal-three-days",
            "三日开局",
            "起步",
            "累计完成 3 个有效学习日",
            "effective_days",
            3,
        ),
        _medal(
            "medal-seven-days",
            "七日成习",
            "起步",
            "累计完成 7 个有效学习日",
            "effective_days",
            7,
        ),
        _medal(
            "medal-first-ranked",
            "首次排位",
            "起步",
            "完成第一次有效排位测评",
            "ranked_assessments",
            1,
        ),
    ]
    for module, key in XINGCE_MODULE_IDS.items():
        for tier_id, tier_name, accuracy_min, seconds_max in PERFORMANCE_TIERS:
            medals.append(
                _medal(
                    f"medal-{key}-{tier_id}",
                    f"{module}·{tier_name}",
                    "模块战绩",
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
                )
            )
    medals.extend(
        [
            _medal(
                "medal-first-correction",
                "第一次订正",
                "纠错",
                "完成 1 道错题订正",
                "resolved_wrongs",
                1,
            ),
            _medal(
                "medal-ten-wrongs",
                "错题十清",
                "纠错",
                "累计解决 10 道错题",
                "resolved_wrongs",
                10,
            ),
            _medal(
                "medal-five-easy-points",
                "易错点封印",
                "纠错",
                "累计解决 5 个易错点",
                "sealed_errors",
                5,
            ),
            _medal(
                "medal-module-gold",
                "模块黄金",
                "战绩",
                "任一模块达到黄金段位",
                "gold_modules",
                1,
            ),
            _medal(
                "medal-xingce-target",
                "行测达标",
                "战绩",
                "行测有效全卷达到目标分",
                "xingce_target",
                1,
            ),
            _medal(
                "medal-shenlun-target",
                "申论达标",
                "战绩",
                "申论可比完整评分达到目标分",
                "shenlun_target",
                1,
            ),
            _medal(
                "medal-both-stable",
                "双科稳定",
                "战绩",
                "行测和申论均完成本赛季定级",
                "ranked_subjects",
                2,
                scope="season",
            ),
            _medal(
                "medal-overall-master",
                "综合大师",
                "战绩",
                "综合段位达到大师",
                "overall_master",
                1,
                scope="season",
            ),
            _medal(
                "medal-season-finish",
                "赛季收官",
                "赛季",
                "完成 1 个正式赛季",
                "finished_seasons",
                1,
            ),
            _medal(
                "medal-four-sims",
                "四次全真",
                "赛季",
                "本赛季完成 4 次全真模拟",
                "full_simulations",
                4,
                scope="season",
            ),
            _medal(
                "medal-core-mastered",
                "五路全能",
                "赛季",
                "五个行测模块均至少达到百里挑一标准",
                "five_modules_elite",
                5,
            ),
        ]
    )
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


def refresh_medals(state: dict[str, Any], timestamp: str | None = None) -> None:
    """按状态事实刷新勋章进度；已点亮勋章不会回退。"""
    merge_default_catalogs(state)
    season_id = state.get("season", {}).get("season_id")
    assessments = [item for item in state.get("assessments", []) if item.get("ranked")]
    season_assessments = [
        item for item in assessments if item.get("season_id") == season_id
    ]
    effective = [
        item
        for item in state.get("attendance", {}).get("records", [])
        if item.get("counts_as_effective")
    ]
    resolved = [
        item
        for item in state.get("wrong_answers", [])
        if item.get("status") == "resolved"
    ]
    sealed = [
        item for item in state.get("error_hunts", []) if item.get("status") == "sealed"
    ]
    practice_records = [
        item for item in state.get("practice_records", []) if isinstance(item, dict)
    ]
    gold_order = {
        "未定级": 0,
        "青铜": 1,
        "白银": 2,
        "黄金": 3,
        "钻石": 4,
        "大师": 5,
        "王者": 6,
    }
    gold_modules = [
        item
        for item in state.get("module_rankings", [])
        if gold_order.get(item.get("rank"), 0) >= 3
    ]
    subject_ranked = [
        item
        for item in state.get("subject_rankings", [])
        if item.get("season_id") == season_id and item.get("rank") != "未定级"
    ]
    x_target = state.get("goal_contract", {}).get("xingce_target")
    s_target = state.get("goal_contract", {}).get("shenlun_target")
    x_hits = [
        item
        for item in assessments
        if item.get("subject") == "行测"
        and x_target is not None
        and isinstance(item.get("score"), (int, float))
        and item["score"] >= x_target
    ]
    s_hits = [
        item
        for item in assessments
        if item.get("subject") == "申论"
        and s_target is not None
        and item.get("score_source") != "ai_internal"
        and isinstance(item.get("score"), (int, float))
        and item["score"] >= s_target
    ]
    full_sims = [
        item
        for item in season_assessments
        if item.get("conditions", {}).get("full_simulation")
    ]
    elite_modules: dict[str, list[str]] = {}
    for module in XINGCE_MODULE_IDS:
        refs = [
            str(item.get("practice_id"))
            for item in practice_records
            if item.get("module") == module
            and item.get("locked_before_start") is True
            and item.get("question_count", 0) >= MIN_MEDAL_QUESTIONS
            and item.get("accuracy_rate", 0) >= 90
            and item.get("seconds_per_question", float("inf")) <= 70
            and item.get("practice_id")
        ]
        if refs:
            elite_modules[module] = refs

    for medal in state["medals"]:
        condition = medal["condition"]
        kind = condition["type"]
        refs: list[str] = []
        if kind == "effective_days":
            current, refs = (
                len(effective),
                [f"attendance:{item.get('date')}" for item in effective],
            )
        elif kind == "ranked_assessments":
            current, refs = len(assessments), _assessment_refs(assessments)
        elif kind == "module_performance":
            matches = [
                item
                for item in practice_records
                if item.get("module") == condition["module"]
                and item.get("locked_before_start") is True
                and item.get("question_count", 0) >= condition["question_count_min"]
                and item.get("accuracy_rate", 0) >= condition["accuracy_min"]
                and item.get("seconds_per_question", float("inf"))
                <= condition["seconds_per_question_max"]
            ]
            current = len(matches)
            refs = [
                str(item["practice_id"]) for item in matches if item.get("practice_id")
            ]
        elif kind == "resolved_wrongs":
            current, refs = len(resolved), [item["wrong_id"] for item in resolved]
        elif kind == "sealed_errors":
            current, refs = len(sealed), [item["error_hunt_id"] for item in sealed]
        elif kind == "gold_modules":
            current, refs = (
                len(gold_modules),
                [item["ranking_id"] for item in gold_modules],
            )
        elif kind == "xingce_target":
            current, refs = len(x_hits), _assessment_refs(x_hits)
        elif kind == "shenlun_target":
            current, refs = len(s_hits), _assessment_refs(s_hits)
        elif kind == "ranked_subjects":
            current, refs = (
                len({item.get("subject") for item in subject_ranked}),
                [item["ranking_id"] for item in subject_ranked],
            )
        elif kind == "overall_master":
            current = int(gold_order.get(state.get("season", {}).get("rank"), 0) >= 5)
            refs = [str(season_id)] if current else []
        elif kind == "finished_seasons":
            current, refs = (
                len(state.get("season_history", [])),
                [
                    str(item.get("season_id"))
                    for item in state.get("season_history", [])
                ],
            )
        elif kind == "full_simulations":
            current, refs = len(full_sims), _assessment_refs(full_sims)
        elif kind == "five_modules_elite":
            current = len(elite_modules)
            refs = [ref for values in elite_modules.values() for ref in values]
        else:
            current = 0
        if medal.get("status") == "unlocked":
            current = max(current, condition["target"])
            refs = [*medal.get("evidence_refs", []), *refs]
        medal["progress_current"] = current
        medal["progress_target"] = condition["target"]
        medal["evidence_refs"] = list(dict.fromkeys(refs))
        if (
            medal.get("status") != "unlocked"
            and current >= condition["target"]
            and refs
        ):
            medal["status"] = "unlocked"
            medal["unlocked_at"] = timestamp


def catalog_counts() -> dict[str, int]:
    return Counter(item["module"] for item in default_skills())


def rebuild_medals(state: dict[str, Any], timestamp: str | None = None) -> None:
    """丢弃旧目录状态，只按当前固定目录和可核验事实重算。"""
    state["medals"] = default_medals()
    refresh_medals(state, timestamp)
