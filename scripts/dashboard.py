"""
版本记录：
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
    from .catalogs import default_medals
    from .state_store import StateError, read_current_state, resolve_state_path
except ImportError:  # 直接运行 scripts/dashboard.py 时没有包上下文
    from catalogs import default_medals
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
    <article class="card">
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
    purpose = "可判定勋章" if item.get("locked_before_start") else "仅记录"
    return f"""
    <tr><td>{_escape(item.get("date"))}</td><td>{_escape(item.get("module"))}</td>
    <td>{_escape(item.get("correct_count"))}/{_escape(item.get("question_count"))}</td>
    <td>{_escape(_format_percent(item.get("accuracy_rate")))}</td>
    <td>{_escape(item.get("duration_seconds"))} 秒</td>
    <td>{_escape(item.get("seconds_per_question"))} 秒</td>
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
      <p class="medal-count">{_escape(current)}/{_escape(target)} {_escape(item.get("progress_unit", "项"))}</p>
      <p class="muted">证据 {evidence_count} 条 · 点亮时间：{_escape(_text(item.get("unlocked_at"), "尚未点亮"))}</p>
    </article>"""


def _empty(message: str) -> str:
    return f'<div class="empty">{_escape(message)}</div>'


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
    medals = "".join(_render_medal(item) for item in medal_items)

    standard = [item for item in catalog if item.get("tier") == "standard"]
    total = len(standard)
    measured_skills = sum(bool(item.get("recent_performance")) for item in standard)
    measured_modules = len({item.get("module") for item in practice_records})
    unlocked_medals = sum(item.get("status") == "unlocked" for item in fixed_medals)
    all_unlocked_medals = sum(item.get("status") == "unlocked" for item in medal_items)
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
    .empty {{ grid-column:1/-1; padding:36px; text-align:center; color:var(--muted) }} .page[hidden],[hidden] {{ display:none!important }} @media(max-width:900px) {{ .summary {{ grid-template-columns:repeat(3,minmax(0,1fr)) }} .grid {{ grid-template-columns:repeat(2,minmax(0,1fr)) }} .rank-overview {{ grid-template-columns:repeat(2,minmax(0,1fr)) }} }} @media(max-width:600px) {{ header {{ display:block }} .meta {{ max-width:100%; text-align:left;margin-top:10px }} .summary,.grid,.rank-overview {{ grid-template-columns:repeat(2,minmax(0,1fr)) }} }}
  </style>
</head><body><main>
  <header><div><p class="eyebrow">GONGKAO SEASON · 第 {_escape(season.get("number", 1))} 赛季</p><h1>备考总览</h1></div>
    <div class="meta">数据更新：{_escape(updated_at)}<br>页面生成：{_escape(generated_at)}<br>每天 08:00 自动刷新<br><button id="manual-refresh" class="manual-refresh" type="button">手动刷新</button><br>数据文件：{_escape(source_path)}</div></header>
  <p class="summary-label">技能全貌</p><section class="summary">
    <button class="metric jump" data-tab="skills" data-filter="all"><strong>{total}</strong><span>技能总数</span></button>
    <button class="metric jump" data-tab="skills" data-filter="measured"><strong>{measured_skills}</strong><span>已有实测技能</span></button>
    <button class="metric jump" data-tab="skills" data-filter="unmeasured"><strong>{total - measured_skills}</strong><span>暂无实测技能</span></button>
    <button class="metric jump" data-tab="records"><strong>{len(practice_records)}</strong><span>练习战绩</span></button>
    <button class="metric jump" data-tab="records"><strong>{measured_modules}/5</strong><span>有量化记录模块</span></button>
  </section>
  <p class="summary-label">学习记录</p><section class="summary">
    <button class="metric jump" data-tab="wrongs"><strong>{len(state.get("wrong_answers", []))}</strong><span>错题</span></button>
    <button class="metric jump" data-tab="easy-points"><strong>{open_easy_points}</strong><span>未解决易错点</span></button>
    <button class="metric jump" data-tab="records"><strong>{len(state.get("assessments", []))}</strong><span>有效战绩</span></button>
    <button class="metric jump" data-tab="medals"><strong>{unlocked_medals}/{len(fixed_medals)}</strong><span>已点亮勋章</span></button>
    <button class="metric jump" data-tab="records"><strong>{_escape(rank)} {"★" * stars}</strong><span>本赛季段位 · 调整点 {adjustment}/{adjustment_cap}</span></button>
  </section>
  <nav class="tabs">
    <button class="tab-btn active" data-tab="skills">技能总览</button><button class="tab-btn" data-tab="wrongs">错题本</button>
    <button class="tab-btn" data-tab="easy-points">易错点</button><button class="tab-btn" data-tab="records">战绩</button>
    <button class="tab-btn" data-tab="answers">申论答题册</button><button class="tab-btn" data-tab="medals">勋章墙</button>
  </nav>
  <section class="page" data-page="skills"><details><summary>数据口径</summary><p>技能页只展示真实练习数据，不再使用“考场可用”等主观标签。模块量化勋章要求单次锁定练习至少 10 题，同时达到规定正确率和平均每题用时；缺少实际用时不判定。</p></details><div class="filters">
    <button class="filter-btn active" data-filter="all">全部 {total}</button>
    <button class="filter-btn" data-filter="measured">有实测 {measured_skills}</button>
    <button class="filter-btn" data-filter="unmeasured">待记录 {total - measured_skills}</button><button class="filter-btn" data-filter="current">本赛季重点 {len(current_ids)}</button>
    <input id="skill-search" type="search" placeholder="搜索科目、模块或技能"></div>
    <div class="grid" id="skill-grid">{skills or _empty("技能目录尚未建立。完成季前校准后再生成总览。")}</div></section>
  <section class="page" data-page="wrongs" hidden><h2>错题本</h2><div class="grid">{wrongs or _empty("暂无错题记录。")}</div></section>
  <section class="page" data-page="easy-points" hidden><h2>易错点</h2><div class="grid">{easy_points or _empty("暂无易错点。")}</div></section>
  <section class="page" data-page="records" hidden><h2>练习战绩</h2><div class="table-wrap"><table><thead><tr><th>日期</th><th>模块</th><th>正确题数</th><th>正确率</th><th>总用时</th><th>平均每题</th><th>用途</th></tr></thead><tbody>{practices or '<tr><td colspan="7">暂无同时包含题量、正确数和实际用时的练习战绩。</td></tr>'}</tbody></table></div><h2>段位定级</h2><div class="rank-overview"><div class="rank-now"><span class="path">本赛季</span><strong>{_escape(rank)} {"★" * stars}</strong></div><div class="rank-stat"><span>上赛季</span><strong>{_escape(previous_rank)} {"★" * previous_stars}</strong></div><div class="rank-stat"><span>历史最高</span><strong>{_escape(highest_rank)}</strong></div><div class="rank-stat"><span>定级进度</span><strong>行测 {_escape(placement.get("xingce_current", 0))}/{_escape(placement.get("xingce_target", 2))} · 申论 {_escape(placement.get("shenlun_current", 0))}/{_escape(placement.get("shenlun_target", 2))}</strong></div></div><h2>模块与科目段位</h2><div class="grid">{rankings or _empty("本赛季有效样本不足，当前未定级。")}</div>
    <h2>战绩</h2><div class="table-wrap"><table><thead><tr><th>日期</th><th>科目</th><th>范围</th><th>成绩</th><th>来源</th><th>用途</th></tr></thead><tbody>{assessments or '<tr><td colspan="6">暂无战绩。</td></tr>'}</tbody></table></div></section>
  <section class="page" data-page="answers" hidden><h2>申论答题册</h2><div class="grid">{answers or _empty("暂无申论作答。")}</div></section>
  <section class="page" data-page="medals" hidden><h2>勋章墙</h2><div class="filters"><button class="medal-filter active" data-medal-filter="all">全部 {len(medal_items)}</button><button class="medal-filter" data-medal-filter="locked">未点亮 {len(medal_items) - all_unlocked_medals}</button><button class="medal-filter" data-medal-filter="unlocked">已点亮 {all_unlocked_medals}</button></div><div class="grid">{medals}</div></section>
</main><script>
  const tabs=[...document.querySelectorAll('.tab-btn')]; const pages=[...document.querySelectorAll('.page')];
  function openTab(name){{tabs.forEach(x=>x.classList.toggle('active',x.dataset.tab===name));pages.forEach(page=>page.hidden=page.dataset.page!==name);}}
  tabs.forEach(button=>button.addEventListener('click',()=>openTab(button.dataset.tab)));
  const cards=[...document.querySelectorAll('.skill-card')]; const filters=[...document.querySelectorAll('.filter-btn')]; const search=document.querySelector('#skill-search'); let filter='all';
  function apply(){{const q=search.value.trim().toLowerCase();cards.forEach(card=>{{const status=card.dataset.status;const match=filter==='all'||status===filter||(filter==='current'&&card.dataset.current==='true');card.hidden=!(match&&card.dataset.search.includes(q));}});}}
  function setSkillFilter(value){{filter=value;filters.forEach(x=>x.classList.toggle('active',x.dataset.filter===value));apply();}}
  filters.forEach(button=>button.addEventListener('click',()=>setSkillFilter(button.dataset.filter))); search.addEventListener('input',apply);
  document.querySelectorAll('.jump').forEach(button=>button.addEventListener('click',()=>{{openTab(button.dataset.tab);if(button.dataset.filter)setSkillFilter(button.dataset.filter);document.querySelector('.tabs').scrollIntoView({{behavior:'smooth'}});}}));
  const medalCards=[...document.querySelectorAll('.medal')]; const medalFilters=[...document.querySelectorAll('.medal-filter')];
  medalFilters.forEach(button=>button.addEventListener('click',()=>{{const value=button.dataset.medalFilter;medalFilters.forEach(x=>x.classList.toggle('active',x===button));medalCards.forEach(card=>card.hidden=value!=='all'&&card.dataset.medalStatus!==value);}}));
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
