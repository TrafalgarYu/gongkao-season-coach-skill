"""
版本记录：
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
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

try:
    from .state_store import StateError, read_current_state, resolve_state_path
except ImportError:  # 直接运行 scripts/dashboard.py 时没有包上下文
    from state_store import StateError, read_current_state, resolve_state_path

PROFICIENCY_LABELS = {
    "silhouette": "未开始",
    "discovered": "练习中",
    "owned": "考场可用",
    "mastered": "稳定掌握",
}
STATUS_ORDER = {"mastered": 0, "owned": 1, "discovered": 2, "silhouette": 3}
CHECK_LABELS = {
    "base": "基础",
    "timed": "限时",
    "mixed": "混合",
    "retained": "延迟复测",
    "structure": "结构",
    "compressed": "限字",
    "transfer": "新材料",
}
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


def _skill_progress(item: dict[str, Any]) -> tuple[int, int]:
    checks = item.get("forms")
    if not isinstance(checks, dict) or not checks:
        return (1 if item.get("status") in {"owned", "mastered"} else 0, 1)
    return sum(value is True for value in checks.values()), len(checks)


def _skill_next_step(item: dict[str, Any]) -> str:
    checks = item.get("forms")
    if isinstance(checks, dict):
        missing = [
            CHECK_LABELS.get(key, key)
            for key, value in checks.items()
            if value is not True
        ]
        if missing:
            return f"还需完成：{'、'.join(missing)}"
    if item.get("status") == "mastered":
        return "已经稳定掌握"
    return f"熟练度标准：{_text(item.get('thresholds'), '尚未设置')}"


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
    status = str(item.get("status", "silhouette"))
    completed, total = _skill_progress(item)
    percent = round(completed / total * 100) if total else 0
    checks = item.get("forms") if isinstance(item.get("forms"), dict) else {}
    check_html = "".join(
        f'<span class="chip {"done" if done is True else "todo"}">'
        f"{'✓' if done is True else '○'} "
        f"{_escape(CHECK_LABELS.get(key, key))}</span>"
        for key, done in checks.items()
    )
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
      </div><span class="status status-{_escape(status)}">{_escape(PROFICIENCY_LABELS.get(status, status))}</span></div>
      <div class="progress"><i style="width:{percent}%"></i></div>
      <p class="muted">熟练度检查 {completed}/{total}</p>
      <div class="skill-performance"><div><span>{_escape(metric_label)}</span>
        <strong>{_escape(metric_value)}</strong></div><p>{_escape(evidence_basis)}</p></div>
      <div class="chips">{check_html or '<span class="chip todo">○ 待设置检查项</span>'}</div>
      <p>{_escape(_skill_next_step(item))}</p>
      <p class="muted">标准：{_escape(_text(item.get("thresholds"), "尚未设置"))}</p>
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
    return f"""
    <tr><td>{_escape(item.get("date"))}</td><td>{_escape(item.get("subject"))}</td>
    <td>{_escape(item.get("scope"))}</td><td>{_escape(_text(item.get("score")))}</td>
    <td>{_escape(item.get("score_source"))}</td><td>{ranked}</td></tr>"""


def _render_answer(item: dict[str, Any]) -> str:
    return f"""
    <article class="card">
      <p class="path">{_escape(item.get("date"))} · {_escape(item.get("task_type"))}</p>
      <h3>{_escape(item.get("prompt_ref") or "未命名作答")}</h3>
      <p>得分：{_escape(_text(item.get("score")))} · 来源：{_escape(_text(item.get("score_source")))}</p>
      <p class="muted">批改维度：{_escape(_text(item.get("dimensions")))}</p>
    </article>"""


def _render_medal(item: dict[str, Any]) -> str:
    unlocked = item.get("status") == "unlocked"
    return f"""
    <article class="card medal {"unlocked" if unlocked else "locked"}">
      <span class="medal-icon">{"●" if unlocked else "○"}</span>
      <h3>{_escape(item.get("name") or "未命名勋章")}</h3>
      <p>{_escape(_text(item.get("description")))}</p>
      <p class="muted">条件：{_escape(_text(item.get("condition"), "尚未设置"))}</p>
    </article>"""


def _empty(message: str) -> str:
    return f'<div class="empty">{_escape(message)}</div>'


def render_html(state: dict[str, Any], *, source_path: Path) -> str:
    catalog = [item for item in state.get("catalog", []) if isinstance(item, dict)]
    catalog.sort(
        key=lambda item: (
            STATUS_ORDER.get(str(item.get("status")), 99),
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
    answers = "".join(
        _render_answer(item) for item in state.get("shenlun_portfolio", [])
    )
    medals = "".join(_render_medal(item) for item in state.get("medals", []))

    total = len(catalog)
    usable = sum(item.get("status") in {"owned", "mastered"} for item in catalog)
    mastered = sum(item.get("status") == "mastered" for item in catalog)
    practicing = sum(item.get("status") == "discovered" for item in catalog)
    not_started = total - usable - practicing
    medal_items = state.get("medals", [])
    unlocked_medals = sum(item.get("status") == "unlocked" for item in medal_items)
    open_easy_points = sum(
        item.get("status") != "sealed" for item in state.get("error_hunts", [])
    )
    adjustment = state.get("economy", {}).get("command_points", 0)
    adjustment_cap = state.get("economy", {}).get("command_points_cap", 0)
    rank = state.get("season", {}).get("rank", "未定级")
    stars = state.get("season", {}).get("stars", 0)
    updated_at = state.get("engine", {}).get("updated_at") or "尚未记录"
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")

    return f"""<!doctype html>
