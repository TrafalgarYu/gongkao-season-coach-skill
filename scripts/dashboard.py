"""
版本记录：
- v5.0.0 / 2026-08-31
  - 勋章墙拆为单次战绩、实力勋章、成长成就、生涯成就和赛季成就。
  - 实力勋章按 11 项能力展示正确率或评分与速度五档路线。
  - 错题本增加固定大类与题目筛选，练习记录增加排序。
  - 把已验收的卷宗式八栏目布局落入正式生成器，恢复今日任务和能力分析入口。
- v4.0.0 / 2026-08-31
  - 技能页取消“考场可用”等主观标签，改为直接展示实测数据和证据量。
  - 战绩页新增练习战绩表，勋章墙切换为 40 枚量化勋章目录。
- v3.2.0 / 2026-08-31
  - 移除页面每 15 秒自动重载，常驻服务改为每天 08:00 生成一次快照。
  - 页面新增手动刷新入口；点击后立即读取最新状态，普通访问不再触发重建。
- v3.1.0 / 2026-08-30
  - 战绩和申论答题册同时显示原始分数、满分、得分率与中文评分来源。
  - 历史考试基线使用原考试标签，AI 单题评分不再显示成整卷成绩。
- v3.0.0 / 2026-08-30
  - 备考总览改为技能状态和学习记录两排可点击指标。
  - 勋章墙展示完整目录、进度口径与锁定状态，段位区展示重新定级进度。
  - 技能页公开四档熟练度的鉴定标准，申论答题册展示原文与反馈。

- v2.1.0 / 2026-08-30
  - 技能卡新增近期正确率或得分率、样本数、题量和证据窗口，检查项不再显示为正确率式百分比。
  - 新增 --serve 只读 HTTP 服务；每次访问按状态文件修改时间更新页面，并禁止浏览器缓存旧页面。

- v2.0.0 / 2026-08-29
  - 总览拆分为技能、错题、易错点、战绩、申论答题册和勋章六个栏目。
  - 技能改用熟练度展示，战绩增加模块、科目和综合段位。
  - 支持单次生成与监听状态文件自动重建，不依赖第三方包。

用途：把 gongkao-season-coach 的持久化状态生成为完整备考总览。
"""

from __future__ import annotations

import argparse
import html
import json
import time
from collections.abc import Callable
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

try:
    from .catalogs import ABILITY_SPECS, TIER_NAMES, default_medals
    from .state_store import StateError, read_current_state, resolve_state_path
except ImportError:  # 直接运行 scripts/dashboard.py 时没有包上下文
    from catalogs import ABILITY_SPECS, TIER_NAMES, default_medals
    from state_store import StateError, read_current_state, resolve_state_path

EASY_POINT_LABELS = {
    "spotted": "待确认",
    "identified": "已找到原因",
    "countered": "纠正中",
    "sealed": "已解决",
}
WRONG_STATUS_LABELS = {
    "recorded": "待订正",
    "corrected": "已订正",
    "review_due": "待复测",
    "resolved": "已掌握",
}
SCORE_SOURCE_LABELS = {
    "official": "正式考试",
    "institution": "机构评分",
    "teacher": "教师评分",
    "platform": "平台量表",
    "user_self": "用户自评",
    "ai_internal": "AI内部估分",
}


