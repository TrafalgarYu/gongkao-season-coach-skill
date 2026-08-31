"""
版本记录：
- v1.7.0 / 2026-08-31
  - 校验状态结构 1.7、五类 234 项成就及历史练习迁移字段。
- v1.6.0 / 2026-08-30
  - 校验状态结构 1.5、固定 70 项技能及历史数据规范化脚本。
- v1.5.0 / 2026-08-30
  - 校验状态结构 1.4、固定目录脚本和赛季定级脚本。

- v1.4.0 / 2026-08-29
  - 校验备考总览、段位计算器及对应测试文件。

- v1.3.0 / 2026-08-29
  - 把 HTML 总览生成器与对应测试加入项目完整性检查。

- v1.2.0 / 2026-08-24
  - 新增零依赖项目校验，覆盖元数据、链接、JSON 示例和版本一致性。
  - 校验文档默认状态与状态脚本的数据结构一致。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from catalogs import merge_default_catalogs
from state_store import RULESET_VERSION, SCHEMA_VERSION, default_state, validate_state

ROOT = Path(__file__).resolve().parents[1]


def parse_frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", text, re.DOTALL)
    if not match:
        raise ValueError("SKILL.md 缺少有效 YAML frontmatter。")
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line or line[0].isspace() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def compare_structure(expected: Any, actual: Any, path: str = "state") -> list[str]:
    errors: list[str] = []
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path} 应为对象"]
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        if missing:
            errors.append(f"{path} 文档缺少字段：{missing}")
        if extra:
            errors.append(f"{path} 文档多出字段：{extra}")
        for key in set(expected) & set(actual):
            errors.extend(
                compare_structure(expected[key], actual[key], f"{path}.{key}")
            )
    elif isinstance(expected, list) and not isinstance(actual, list):
        errors.append(f"{path} 应为数组")
    return errors


def validate_markdown_files(errors: list[str]) -> None:
    link_pattern = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
    json_pattern = re.compile(r"^```json\s*\n(.*?)^```\s*$", re.MULTILINE | re.DOTALL)
    for path in ROOT.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        if "\ufffd" in text:
            errors.append(f"{path.relative_to(ROOT)} 含 Unicode 替换字符")
        for raw_link in link_pattern.findall(text):
            link = raw_link.split("#", 1)[0].strip()
            if not link or re.match(r"^[a-z]+://", link):
                continue
            target = (path.parent / link).resolve()
            if not target.exists():
                errors.append(f"{path.relative_to(ROOT)} 的链接不存在：{raw_link}")
        for index, block in enumerate(json_pattern.findall(text), start=1):
            try:
                json.loads(block)
            except json.JSONDecodeError as exc:
                errors.append(
                    f"{path.relative_to(ROOT)} 的第 {index} 个 JSON 示例无效：{exc}"
                )


def main() -> int:
    errors: list[str] = []
    required_paths = (
        "SKILL.md",
        "LICENSE",
        "agents/openai.yaml",
        "references/state-schema.md",
        "references/task-and-reward-engine.md",
        "references/assessment-and-season.md",
        "scripts/state_store.py",
        "scripts/dashboard.py",
        "scripts/progression.py",
        "scripts/catalogs.py",
        "scripts/normalization.py",
        "scripts/rankings.py",
        "tests/test_state_store.py",
        "tests/test_dashboard.py",
        "tests/test_progression.py",
        "tests/test_rankings.py",
        "tests/fixtures/state-v1.0.json",
    )
    for relative in required_paths:
        if not (ROOT / relative).exists():
            errors.append(f"缺少必需文件：{relative}")

    skill_text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    try:
        frontmatter = parse_frontmatter(skill_text)
    except ValueError as exc:
        errors.append(str(exc))
        frontmatter = {}
    for key in ("name", "description", "license"):
        if not frontmatter.get(key):
            errors.append(f"SKILL.md frontmatter 缺少 {key}")
    name = frontmatter.get("name", "")
    if name and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        errors.append("Skill name 必须使用小写字母、数字和连字符")
    description = frontmatter.get("description", "")
    if len(description) > 60:
        errors.append(f"Hermes description 超过 60 字符：{len(description)}")
    if f"规则协议：`{RULESET_VERSION}`；状态结构：`{SCHEMA_VERSION}`" not in skill_text:
        errors.append("SKILL.md 缺少当前协议/结构版本声明")

    openai_yaml = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
    if "$gongkao-season-coach" not in openai_yaml:
        errors.append("agents/openai.yaml 的 default_prompt 必须显式提及 Skill")

    validate_markdown_files(errors)

    state_schema_text = (ROOT / "references" / "state-schema.md").read_text(
        encoding="utf-8"
    )
    match = re.search(
        r"^```json\s*\n(.*?)^```\s*$",
        state_schema_text,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        errors.append("state-schema.md 缺少默认状态 JSON")
    else:
        documented = json.loads(match.group(1))
        expected = default_state()
        errors.extend(compare_structure(expected, documented))
        try:
            merge_default_catalogs(documented)
            validate_state(documented)
        except Exception as exc:  # noqa: BLE001 - 聚合项目校验错误
            errors.append(f"文档默认状态未通过运行时校验：{exc}")

    if errors:
        print("项目校验失败：")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        f"项目校验通过：skill={name}, ruleset={RULESET_VERSION}, "
        f"schema={SCHEMA_VERSION}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