<html lang="zh-CN"><head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="refresh" content="15"><title>公考备考总览</title>
  <style>
    :root {{ color-scheme:dark; --bg:#08111d; --panel:#111d2b; --line:#26364a;
      --text:#edf5ff; --muted:#91a3b8; --gold:#ffc857; --blue:#4cc9f0; --green:#4ade80; }}
    * {{ box-sizing:border-box }} body {{ margin:0; color:var(--text); background:radial-gradient(circle at 80% 0,#17345c 0,transparent 35%),var(--bg); font:15px/1.6 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif }}
    main {{ width:min(1180px,calc(100% - 32px)); margin:auto; padding:38px 0 64px }} header {{ display:flex; justify-content:space-between; gap:24px; align-items:end }}
    h1 {{ margin:0; font-size:clamp(30px,5vw,48px) }} h2 {{ margin:26px 0 12px }} h3 {{ margin:2px 0 10px }} .eyebrow,.path,.muted,.meta {{ color:var(--muted) }} .eyebrow,.path {{ margin:0; font-size:12px }} .meta {{ text-align:right; font-size:12px }}
    .summary {{ display:grid; grid-template-columns:repeat(6,1fr); gap:10px; margin:22px 0 }} .metric,.card,.tabs,.filters,.table-wrap,.empty {{ background:var(--panel); border:1px solid var(--line); border-radius:14px }}
    .metric {{ padding:14px }} .metric strong {{ display:block; color:var(--gold); font-size:25px }} .metric span {{ color:var(--muted); font-size:12px }} .tabs,.filters {{ display:flex; gap:8px; flex-wrap:wrap; padding:10px; margin-bottom:16px }}
    button,input {{ border:1px solid var(--line); border-radius:9px; padding:8px 11px; color:var(--text); background:#0b1624; font:inherit }} button {{ cursor:pointer }} button.active {{ color:var(--gold); border-color:var(--gold) }} input {{ flex:1; min-width:210px }}
    .grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:13px }} .card {{ padding:16px; position:relative; overflow:hidden }} .card-head {{ display:flex; justify-content:space-between; gap:12px; align-items:start }} .status {{ padding:3px 8px; border-radius:99px; background:#1c2b3d; white-space:nowrap; font-size:12px }}
    .status-mastered {{ color:var(--gold) }} .status-owned {{ color:var(--green) }} .status-discovered {{ color:var(--blue) }} .progress {{ height:7px; background:#26364a; border-radius:99px; overflow:hidden }} .progress i {{ display:block; height:100%; background:linear-gradient(90deg,var(--blue),var(--green)) }}
    .skill-performance {{ display:flex; justify-content:space-between; gap:12px; align-items:end; margin:12px 0; padding:10px 12px; background:#0b1624; border-left:3px solid var(--gold); border-radius:3px 9px 9px 3px }} .skill-performance span {{ display:block; color:var(--muted); font-size:11px }} .skill-performance strong {{ color:var(--gold); font-size:22px; line-height:1.2 }} .skill-performance p {{ margin:0; color:var(--muted); font-size:12px; text-align:right }}
    .chips {{ display:flex; flex-wrap:wrap; gap:5px }} .chip {{ padding:3px 7px; border-radius:7px; font-size:12px }} .chip.done {{ color:#baf7cc; background:#143323 }} .chip.todo {{ color:#b5c0cd; background:#1a2736 }} .season-tag {{ position:absolute; right:0; bottom:0; padding:3px 9px; color:#08111d; background:var(--gold); font-size:11px; font-weight:700 }}
    .rank {{ color:var(--gold); font-size:22px }} .medal-icon {{ color:var(--gold); font-size:28px }} .medal.locked {{ opacity:.58 }} .table-wrap {{ overflow:auto }} table {{ width:100%; border-collapse:collapse }} th,td {{ padding:10px; text-align:left; border-bottom:1px solid var(--line); white-space:nowrap }}
    .empty {{ grid-column:1/-1; padding:36px; text-align:center; color:var(--muted) }} .page[hidden],[hidden] {{ display:none!important }} @media(max-width:900px) {{ .summary {{ grid-template-columns:repeat(3,1fr) }} .grid {{ grid-template-columns:repeat(2,1fr) }} }} @media(max-width:600px) {{ header {{ display:block }} .meta {{ text-align:left;margin-top:10px }} .summary,.grid {{ grid-template-columns:1fr 1fr }} }}
  </style>
</head><body><main>
  <header><div><p class="eyebrow">GONGKAO SEASON</p><h1>备考总览</h1></div>
    <div class="meta">数据更新：{_escape(updated_at)}<br>页面生成：{_escape(generated_at)}<br>数据文件：{_escape(source_path)}</div></header>
  <section class="summary">
    <div class="metric"><strong>{mastered}/{total}</strong><span>稳定掌握技能</span></div>
    <div class="metric"><strong>{len(state.get("wrong_answers", []))}</strong><span>错题记录</span></div>
    <div class="metric"><strong>{open_easy_points}</strong><span>未解决易错点</span></div>
    <div class="metric"><strong>{len(state.get("assessments", []))}</strong><span>有效战绩</span></div>
    <div class="metric"><strong>{unlocked_medals}/{len(medal_items)}</strong><span>已获勋章</span></div>
    <div class="metric"><strong>{_escape(rank)} {"★" * stars}</strong><span>综合段位 · 调整点 {adjustment}/{adjustment_cap}</span></div>
  </section>
  <nav class="tabs">
    <button class="tab-btn active" data-tab="skills">技能总览</button><button class="tab-btn" data-tab="wrongs">错题本</button>
    <button class="tab-btn" data-tab="easy-points">易错点</button><button class="tab-btn" data-tab="records">战绩</button>
    <button class="tab-btn" data-tab="answers">申论答题册</button><button class="tab-btn" data-tab="medals">勋章墙</button>
  </nav>
  <section class="page" data-page="skills"><div class="filters">
    <button class="filter-btn active" data-filter="all">全部 {total}</button><button class="filter-btn" data-filter="mastered">稳定掌握 {mastered}</button>
    <button class="filter-btn" data-filter="owned">考场可用 {usable - mastered}</button><button class="filter-btn" data-filter="discovered">练习中 {practicing}</button>
    <button class="filter-btn" data-filter="silhouette">未开始 {not_started}</button><button class="filter-btn" data-filter="current">本赛季重点 {len(current_ids)}</button>
    <input id="skill-search" type="search" placeholder="搜索科目、模块或技能"></div>
    <div class="grid" id="skill-grid">{skills or _empty("技能目录尚未建立。完成季前校准后再生成总览。")}</div></section>
  <section class="page" data-page="wrongs" hidden><h2>错题本</h2><div class="grid">{wrongs or _empty("暂无错题记录。")}</div></section>
  <section class="page" data-page="easy-points" hidden><h2>易错点</h2><div class="grid">{easy_points or _empty("暂无易错点。")}</div></section>
  <section class="page" data-page="records" hidden><h2>当前段位</h2><div class="grid">{rankings or _empty("有效样本不足，当前未定级。")}</div>
    <h2>战绩</h2><div class="table-wrap"><table><thead><tr><th>日期</th><th>科目</th><th>范围</th><th>成绩</th><th>来源</th><th>用途</th></tr></thead><tbody>{assessments or '<tr><td colspan="6">暂无战绩。</td></tr>'}</tbody></table></div></section>
  <section class="page" data-page="answers" hidden><h2>申论答题册</h2><div class="grid">{answers or _empty("暂无申论作答。")}</div></section>
  <section class="page" data-page="medals" hidden><h2>勋章墙</h2><div class="grid">{medals or _empty("勋章目录尚未建立。")}</div></section>
</main><script>
  const tabs=[...document.querySelectorAll('.tab-btn')]; const pages=[...document.querySelectorAll('.page')];
  tabs.forEach(button=>button.addEventListener('click',()=>{{tabs.forEach(x=>x.classList.toggle('active',x===button));pages.forEach(page=>page.hidden=page.dataset.page!==button.dataset.tab);}}));
  const cards=[...document.querySelectorAll('.skill-card')]; const filters=[...document.querySelectorAll('.filter-btn')]; const search=document.querySelector('#skill-search'); let filter='all';
  function apply(){{const q=search.value.trim().toLowerCase();cards.forEach(card=>{{const status=card.dataset.status;const match=filter==='all'||status===filter||(filter==='current'&&card.dataset.current==='true');card.hidden=!(match&&card.dataset.search.includes(q));}});}}
  filters.forEach(button=>button.addEventListener('click',()=>{{filter=button.dataset.filter;filters.forEach(x=>x.classList.toggle('active',x===button));apply();}})); search.addEventListener('input',apply);
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
    state_path: Path, output_path: Path, host: str, port: int
) -> HTTPServer:
    """创建只读总览服务；请求首页时按需重建已变化的页面。"""
    previous_mtime: int | None = None

    def refresh() -> None:
        nonlocal previous_mtime
        current_mtime = state_path.stat().st_mtime_ns
        if current_mtime != previous_mtime:
            build_report(state_path, output_path)
            previous_mtime = current_mtime

    class DashboardHandler(BaseHTTPRequestHandler):
        def _send_dashboard(self, *, include_body: bool) -> None:
            request_path = urlsplit(self.path).path
            if request_path not in {"/", f"/{quote(output_path.name)}"}:
                self.send_error(404, "Not Found")
                return
            try:
                refresh()
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

    refresh()
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