def _escape(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _text(value: Any, empty: str = "尚未记录") -> str:
    if value in (None, "", {}, []):
        return empty
    if isinstance(value, dict):
        return "；".join(f"{key}：{_text(item)}" for key, item in value.items())
    if isinstance(value, list):
        return "；".join(_text(item) for item in value)
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def _score_text(item: dict[str, Any]) -> str:
    score = item.get("score")
    if score is None:
        return "尚未评分"
    if item.get("normalization_status") == "needs_review":
        return f"{score}（口径待确认）"
    score_max = item.get("score_max")
    score_rate = item.get("score_rate")
    if score_max is None:
        return str(score)
    if score_rate is not None and score_max != 100:
        return f"{score}/{score_max}（{score_rate}%）"
    return f"{score}/{score_max}"


def _format_percent(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "--"
    return f"{value:g}%"


def _skill_performance(item: dict[str, Any]) -> tuple[str, str, str]:
    performance = item.get("recent_performance")
    default_label = "最近得分率" if item.get("subject") == "申论" else "最近正确率"
    if not isinstance(performance, dict) or performance.get("value") is None:
        return default_label, "--", "实测数据不足"

    metric = performance.get("metric")
    label = "最近得分率" if metric == "score_rate" else "最近正确率"
    sample_count = performance.get("sample_count")
    question_count = performance.get("question_count")
    basis = performance.get("window_label")
    details: list[str] = []
    if isinstance(basis, str) and basis:
        details.append(basis)
    elif isinstance(sample_count, int) and not isinstance(sample_count, bool):
        details.append(f"最近 {sample_count} 次")
    if isinstance(question_count, int) and not isinstance(question_count, bool):
        details.append(f"共 {question_count} 题")
    return label, _format_percent(performance.get("value")), " · ".join(details)


def _render_skill(item: dict[str, Any], current_ids: set[str]) -> str:
    evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
    status = "measured" if item.get("recent_performance") else "unmeasured"
    item_id = str(item.get("id", ""))
    current = item_id in current_ids
    metric_label, metric_value, evidence_basis = _skill_performance(item)
    search_text = " ".join(
        str(item.get(key, "")) for key in ("name", "subject", "module")
    ).lower()
    return f"""
    <article class="card skill-card" data-status="{_escape(status)}"
             data-current="{str(current).lower()}" data-search="{_escape(search_text)}">
      <div class="card-head"><div>
        <p class="path">{_escape(item.get("subject", "未分类"))} · {_escape(item.get("module", "未分组"))}</p>
        <h3>{_escape(item.get("name", item_id or "未命名技能"))}</h3>
      </div><span class="status status-{_escape(status)}">{"有实测" if status == "measured" else "待记录"}</span></div>
      <div class="skill-performance"><div><span>{_escape(metric_label)}</span>
        <strong>{_escape(metric_value)}</strong></div><p>{_escape(evidence_basis)}</p></div>
      <p>有效证据：{len(evidence)} 条</p>
      <p class="muted">技能页只展示实测事实；勋章按模块练习的题量、正确率和实际用时判定。</p>
      {('<span class="season-tag">本赛季重点</span>' if current else "")}
    </article>"""


def _render_wrong(item: dict[str, Any]) -> str:
    return f"""
    <article class="card wrong-card" data-wrong-module="{_escape(item.get("module"))}" data-wrong-id="{_escape(item.get("wrong_id"))}">
      <p class="path">{_escape(item.get("date"))} · {_escape(item.get("subject"))} · {_escape(item.get("module"))}</p>
      <h3>{_escape(item.get("question_ref") or "未命名错题")}</h3>
      <p>我的答案：{_escape(_text(item.get("user_answer")))}</p>
      <p>正确答案：{_escape(_text(item.get("correct_answer")))}</p>
      <p>订正：{_escape(_text(item.get("correction")))}</p>
      <p class="muted">状态：{_escape(WRONG_STATUS_LABELS.get(str(item.get("status")), item.get("status")))} · 复测：{_escape(_text(item.get("next_review_at")))}</p>
    </article>"""


def _render_easy_point(item: dict[str, Any]) -> str:
    status = str(item.get("status", "spotted"))
    evidence = item.get("evidence")
    evidence_count = len(evidence) if isinstance(evidence, list) else 0
    return f"""
    <article class="card">
      <p class="path">{_escape(item.get("subject"))} · {_escape(item.get("module"))}</p>
      <div class="card-head"><h3>{_escape(item.get("mechanism") or "未命名易错点")}</h3>
        <span class="status">{_escape(EASY_POINT_LABELS.get(status, status))}</span></div>
      <p>相关证据：{evidence_count} 条</p>
      <p class="muted">下次复测：{_escape(_text(item.get("next_review_at")))}</p>
    </article>"""


def _render_ranking(item: dict[str, Any]) -> str:
    stars = item.get("stars", 0)
    star_text = ""
    if isinstance(stars, int):
        star_text = "★" * stars + "☆" * max(0, 3 - stars)
    label = item.get("module") or item.get("subject") or "综合"
    return f"""
    <article class="card rank-card">
      <p class="path">{_escape(item.get("subject") or "综合")}</p>
      <h3>{_escape(label)}</h3>
      <strong class="rank">{_escape(item.get("rank", "未定级"))} {star_text}</strong>
      <p>稳定成绩：{_escape(_text(item.get("stable_value")))}</p>
      <p>下一段：{_escape(_text(item.get("next_rank"), "已到最高段位"))} · 还差 {_escape(_text(item.get("gap_to_next"), "待定级"))}</p>
      <p class="muted">有效样本：{_escape(item.get("sample_size", 0))}</p>
    </article>"""


def _render_assessment(item: dict[str, Any]) -> str:
    ranked = "计入段位" if item.get("ranked") else "仅记录"
    conditions = item.get("conditions")
    if not isinstance(conditions, dict):
        conditions = {}
    date_label = item.get("date") or conditions.get("exam_label") or "日期未记录"
    source = SCORE_SOURCE_LABELS.get(
        str(item.get("score_source")), _text(item.get("score_source"))
    )
    return f"""
    <tr><td>{_escape(date_label)}</td><td>{_escape(item.get("subject"))}</td>
    <td>{_escape(item.get("scope"))}</td><td>{_escape(_score_text(item))}</td>
    <td>{_escape(source)}</td><td>{ranked}</td></tr>"""


def _render_practice(item: dict[str, Any]) -> str:
    purpose = "长期能力" if item.get("counts_for_ability", True) else "复测单列"
    duration = f"{item.get('duration_seconds')} 秒" if item.get("duration_seconds") is not None else "未记录"
    average = f"{item.get('seconds_per_question')} 秒" if item.get("seconds_per_question") is not None else "未记录"
    return f"""
    <tr data-date="{_escape(item.get('date'))}" data-accuracy="{_escape(item.get('accuracy_rate'))}" data-type="{_escape(item.get('record_type'))}"><td>{_escape(item.get("date"))}</td><td>{_escape(item.get("module"))}</td>
    <td>{_escape(item.get("correct_count"))}/{_escape(item.get("question_count"))}</td>
    <td>{_escape(_format_percent(item.get("accuracy_rate")))}</td>
    <td>{_escape(duration)}</td>
    <td>{_escape(average)}</td>
    <td>{_escape(purpose)}</td></tr>"""


def _render_answer(item: dict[str, Any]) -> str:
    answer = _text(item.get("answer_text"), "未保存原文")
    if len(answer) > 240:
        answer = f"{answer[:240]}…"
    source = SCORE_SOURCE_LABELS.get(
        str(item.get("score_source")), _text(item.get("score_source"))
    )
    score_label = (
        "AI内部单题评分" if item.get("score_source") == "ai_internal" else "得分"
    )
    return f"""
    <article class="card">
      <p class="path">{_escape(item.get("date"))} · {_escape(item.get("task_type"))}</p>
      <h3>{_escape(item.get("prompt_ref") or "未命名作答")}</h3>
      <p>{score_label}：{_escape(_score_text(item))} · 来源：{_escape(source)}</p>
      <p class="answer-text">{_escape(answer)}</p>
      <p>反馈：{_escape(_text(item.get("feedback")))}</p>
      <p class="muted">字数：{_escape(_text(item.get("word_count")))} · 用时：{_escape(_text(item.get("time_minutes")))} 分钟<br>批改维度：{_escape(_text(item.get("dimensions")))}</p>
    </article>"""


def _render_medal(item: dict[str, Any]) -> str:
    unlocked = item.get("status") == "unlocked"
    condition = item.get("condition") if isinstance(item.get("condition"), dict) else {}
    scope = "本赛季" if condition.get("scope") == "season" else "生涯累计"
    current = item.get("progress_current", 0)
    target = item.get("progress_target", condition.get("target", 1))
    evidence = item.get("evidence_refs")
    evidence_count = len(evidence) if isinstance(evidence, list) else 0
    return f"""
    <article class="card medal {"unlocked" if unlocked else "locked"}"
             data-medal-status="{"unlocked" if unlocked else "locked"}"
             data-medal-category="{_escape(item.get("category", "其他"))}">
      <div class="medal-mark"><span>{"已点亮" if unlocked else "未点亮"}</span></div>
      <p class="path">{_escape(item.get("category", "其他"))} · {scope}</p>
      <h3>{_escape(item.get("name") or "未命名勋章")}</h3>
      <p>{_escape(_text(item.get("description")))}</p>
      <div class="medal-progress"><i style="width:{min(100, round(current / target * 100)) if target else 0}%"></i></div>
      <p class="medal-count">{('★' + _escape(item.get('times_earned', 0)) + ' · ' if item.get('repeatable') else '')}{_escape(current)}/{_escape(target)} {_escape(item.get("progress_unit", "项"))}</p>
      <p class="muted">证据 {evidence_count} 条 · 点亮时间：{_escape(_text(item.get("unlocked_at"), "尚未点亮"))}</p>
    </article>"""


def _render_strength_board(state: dict[str, Any], medals: list[dict[str, Any]]) -> str:
    practices = [item for item in state.get("practice_records", []) if item.get("counts_for_ability", True)]
    portfolio = [item for item in state.get("shenlun_portfolio", []) if item.get("score_source") != "ai_internal" and item.get("normalization_status") == "exact"]
    cards: list[str] = []
    for ability_id, name, group, _time_cuts, window in ABILITY_SPECS:
        rows = [item for item in practices if item.get("ability_id") == ability_id]
        questions = sum(int(item.get("question_count") or 0) for item in rows)
        correct = sum(int(item.get("correct_count") or 0) for item in rows)
        timed = [item for item in rows if item.get("duration_seconds") is not None]
        timed_questions = sum(int(item.get("question_count") or 0) for item in timed)
        if ability_id == "shenlun":
            score_rows = [item for item in portfolio if isinstance(item.get("score_rate"), (int, float))]
            metric_value = round(sum(item["score_rate"] for item in score_rows) / len(score_rows), 1) if score_rows else None
            speed_rows = [item for item in portfolio if isinstance(item.get("time_minutes"), (int, float))]
            speed_value = round(sum(item["time_minutes"] for item in speed_rows) / len(speed_rows), 1) if speed_rows else None
            metric_name, unit = "评分", "分钟/题"
        else:
            metric_value = round(correct / questions * 100, 1) if questions else None
            speed_value = round(sum(item["duration_seconds"] for item in timed) / timed_questions, 1) if timed_questions else None
            metric_name, unit = "正确率", "秒/题"
        tracks: list[str] = []
        for metric, label, value, suffix, sample_count in (("score" if ability_id == "shenlun" else "accuracy", metric_name, metric_value, "%", len(score_rows) if ability_id == "shenlun" else questions), ("speed", "速度", speed_value, unit, len(speed_rows) if ability_id == "shenlun" else timed_questions)):
            tiers = sorted([item for item in medals if item.get("ability_id") == ability_id and item.get("metric") == metric], key=lambda item: item.get("level", 0))
            achieved = sum(item.get("status") == "unlocked" for item in tiers)
            steps = "".join(f'<span class="medal-step level-{index} {"earned" if item.get("status") == "unlocked" else "next" if index == achieved + 1 else ""}"><b>{_escape(TIER_NAMES[index-1])}</b><small>{_escape(item.get("condition", {}).get("cut"))}{suffix}</small></span>' for index, item in enumerate(tiers, start=1))
            sample_unit = "篇" if ability_id == "shenlun" else "题"
            tracks.append(f'<div class="strength-track"><div class="track-head"><span>{label} · 当前 {_escape(value if value is not None else "暂无")}{suffix if value is not None else ""} · 样本 {sample_count}/{window}{sample_unit}</span><b>已达 {achieved}/5档 · 还剩 {5-achieved}档</b></div><div class="medal-ladder">{steps}</div></div>')
        cards.append(f'<article class="card strength-card"><p class="path">{_escape(group)}</p><h3>{_escape(name)}</h3>{"".join(tracks)}</article>')
    return "".join(cards)


def _empty(message: str) -> str:
    return f'<div class="empty">{_escape(message)}</div>'


def _format_seconds(value: int | float | None) -> str:
    if not isinstance(value, (int, float)):
        return "未记录"
    minutes, seconds = divmod(round(value), 60)
    return f"{minutes}:{seconds:02d}"


def render_html(state: dict[str, Any], *, source_path: Path) -> str:
    catalog = [item for item in state.get("catalog", []) if isinstance(item, dict)]
    catalog.sort(
        key=lambda item: (
            0 if item.get("recent_performance") else 1,
            str(item.get("subject", "")),
            str(item.get("module", "")),
            str(item.get("name", "")),
        )
    )
    current_ids = {
        str(item_id)
        for item_id in state.get("season", {}).get("locked_catalog_ids", [])
    }
    skills = "".join(_render_skill(item, current_ids) for item in catalog)
    wrongs = "".join(_render_wrong(item) for item in state.get("wrong_answers", []))
    easy_points = "".join(
        _render_easy_point(item) for item in state.get("error_hunts", [])
    )
    rankings = "".join(
        _render_ranking(item)
        for item in [
            *state.get("subject_rankings", []),
            *state.get("module_rankings", []),
        ]
    )
    assessments = "".join(
        _render_assessment(item) for item in state.get("assessments", [])
    )
    practice_records = [
        item for item in state.get("practice_records", []) if isinstance(item, dict)
    ]
    practices = "".join(_render_practice(item) for item in practice_records)
    answers = "".join(
        _render_answer(item) for item in state.get("shenlun_portfolio", [])
    )
    medal_items = state.get("medals", [])
    fixed_medal_ids = {item["medal_id"] for item in default_medals()}
    fixed_medals = [
        item for item in medal_items if item.get("medal_id") in fixed_medal_ids
    ]
    medal_groups = {
        category: [item for item in medal_items if item.get("category") == category]
        for category in ("单次战绩", "实力勋章", "成长成就", "生涯成就", "赛季成就")
    }
    single_medals = "".join(_render_medal(item) for item in medal_groups["单次战绩"])
    strength_board = _render_strength_board(state, medal_groups["实力勋章"])
    growth_medals = "".join(_render_medal(item) for item in medal_groups["成长成就"])
    career_medals = "".join(_render_medal(item) for item in medal_groups["生涯成就"])
    season_medals = "".join(_render_medal(item) for item in medal_groups["赛季成就"])

    standard = [item for item in catalog if item.get("tier") == "standard"]
    total = len(standard)
    measured_skills = sum(bool(item.get("recent_performance")) for item in standard)
    measured_modules = len({item.get("module") for item in practice_records})
    unlocked_medals = sum(item.get("status") == "unlocked" for item in fixed_medals)
    open_easy_points = sum(
        item.get("status") != "sealed" for item in state.get("error_hunts", [])
    )
    adjustment = state.get("economy", {}).get("command_points", 0)
    adjustment_cap = state.get("economy", {}).get("command_points_cap", 0)
    season = state.get("season", {})
    rank = season.get("rank", "未定级")
    stars = season.get("stars", 0)
    previous_rank = season.get("previous_rank", "未定级")
    previous_stars = season.get("previous_stars", 0)
    highest_rank = season.get("highest_rank", "未定级")
    placement = season.get("placement_progress", {})
    updated_at = state.get("engine", {}).get("updated_at") or "尚未记录"
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")

    daily = state.get("daily_quest", {})
    accepted_id = daily.get("accepted_task_id")
    accepted = next(
        (
            item
            for item in daily.get("options", [])
            if item.get("task_id") == accepted_id
        ),
        (daily.get("locked_conditions") or {}).get("template", {}),
    )
    accepted_skills = [
        item.get("name")
        for item in catalog
        if item.get("id") in set(accepted.get("skill_ids", []))
    ]
    focus_module = accepted.get("module") or "等待生成任务"
    focus_name = accepted_skills[0] if accepted_skills else focus_module
    task_title = (
        f"补齐{focus_module}核心技能“{focus_name}”"
        if accepted_id
        else "今天还没有接取主任务"
    )
    task_practices = [
        item for item in practice_records if item.get("task_id") == accepted_id
    ]
    task_practice = task_practices[-1] if task_practices else None
    if task_practice:
        task_result = (
            str(task_practice["question_count"]),
            str(task_practice["correct_count"]),
            f'{task_practice["accuracy_rate"]:g}%',
            _format_seconds(task_practice.get("duration_seconds")),
        )
    else:
        task_result = ("—", "—", "—", "—")
    status_labels = {
        "not_generated": "待生成",
        "offered": "待选择",
        "accepted": "进行中",
        "submitted": "待验收",
        "verified": "已验收",
        "reward_ready": "已完成待查看结算",
        "revealed": "已结算",
    }
    task_status = status_labels.get(daily.get("status"), str(daily.get("status") or "待生成"))
    verification_note = (daily.get("verification") or {}).get("note")
    evidence_items = [
        "已保存题量、正确数与用户提供的实际用时",
        f"本次记录已归入{task_practice.get('ability_id')}能力项" if task_practice else "提交后将自动分类进入历史记录",
    ]
    if verification_note:
        evidence_items.append(str(verification_note))
    evidence_html = "".join(f"<li>{_escape(item)}</li>" for item in evidence_items)

    ability_samples: list[tuple[str, str, int, int, float | None]] = []
    for ability_id, ability_name, _group, _cuts, window in ABILITY_SPECS:
        if ability_id == "shenlun":
            sample = len(
                [
                    item
                    for item in state.get("shenlun_portfolio", [])
                    if item.get("score_source") != "ai_internal"
                    and item.get("normalization_status") == "exact"
                ]
            )
            value = None
        else:
            rows = [
                item
                for item in practice_records
                if item.get("counts_for_ability", True)
                and item.get("ability_id") == ability_id
            ]
            sample = sum(int(item.get("question_count") or 0) for item in rows)
            correct = sum(int(item.get("correct_count") or 0) for item in rows)
            value = round(correct / sample * 100, 1) if sample else None
        ability_samples.append((ability_id, ability_name, sample, window, value))
    sampled_abilities = sum(sample > 0 for _id, _name, sample, _window, _value in ability_samples)
    timed_abilities = len(
        {
            item.get("ability_id")
            for item in practice_records
            if item.get("counts_for_ability", True)
            and isinstance(item.get("duration_seconds"), (int, float))
        }
    )
    pending_samples = [item for item in ability_samples if 0 < item[2] < item[3]]
    next_ability = min(pending_samples, key=lambda item: item[3] - item[2]) if pending_samples else ability_samples[0]
    next_count = max(1, next_ability[3] - next_ability[2])
    next_title = f"{next_ability[1]}再补一组"
    next_reason = f"目前长期样本 {next_ability[2]}/{next_ability[3]}{'篇' if next_ability[0] == 'shenlun' else '题'}，补齐后才能按历史数据授予实力档位。"
    attendance_records = state.get("attendance", {}).get("records", [])
    planned_days = len([item for item in attendance_records if item.get("status") != "planned_rest"])
    effective_days = len([item for item in attendance_records if item.get("counts_as_effective")])

    return f"""<!doctype html>
<html lang="zh-CN"><head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <title>公考备考总览</title>
  <style>
    :root {{ color-scheme:dark; --bg:#08111d; --panel:#111d2b; --line:#26364a;
      --text:#edf5ff; --muted:#91a3b8; --gold:#ffc857; --blue:#4cc9f0; --green:#4ade80; }}
    * {{ box-sizing:border-box }} body {{ margin:0; color:var(--text); background:radial-gradient(circle at 80% 0,#17345c 0,transparent 35%),var(--bg); font:15px/1.6 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif }}
    main {{ width:min(1180px,calc(100% - 32px)); margin:auto; padding:38px 0 64px }} header {{ display:flex; justify-content:space-between; gap:24px; align-items:end }}
    h1 {{ margin:0; font-size:clamp(30px,5vw,48px) }} h2 {{ margin:26px 0 12px }} h3 {{ margin:2px 0 10px }} .eyebrow,.path,.muted,.meta {{ color:var(--muted) }} .eyebrow,.path {{ margin:0; font-size:12px }} .meta {{ max-width:48%; text-align:right; font-size:12px; overflow-wrap:anywhere }}
    .summary {{ display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:10px; margin:10px 0 }} .summary-label {{ margin:22px 0 2px; color:var(--muted); font-size:12px; letter-spacing:.12em }} .metric,.card,.tabs,.filters,.table-wrap,.empty,.rank-overview,details {{ background:var(--panel); border:1px solid var(--line); border-radius:14px }}
    .metric {{ min-width:0; padding:14px; text-align:left }} .metric:hover,.metric:focus-visible {{ border-color:var(--gold); transform:translateY(-1px) }} .metric strong {{ display:block; color:var(--gold); font-size:25px }} .metric span {{ color:var(--muted); font-size:12px }} .tabs,.filters {{ display:flex; gap:8px; flex-wrap:wrap; padding:10px; margin-bottom:16px }}
    button,input {{ border:1px solid var(--line); border-radius:9px; padding:8px 11px; color:var(--text); background:#0b1624; font:inherit }} button {{ cursor:pointer }} button.active {{ color:var(--gold); border-color:var(--gold) }} input {{ flex:1; min-width:210px }} .manual-refresh {{ margin-top:7px; color:var(--gold); border-color:#78622f }}
    .grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:13px }} .card {{ padding:16px; position:relative; overflow:hidden }} .card-head {{ display:flex; justify-content:space-between; gap:12px; align-items:start }} .status {{ padding:3px 8px; border-radius:99px; background:#1c2b3d; white-space:nowrap; font-size:12px }}
    .status-measured {{ color:var(--green) }} .status-unmeasured {{ color:var(--muted) }}
    .skill-performance {{ display:flex; justify-content:space-between; gap:12px; align-items:end; margin:12px 0; padding:10px 12px; background:#0b1624; border-left:3px solid var(--gold); border-radius:3px 9px 9px 3px }} .skill-performance span {{ display:block; color:var(--muted); font-size:11px }} .skill-performance strong {{ color:var(--gold); font-size:22px; line-height:1.2 }} .skill-performance p {{ margin:0; color:var(--muted); font-size:12px; text-align:right }}
    .chips {{ display:flex; flex-wrap:wrap; gap:5px }} .chip {{ padding:3px 7px; border-radius:7px; font-size:12px }} .chip.done {{ color:#baf7cc; background:#143323 }} .chip.todo {{ color:#b5c0cd; background:#1a2736 }} .season-tag {{ position:absolute; right:0; bottom:0; padding:3px 9px; color:#08111d; background:var(--gold); font-size:11px; font-weight:700 }}
    details {{ padding:12px 16px; margin-bottom:16px }} summary {{ cursor:pointer; color:var(--gold); font-weight:700 }} .rank-overview {{ display:grid; grid-template-columns:1.2fr repeat(3,1fr); gap:14px; padding:18px }} .rank-now strong {{ display:block; color:var(--gold); font-size:28px }} .rank-stat span {{ display:block; color:var(--muted); font-size:12px }} .rank-stat strong {{ font-size:18px }}
    .rank {{ color:var(--gold); font-size:22px }} .medal {{ transition:border-color .2s,filter .2s }} .medal.locked {{ filter:grayscale(1); opacity:.58 }} .medal.unlocked {{ border-color:#78622f; background:linear-gradient(145deg,#182334,#322917) }} .medal-mark {{ width:64px; height:64px; display:grid; place-items:center; border:2px solid var(--gold); border-radius:50%; margin-bottom:12px; color:var(--gold); font-size:11px; font-weight:700 }} .medal-count {{ color:var(--gold); font-weight:700 }} .answer-text {{ padding:10px; background:#0b1624; border-radius:8px; white-space:pre-wrap }} .medal-progress {{ height:7px; background:#26364a; border-radius:99px; overflow:hidden }} .medal-progress i {{ display:block; height:100%; background:linear-gradient(90deg,var(--blue),var(--green)) }} .table-wrap {{ overflow:auto }} table {{ width:100%; border-collapse:collapse }} th,td {{ padding:10px; text-align:left; border-bottom:1px solid var(--line); white-space:nowrap }}
    .achievement-tabs {{ display:grid; grid-template-columns:repeat(5,1fr); gap:8px; margin-bottom:16px }} .achievement-tab {{ text-align:left }} .achievement-tab strong {{ display:block }} .achievement-page[hidden] {{ display:none!important }} .strength-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px }} .strength-track {{ margin-top:12px; padding:11px; background:#0b1624; border-left:3px solid var(--blue) }} .strength-track+ .strength-track {{ border-color:var(--green) }} .track-head {{ display:flex; justify-content:space-between; gap:10px; margin-bottom:8px; font-size:11px }} .track-head b {{ color:var(--gold) }} .medal-ladder {{ display:grid; grid-template-columns:repeat(5,1fr); gap:4px }} .medal-step {{ min-height:45px; padding:6px 2px; color:var(--muted); background:#1b2a3a; border:1px solid var(--line); text-align:center }} .medal-step b,.medal-step small {{ display:block }} .medal-step.level-1.earned {{ background:#70828c }} .medal-step.level-2.earned {{ background:#9b6a36 }} .medal-step.level-3.earned {{ background:#327ca1 }} .medal-step.level-4.earned {{ color:#111; background:#d6a322 }} .medal-step.level-5.earned {{ background:#a9423a }} .medal-step.next {{ color:var(--text); border:2px solid #d85b4e }} .wrong-controls,.record-controls {{ display:flex; gap:8px; align-items:center; margin-bottom:12px }} select {{ border:1px solid var(--line); border-radius:9px; padding:8px 11px; color:var(--text); background:#0b1624 }}
    .empty {{ grid-column:1/-1; padding:36px; text-align:center; color:var(--muted) }} .page[hidden],[hidden] {{ display:none!important }} @media(max-width:900px) {{ .summary {{ grid-template-columns:repeat(3,minmax(0,1fr)) }} .grid,.strength-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)) }} .rank-overview {{ grid-template-columns:repeat(2,minmax(0,1fr)) }} .achievement-tabs {{ grid-template-columns:repeat(3,1fr) }} }} @media(max-width:600px) {{ header {{ display:block }} .meta {{ max-width:100%; text-align:left;margin-top:10px }} .summary,.grid,.rank-overview,.strength-grid,.achievement-tabs {{ grid-template-columns:1fr }} }}
  </style>
  <style>
    :root {{ color-scheme:light; --bg:#e9eef1; --panel:#fbfcfd; --line:#ccd7dc; --text:#172c3b; --muted:#6d7e88; --gold:#a56c18; --blue:#226e96; --green:#2b745d; --red:#c34b3f; --paper-2:#f2f5f6 }}
    body {{ background:linear-gradient(rgba(41,69,87,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(41,69,87,.035) 1px,transparent 1px),var(--bg); background-size:24px 24px; font-family:"Microsoft YaHei","PingFang SC",sans-serif }}
    main {{ display:grid; grid-template-columns:230px minmax(0,1fr); width:min(1500px,calc(100% - 28px)); min-height:calc(100vh - 28px); margin:14px auto; padding:0; background:#fbfcfd; border:1px solid var(--line); box-shadow:0 13px 32px rgba(27,53,69,.085) }}
    main>header,main>.page {{ grid-column:2 }} main>.summary-label,main>.summary {{ display:none }}
    header {{ min-height:74px; padding:17px 30px; align-items:center; background:rgba(251,252,253,.96); border-bottom:1px solid var(--line) }}
    header h1 {{ font-size:17px }} header .eyebrow {{ color:var(--muted) }} .meta {{ color:var(--muted) }} .manual-refresh {{ color:#fff; background:var(--blue); border:0 }}
    .tabs {{ grid-column:1; grid-row:1/span 20; position:sticky; top:14px; align-self:start; display:grid; align-content:start; gap:4px; height:calc(100vh - 28px); margin:0; padding:28px 18px 20px; color:#f3f7f9; background:#172c3b; border:0; border-radius:0; overflow:auto }}
    .rail-brand {{ padding:0 9px 25px; margin-bottom:18px; border-bottom:1px solid rgba(255,255,255,.13) }} .rail-brand small {{ color:#8fb7cc; font:700 10px/1.2 Consolas,monospace; letter-spacing:.16em }} .rail-brand strong {{ display:block; margin:9px 0 5px; color:#fff; font:700 27px/1.1 "STZhongsong","SimSun",serif }} .rail-brand span {{ color:#9fb0ba; font-size:12px }}
    .tab-btn {{ display:grid; grid-template-columns:28px 1fr auto; align-items:center; gap:9px; width:100%; padding:11px 10px; color:#b7c5cc; text-align:left; background:transparent; border:0; border-radius:6px }} .tab-btn>span:first-child {{ color:#7693a3; font:700 11px/1 Consolas,monospace }} .tab-btn b {{ padding:2px 6px; color:#9fb0ba; background:rgba(255,255,255,.07); border-radius:99px; font:600 10px/1.4 Consolas,monospace }} .tab-btn.active {{ color:#fff; background:rgba(255,255,255,.11) }}
    .page {{ min-width:0; padding:28px 30px 56px }} .page-head {{ display:flex; justify-content:space-between; gap:24px; align-items:end; margin-bottom:20px }} .page-head .eyebrow {{ margin:0 0 6px; color:var(--blue); font:700 11px/1.2 Consolas,monospace; letter-spacing:.14em }} .page-head h2 {{ margin:0; font:700 clamp(29px,3vw,42px)/1.08 "STZhongsong","SimSun",serif }} .page-head>p {{ max-width:620px; margin:0; color:var(--muted); line-height:1.65; text-align:right }}
    .metric,.card,.filters,.table-wrap,.empty,.rank-overview,details {{ background:var(--panel); border:1px solid var(--line); border-radius:4px 4px 14px 4px; box-shadow:0 8px 22px rgba(27,53,69,.06) }}
    button,input,select {{ color:var(--text); background:var(--panel); border-color:var(--line) }} button.active {{ color:#fff; background:#294557; border-color:#294557 }}
    .grid {{ grid-template-columns:repeat(3,minmax(0,1fr)) }} .card {{ color:var(--text) }} .skill-performance,.strength-track,.answer-text {{ background:var(--paper-2) }} .chip.done {{ color:var(--green); background:#dcece6 }} .chip.todo,.status {{ color:var(--muted); background:#e5eaec }} .status-measured {{ color:var(--green) }}
    .today-grid {{ display:grid; grid-template-columns:minmax(0,1.35fr) minmax(290px,.65fr); gap:15px }} .mission {{ overflow:hidden }} .mission-head {{ padding:28px; color:#fff; background:#294557 }} .mission-head .label {{ color:#97c4da; font:700 11px/1 Consolas,monospace; letter-spacing:.14em }} .mission-head h3 {{ margin:10px 0 12px; font:700 28px/1.2 "STZhongsong","SimSun",serif }} .mission-head p {{ margin:0; color:#c5d3da; line-height:1.7 }} .mission-body,.next-action,.loop {{ padding:22px }} .mission-result {{ display:grid; grid-template-columns:repeat(4,1fr); gap:8px }} .mission-result div {{ padding:12px; background:var(--paper-2); border-left:3px solid var(--blue) }} .mission-result small {{ display:block; color:var(--muted); font-size:11px }} .mission-result strong {{ display:block; margin-top:4px; font:700 20px/1.1 Consolas,monospace }} .evidence-list {{ margin:17px 0 0; padding-left:20px; color:var(--muted); line-height:1.8 }}
    .next-action .stamp {{ display:grid; place-items:center; width:58px; height:58px; color:var(--red); border:2px solid var(--red); border-radius:50%; font:800 11px/1.1 Consolas,monospace; transform:rotate(-7deg) }} .next-action h3 {{ margin:19px 0 8px; font-size:22px }} .next-action p {{ color:var(--muted) }} .next-action .why {{ margin-top:16px; padding:12px; color:#fff; background:#294557; border-radius:3px 3px 11px 3px; font-size:13px }}
    .loop {{ margin-top:15px }} .loop-track {{ display:grid; grid-template-columns:repeat(6,1fr); gap:8px }} .loop-step {{ min-height:100px; padding:13px; background:var(--paper-2); border-top:4px solid var(--line) }} .loop-step.done {{ border-color:var(--green) }} .loop-step.current {{ border-color:var(--red); background:#f5dfdc }} .loop-step small,.loop-step p {{ color:var(--muted); font-size:11px }} .loop-step strong {{ display:block; margin:10px 0 5px }}
    .ability-summary {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin-bottom:14px }} .summary-card {{ padding:16px 18px }} .summary-card small {{ color:var(--muted) }} .summary-card strong {{ display:block; margin-top:7px; font:700 25px/1 Consolas,monospace }}
    .strength-track {{ border-left:3px solid var(--blue) }} .strength-track+.strength-track {{ border-color:var(--green) }} .medal-step {{ color:#87949b; background:#e3e8ea; border-color:#d4dde1 }} .medal-step.level-1.earned {{ background:#70828c }} .medal-step.level-2.earned {{ background:#9b6a36 }} .medal-step.level-3.earned {{ background:#327ca1 }} .medal-step.level-4.earned {{ color:#fff; background:#b27b14 }} .medal-step.level-5.earned {{ background:#a9423a }} .medal-step.next {{ color:var(--text); background:#fff; border:2px solid var(--red) }} .track-head b,.medal-count {{ color:var(--red) }}
    .achievement-tab.active {{ color:#c7d5dc; background:#294557; border-color:#294557 }} .medal.locked {{ opacity:.62 }} .medal.unlocked {{ border-color:#a7cdbf; background:#dcece6 }} .medal-mark {{ color:var(--blue); border-color:var(--blue) }} .medal-progress {{ background:#e0e6e9 }}
    .wrong-layout {{ display:grid; grid-template-columns:1fr 1fr; gap:14px }} .section-title {{ display:flex; justify-content:space-between; align-items:center; margin:18px 0 10px }} .record-controls,.wrong-controls {{ padding:12px 14px; background:var(--panel); border:1px solid var(--line); border-radius:4px }}
    @media(max-width:900px) {{ main {{ display:block; width:calc(100% - 16px); margin:8px auto }} .tabs {{ position:sticky; top:0; z-index:8; display:flex; height:auto; padding:12px; overflow-x:auto }} .rail-brand,.tab-btn>span:first-child,.tab-btn b {{ display:none }} .tab-btn {{ display:block; flex:0 0 auto; width:auto; white-space:nowrap }} header {{ display:flex }} .page {{ padding:22px 16px 42px }} .today-grid,.wrong-layout {{ grid-template-columns:1fr }} .loop-track {{ grid-template-columns:repeat(3,1fr) }} }}
    @media(max-width:600px) {{ .page-head {{ display:block }} .page-head>p {{ margin-top:8px; text-align:left }} .mission-result,.ability-summary,.grid,.strength-grid,.achievement-tabs {{ grid-template-columns:1fr }} .loop-track {{ grid-template-columns:repeat(2,1fr) }} }}
  </style>
</head><body><main>
  <header><div><p class="eyebrow">今天的训练重点</p><h1>{_escape(focus_module)} · {_escape(focus_name)}</h1></div>
    <div class="meta">计划出勤 {effective_days}/{planned_days} · 练习主样本 {sum(item[2] for item in ability_samples if item[0] != 'shenlun')}题 · 错题 {len(state.get("wrong_answers", []))}<br>数据更新：{_escape(updated_at)} · 每天 08:00 自动刷新<br><button id="manual-refresh" class="manual-refresh" type="button">手动刷新</button><br>数据文件：{_escape(source_path)} · 页面生成：{_escape(generated_at)}</div></header>
  <p class="summary-label">技能全貌</p><section class="summary">
    <button class="metric jump" data-tab="skills" data-filter="all"><strong>{total}</strong><span>技能总数</span></button>
    <button class="metric jump" data-tab="skills" data-filter="measured"><strong>{measured_skills}</strong><span>已有实测技能</span></button>
    <button class="metric jump" data-tab="skills" data-filter="unmeasured"><strong>{total - measured_skills}</strong><span>暂无实测技能</span></button>
    <button class="metric jump" data-tab="records"><strong>{len(practice_records)}</strong><span>练习战绩</span></button>
    <button class="metric jump" data-tab="records"><strong>{measured_modules}/5</strong><span>有量化记录模块</span></button>
  </section>
  <p class="summary-label">学习记录</p><section class="summary">
    <button class="metric jump" data-tab="wrongs"><strong>{len(state.get("wrong_answers", []))}</strong><span>错题</span></button>
    <button class="metric jump" data-tab="wrongs"><strong>{open_easy_points}</strong><span>未解决易错点</span></button>
    <button class="metric jump" data-tab="records"><strong>{len(state.get("assessments", []))}</strong><span>有效战绩</span></button>
    <button class="metric jump" data-tab="medals"><strong>{unlocked_medals}/{len(fixed_medals)}</strong><span>已点亮勋章</span></button>
    <button class="metric jump" data-tab="records"><strong>{_escape(rank)} {"★" * stars}</strong><span>本赛季段位 · 调整点 {adjustment}/{adjustment_cap}</span></button>
  </section>
  <nav class="tabs">
    <div class="rail-brand"><small>GONGKAO DOSSIER</small><strong>备考卷宗</strong><span>第 {_escape(season.get("number", 1))} 赛季 · {_escape(season.get("theme") or season.get("phase") or "校准期")}</span></div>
    <button class="tab-btn active" data-tab="today"><span>01</span>今日任务<b>{_escape(task_status)}</b></button>
    <button class="tab-btn" data-tab="ability"><span>02</span>能力分析<b>{sampled_abilities}有样本</b></button>
    <button class="tab-btn" data-tab="skills"><span>03</span>技能地图<b>{measured_skills}/{total}</b></button>
    <button class="tab-btn" data-tab="records"><span>04</span>练习记录<b>{len(practice_records)}</b></button>
    <button class="tab-btn" data-tab="wrongs"><span>05</span>错题本<b>{len(state.get("wrong_answers", []))}</b></button>
    <button class="tab-btn" data-tab="answers"><span>06</span>申论答题本<b>{len(state.get("shenlun_portfolio", []))}</b></button>
    <button class="tab-btn" data-tab="rank"><span>07</span>战绩段位<b>{_escape(rank)}</b></button>
    <button class="tab-btn" data-tab="medals"><span>08</span>成就墙<b>{unlocked_medals}</b></button>
  </nav>
  <section class="page" data-page="today"><div class="page-head"><div><p class="eyebrow">TODAY / 行动入口</p><h2>今天只管把下一步做对</h2></div><p>系统规划一个主任务，自主练习不限量。提交后统一进入记录、诊断、纠错和成就结算。</p></div><div class="today-grid"><article class="card mission"><div class="mission-head"><span class="label">今日主任务 · {_escape(task_status)}</span><h3>{_escape(task_title)}</h3><p>{_escape(accepted.get("content") or "尚未生成任务内容。")}</p></div><div class="mission-body"><div class="mission-result"><div><small>题量</small><strong>{_escape(task_result[0])}</strong></div><div><small>正确</small><strong>{_escape(task_result[1])}</strong></div><div><small>正确率</small><strong>{_escape(task_result[2])}</strong></div><div><small>实际用时</small><strong>{_escape(task_result[3])}</strong></div></div><ul class="evidence-list">{evidence_html}</ul></div></article><aside class="card next-action"><div class="stamp">下一步<br>建议</div><h3>{_escape(next_title)}</h3><p>{_escape(next_reason)}</p><div class="why">建议任务：{next_count}{'篇' if next_ability[0] == 'shenlun' else '题'} {_escape(next_ability[1])}，先补足长期样本；有实际用时就同时推进速度战线，没有也照常记录正确率。</div></aside></div><article class="card loop"><div class="section-title"><h3>一次练习怎样进入备考系统</h3><span class="muted">每一步都保留证据</span></div><div class="loop-track"><div class="loop-step done"><small>01</small><strong>规划任务</strong><p>根据当前弱点选训练内容</p></div><div class="loop-step done"><small>02</small><strong>提交练习</strong><p>任务、自主练习或全卷模拟</p></div><div class="loop-step done"><small>03</small><strong>分类记录</strong><p>题型、题量、正确数和用时</p></div><div class="loop-step done"><small>04</small><strong>更新诊断</strong><p>能力、技能、错题和申论</p></div><div class="loop-step current"><small>05</small><strong>即时结算</strong><p>纪录、成就、样本变化</p></div><div class="loop-step"><small>06</small><strong>安排下一步</strong><p>正确率优先或转入限时</p></div></div></article></section>
  <section class="page" data-page="ability" hidden><div class="page-head"><div><p class="eyebrow">DIAGNOSIS / 双战线</p><h2>强项看段位，弱项也能看见进步</h2></div><p>11项能力分别计算正确率和速度。实力档位永久保留，成长成就只奖励互不重叠窗口之间的真实提升。</p></div><div class="ability-summary"><div class="card summary-card"><small>正确率已有样本</small><strong>{sampled_abilities}/11项</strong></div><div class="card summary-card"><small>速度已有样本</small><strong>{timed_abilities}/11项</strong></div><div class="card summary-card"><small>最接近补齐样本</small><strong>{_escape(next_ability[1])}</strong></div></div><div class="strength-grid">{strength_board}</div></section>
  <section class="page" data-page="skills" hidden><div class="page-head"><div><p class="eyebrow">SKILL MAP / 学习覆盖</p><h2>解锁表示练过，不表示掌握</h2></div><p>70项标准技能永久保留。技能负责组织知识，能力分析负责判断历史表现。</p></div><details><summary>数据口径</summary><p>用户提交的日常练习、任务练习和全卷模拟均按原始题量、正确数与实际用时记录；缺少用时只参与正确率战线，不参与速度战线。</p></details><div class="filters">
    <button class="filter-btn active" data-filter="all">全部 {total}</button>
    <button class="filter-btn" data-filter="measured">有实测 {measured_skills}</button>
    <button class="filter-btn" data-filter="unmeasured">待记录 {total - measured_skills}</button><button class="filter-btn" data-filter="current">本赛季重点 {len(current_ids)}</button>
    <input id="skill-search" type="search" placeholder="搜索科目、模块或技能"></div>
    <div class="grid" id="skill-grid">{skills or _empty("技能目录尚未建立。完成季前校准后再生成总览。")}</div></section>
  <section class="page" data-page="wrongs" hidden><div class="page-head"><div><p class="eyebrow">CORRECTION / 错因追踪</p><h2>错题保存题，易错点保存机制</h2></div><p>两者分开记录。错题负责订正和复测，易错点负责识别反复出现的错误模式。</p></div><p class="muted">一级分类固定为资料分析、数量关系、言语理解、判断推理、常识判断和申论。新题型只增加标签，不增加大类。</p><div class="wrong-controls"><select id="wrong-module"><option value="all">全部大类</option>{''.join(f'<option value="{_escape(name)}">{_escape(name)}</option>' for name in ('资料分析','数量关系','言语理解','判断推理','常识判断','申论'))}</select><select id="wrong-question"><option value="all">全部题目</option>{''.join(f'<option value="{_escape(item.get("wrong_id"))}" data-module="{_escape(item.get("module"))}">{_escape(item.get("question_ref") or item.get("wrong_id"))}</option>' for item in state.get('wrong_answers', []))}</select></div><div class="wrong-layout"><section><div class="section-title"><h3>错题本 · {len(state.get("wrong_answers", []))}题</h3></div><div class="grid" id="wrong-grid">{wrongs or _empty("暂无错题记录。")}</div></section><section><div class="section-title"><h3>易错点 · {len(state.get("error_hunts", []))}项</h3><span class="muted">{open_easy_points}项待处理</span></div><div class="grid">{easy_points or _empty("暂无易错点。")}</div></section></div></section>
  <section class="page" data-page="records" hidden><div class="page-head"><div><p class="eyebrow">LEDGER / 原始账本</p><h2>练过什么，先如实记下来</h2></div><p>任务练习、自主练习和全卷模拟进入同一账本。缺少用时不会阻止保存正确率，但不能参与速度统计。</p></div><div class="record-controls"><label for="record-sort">当前共 {len(practice_records)} 条记录 · 排序</label><select id="record-sort"><option value="newest">日期从新到旧</option><option value="oldest">日期从旧到新</option><option value="accuracy-desc">正确率从高到低</option><option value="accuracy-asc">正确率从低到高</option><option value="type">按记录类型</option></select></div><div class="table-wrap"><table><thead><tr><th>日期</th><th>模块</th><th>正确题数</th><th>正确率</th><th>总用时</th><th>平均每题</th><th>用途</th></tr></thead><tbody id="practice-body">{practices or '<tr><td colspan="7">暂无练习战绩。</td></tr>'}</tbody></table></div></section>
  <section class="page" data-page="answers" hidden><div class="page-head"><div><p class="eyebrow">SHENLUN / 作答档案</p><h2>原答案、评分和修改方向放在一起</h2></div><p>AI内部单题评分只用于训练反馈，不改变排位段位。完整外部评分另行入账。</p></div><div class="grid">{answers or _empty("暂无申论作答。")}</div></section>
  <section class="page" data-page="rank" hidden><div class="page-head"><div><p class="eyebrow">RANK / 完整考试</p><h2>段位只看可比的全卷成绩</h2></div><p>日常专项练习更新能力，不直接改变段位；历史成绩只作为基线。</p></div><div class="rank-overview"><div class="rank-now"><span class="path">本赛季</span><strong>{_escape(rank)} {"★" * stars}</strong></div><div class="rank-stat"><span>上赛季</span><strong>{_escape(previous_rank)} {"★" * previous_stars}</strong></div><div class="rank-stat"><span>历史最高</span><strong>{_escape(highest_rank)}</strong></div><div class="rank-stat"><span>定级进度</span><strong>行测 {_escape(placement.get("xingce_current", 0))}/{_escape(placement.get("xingce_target", 2))} · 申论 {_escape(placement.get("shenlun_current", 0))}/{_escape(placement.get("shenlun_target", 2))}</strong></div></div><h2>模块与科目段位</h2><div class="grid">{rankings or _empty("本赛季有效样本不足，当前未定级。")}</div><h2>战绩</h2><div class="table-wrap"><table><thead><tr><th>日期</th><th>科目</th><th>范围</th><th>成绩</th><th>来源</th><th>用途</th></tr></thead><tbody>{assessments or '<tr><td colspan="6">暂无战绩。</td></tr>'}</tbody></table></div></section>
  <section class="page" data-page="medals" hidden><div class="page-head"><div><p class="eyebrow">ACHIEVEMENTS / 五类反馈</p><h2>达标有勋章，变好也有奖励</h2></div><p>实力勋章证明达到过什么水平，成长成就记录从哪里提升上来；两者分开后，弱项不会长期空白。</p></div><div class="achievement-tabs"><button class="achievement-tab active" data-achievement="single"><strong>单次战绩</strong>25枚，可累计</button><button class="achievement-tab" data-achievement="strength"><strong>实力勋章</strong>11项双战线</button><button class="achievement-tab" data-achievement="growth"><strong>成长成就</strong>奖励真实提升</button><button class="achievement-tab" data-achievement="career"><strong>生涯成就</strong>升星与里程碑</button><button class="achievement-tab" data-achievement="season"><strong>赛季成就</strong>本赛季进度</button></div><div class="achievement-page" data-achievement-page="single"><div class="grid">{single_medals}</div></div><div class="achievement-page" data-achievement-page="strength" hidden><p class="muted">每条战线五档。已获得档位永久保留，彩色代表已达成，红框标出下一档，灰色显示未来目标。</p><div class="strength-grid">{strength_board}</div></div><div class="achievement-page" data-achievement-page="growth" hidden><p class="muted">正确率提升5、10、15个百分点，速度提升10%、20%、30%。速度奖励要求正确率下降不超过5个百分点。</p><div class="grid">{growth_medals}</div></div><div class="achievement-page" data-achievement-page="career" hidden><div class="grid">{career_medals}</div></div><div class="achievement-page" data-achievement-page="season" hidden><div class="grid">{season_medals}</div></div></section>
</main><script>
  const tabs=[...document.querySelectorAll('.tab-btn')]; const pages=[...document.querySelectorAll('.page')];
  function openTab(name){{tabs.forEach(x=>x.classList.toggle('active',x.dataset.tab===name));pages.forEach(page=>page.hidden=page.dataset.page!==name);}}
  tabs.forEach(button=>button.addEventListener('click',()=>openTab(button.dataset.tab)));
  const cards=[...document.querySelectorAll('.skill-card')]; const filters=[...document.querySelectorAll('.filter-btn')]; const search=document.querySelector('#skill-search'); let filter='all';
  function apply(){{const q=search.value.trim().toLowerCase();cards.forEach(card=>{{const status=card.dataset.status;const match=filter==='all'||status===filter||(filter==='current'&&card.dataset.current==='true');card.hidden=!(match&&card.dataset.search.includes(q));}});}}
  function setSkillFilter(value){{filter=value;filters.forEach(x=>x.classList.toggle('active',x.dataset.filter===value));apply();}}
  filters.forEach(button=>button.addEventListener('click',()=>setSkillFilter(button.dataset.filter))); search.addEventListener('input',apply);
  document.querySelectorAll('.jump').forEach(button=>button.addEventListener('click',()=>{{openTab(button.dataset.tab);if(button.dataset.filter)setSkillFilter(button.dataset.filter);document.querySelector('.tabs').scrollIntoView({{behavior:'smooth'}});}}));
  const achievementTabs=[...document.querySelectorAll('.achievement-tab')]; const achievementPages=[...document.querySelectorAll('.achievement-page')];
  achievementTabs.forEach(button=>button.addEventListener('click',()=>{{achievementTabs.forEach(x=>x.classList.toggle('active',x===button));achievementPages.forEach(page=>page.hidden=page.dataset.achievementPage!==button.dataset.achievement);}}));
  const wrongModule=document.querySelector('#wrong-module'); const wrongQuestion=document.querySelector('#wrong-question'); const wrongCards=[...document.querySelectorAll('.wrong-card')];
  function applyWrongFilter(){{const module=wrongModule.value;const question=wrongQuestion.value;wrongCards.forEach(card=>card.hidden=!(module==='all'||card.dataset.wrongModule===module)||!(question==='all'||card.dataset.wrongId===question));[...wrongQuestion.options].forEach((option,index)=>{{if(index)option.hidden=module!=='all'&&option.dataset.module!==module;}});}}
  wrongModule.addEventListener('change',()=>{{wrongQuestion.value='all';applyWrongFilter();}}); wrongQuestion.addEventListener('change',applyWrongFilter);
  const recordSort=document.querySelector('#record-sort'); const practiceBody=document.querySelector('#practice-body');
  recordSort.addEventListener('change',()=>{{const rows=[...practiceBody.querySelectorAll('tr[data-date]')];rows.sort((a,b)=>{{if(recordSort.value==='oldest')return a.dataset.date.localeCompare(b.dataset.date);if(recordSort.value==='accuracy-desc')return Number(b.dataset.accuracy)-Number(a.dataset.accuracy);if(recordSort.value==='accuracy-asc')return Number(a.dataset.accuracy)-Number(b.dataset.accuracy);if(recordSort.value==='type')return a.dataset.type.localeCompare(b.dataset.type);return b.dataset.date.localeCompare(a.dataset.date);}});rows.forEach(row=>practiceBody.appendChild(row));}});
  function requestRefresh(){{window.location.search='refresh=1';}}
  if(new URLSearchParams(window.location.search).get('refresh')==='1')history.replaceState({{}},'',window.location.pathname);
  document.querySelector('#manual-refresh').addEventListener('click',requestRefresh);
  const nextRefresh=new Date(); nextRefresh.setHours(8,0,0,0); if(nextRefresh<=new Date())nextRefresh.setDate(nextRefresh.getDate()+1);
  setTimeout(requestRefresh,nextRefresh-new Date());
</script></body></html>"""


def build_report(state_path: Path, output_path: Path) -> None:
    state = read_current_state(state_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_html(state, source_path=state_path), encoding="utf-8")


def watch_report(state_path: Path, output_path: Path, interval: float) -> None:
    previous_mtime: int | None = None
    while True:
        current_mtime = state_path.stat().st_mtime_ns
        if current_mtime != previous_mtime:
            build_report(state_path, output_path)
            previous_mtime = current_mtime
            print(f"已更新备考总览：{output_path}", flush=True)
        time.sleep(interval)


def create_server(
    state_path: Path,
    output_path: Path,
    host: str,
    port: int,
    now_provider: Callable[[], datetime] | None = None,
) -> HTTPServer:
    """创建只读总览服务；每天 08:00 或用户明确要求时重建页面。"""
    current_time = now_provider or (lambda: datetime.now().astimezone())
    last_daily_refresh: str | None = None

    def refresh(*, force: bool = False) -> None:
        nonlocal last_daily_refresh
        now = current_time()
        today = now.date().isoformat()
        daily_refresh_due = (now.hour, now.minute) >= (8, 0)
        if (
            force
            or not output_path.exists()
            or (daily_refresh_due and last_daily_refresh != today)
        ):
            build_report(state_path, output_path)
            if daily_refresh_due:
                last_daily_refresh = today

    class DashboardHandler(BaseHTTPRequestHandler):
        def _send_dashboard(self, *, include_body: bool) -> None:
            request = urlsplit(self.path)
            request_path = request.path
            if request_path not in {"/", f"/{quote(output_path.name)}"}:
                self.send_error(404, "Not Found")
                return
            manual_refresh = parse_qs(request.query).get("refresh") == ["1"]
            try:
                refresh(force=manual_refresh)
                content = output_path.read_bytes()
            except (OSError, StateError, ValueError) as exc:
                self.log_error("dashboard refresh failed: %s", exc)
                self.send_error(500, "Dashboard Update Failed")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            if include_body:
                self.wfile.write(content)

        def do_GET(self) -> None:
            self._send_dashboard(include_body=True)

        def do_HEAD(self) -> None:
            self._send_dashboard(include_body=False)

        def log_message(self, format: str, *args: object) -> None:
            return

    refresh(force=True)
    return HTTPServer((host, port), DashboardHandler)


def serve_report(state_path: Path, output_path: Path, host: str, port: int) -> None:
    server = create_server(state_path, output_path, host, port)
    actual_host, actual_port = server.server_address[:2]
    print(
        json.dumps(
            {
                "url": f"http://{actual_host}:{actual_port}/",
                "output": str(output_path),
                "state": str(state_path),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成公考备考总览 HTML")
    parser.add_argument("--state-path", help="显式指定 state.json")
    parser.add_argument("--output", help="输出路径，默认与 state.json 同目录")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--watch", action="store_true", help="监听状态变化并自动重建")
    mode.add_argument("--serve", action="store_true", help="启动只读 HTTP 总览服务")
    parser.add_argument("--interval", type=float, default=2.0, help="监听间隔秒数")
    parser.add_argument("--host", default="127.0.0.1", help="服务监听地址")
    parser.add_argument("--port", type=int, default=8080, help="服务监听端口")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    state_path, _ = resolve_state_path(args.state_path)
    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else state_path.with_name("dashboard.html")
    )
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port 必须位于 1 至 65535。")
    if args.serve:
        serve_report(state_path, output_path, args.host, args.port)
        return 0
    build_report(state_path, output_path)
    print(
        json.dumps(
            {"output": str(output_path), "state": str(state_path)}, ensure_ascii=False
        )
    )
    if args.watch:
        watch_report(state_path, output_path, args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
