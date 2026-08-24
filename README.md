# 公考赛季教练

把公务员考试行测与申论备考组织成 14–28 天的游戏化短赛季。它用真实答案、正确率、用时、订正和复测作为能力证据，提供每日三选一任务、能力图鉴、错因追猎、排位、周报和赛季结算。

当前版本：规则协议 `1.2.0`，状态结构 `1.2`。支持 Hermes Agent 与 OpenAI Codex，状态只保存在本机。

## 设计边界

- 本 Skill 负责任务编排、状态、验收和反馈，不充当权威题库。
- 有效出勤、任务完成、能力变化、错因变化和排位变化分别判定。
- 未校准的 AI 申论内部估分不能增加或扣除排位星。
- 虚拟奖励必须能追溯到真实学习证据。

## 安装

### Codex

在 Codex 中调用 `$skill-installer`，让它从以下仓库安装：

```text
https://github.com/TrafalgarYu/gongkao-season-coach-skill
```

也可以把仓库复制到用户级 Skill 目录：

```text
~/.agents/skills/gongkao-season-coach/
```

### Hermes Agent

Hermes 支持从 `SKILL.md` URL 安装并获取被明确引用的 `references/` 与 `scripts/`：

```bash
hermes skills install https://raw.githubusercontent.com/TrafalgarYu/gongkao-season-coach-skill/main/SKILL.md --category productivity
```

安装后可以使用 `/gongkao-season-coach`，也可以直接说“开始学习”或“今日任务”。

## 首次使用

首次启动会分批收集考试类型、考试日期、目标岗位、近期严格限时成绩、申论评分来源、可投入时长和计划学习日。考试类型与权重未确认前不会硬套 135 分目标。

典型入口：

```text
使用公考赛季教练，根据我的真实成绩建立一个备考赛季。
今日任务。
提交作业：任务 ID……
查看本周结算。
```

## 状态安全与双平台兼容

`scripts/state_store.py` 负责状态路径、锁、revision、幂等事件、备份、迁移和原子替换。Hermes 与 Codex 共用同一套脚本，不依赖第三方 Python 包。

路径解析顺序：

1. 命令行 `--state-path`；
2. 环境变量 `GONGKAO_SEASON_COACH_STATE`；
3. 唯一已存在的新版、Hermes 旧版或 Codex 旧版状态；
4. 操作系统的用户数据目录。

发现两个候选主状态时脚本会停止，避免自动覆盖。已有 `~/.hermes/data/gongkao-season-coach/state.json` 不会被擅自搬迁。

查看实际路径：

```bash
python scripts/state_store.py resolve-path
```

校验状态：

```bash
python scripts/state_store.py validate
```

迁移旧状态前会生成同目录 `state.backup.json`：

```bash
python scripts/state_store.py migrate
```

## 项目结构

```text
.
├── SKILL.md
├── agents/openai.yaml
├── references/
│   ├── assessment-and-season.md
│   ├── state-schema.md
│   └── task-and-reward-engine.md
├── scripts/
│   ├── state_store.py
│   └── validate_project.py
└── tests/test_state_store.py
```

三个 `references` 是同一个 Skill 的条件规则，不是三个独立 Skill。只有能独立触发、独立安装和独立迭代的工作流才应拆成新的 Skill。

## 开发与验证

项目只使用 Python 标准库。Windows 下显式启用 UTF-8，可避免系统默认 GBK 影响 Markdown 校验：

```powershell
python -X utf8 scripts/validate_project.py
python -X utf8 -m unittest discover -s tests -v
```

若本机有 OpenAI `skill-creator`，还可以运行其快速校验器：

```powershell
python -X utf8 <skill-creator目录>/scripts/quick_validate.py .
```

## 许可证

[MIT License](LICENSE)
