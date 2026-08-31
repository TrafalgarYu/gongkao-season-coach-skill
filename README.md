# 公考赛季教练

把公务员考试行测与申论备考组织成 14–28 天的短赛季。它用真实答案、正确率、用时、订正和复测作为能力证据，提供每日三选一任务、技能总览、错题本、易错点、战绩、申论答题册、段位、勋章墙和赛季结算。

当前版本：规则协议 `1.6.0`，状态结构 `1.6`。支持 Hermes Agent 与 OpenAI Codex，状态只保存在本机。

## 设计边界

- 本 Skill 负责任务编排、状态、验收和反馈，不充当权威题库。
- 有效出勤、任务完成、练习战绩、勋章、错题、易错点和段位分别判定。
- 未校准的 AI 申论内部估分不能改变段位。
- 技能页直接显示样本、正确率或得分率，不再使用“考场可用”等主观标签。
- 五个行测模块各有五级正确率与用时勋章；排位战绩与日常练习分开。

## HTML 备考总览

生成一次当前总览：

```powershell
python -X utf8 scripts/dashboard.py
```

默认输出到状态文件同目录的 `dashboard.html`。页面有技能总览、错题本、易错点、战绩、申论答题册和勋章墙六个栏目。技能总览固定展示 70 项标准技能，战绩页展示题量、正确数、正确率和实际用时，勋章墙固定展示 40 枚勋章；战绩同时显示本赛季、上赛季、历史最高段位和重新定级进度。

保持页面随状态文件更新：

```powershell
python -X utf8 scripts/dashboard.py --watch
```

监听脚本检测到状态变化后会重建 HTML，浏览器页面每 15 秒自动刷新。需要指定独立存档时可增加 `--state-path <路径>`。

轻量云服务器建议使用内置只读服务：

```bash
python scripts/dashboard.py --state-path /srv/gongkao/state.json --serve --host 127.0.0.1 --port 8080
```

每次访问首页时，服务都会检查状态文件是否变化；浏览器打开后仍每 15 秒刷新。生产环境用 systemd 保持进程常驻，再由 Nginx 配置 HTTPS、密码或其他访问控制。若确实要让脚本直接监听公网，可以把 `--host` 改为 `0.0.0.0`，但不建议在没有访问控制时这样做。

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

结算旧赛季后开启新赛季：

```bash
python scripts/state_store.py new-season --start-date 2026-09-01 --end-date 2026-09-28 --theme 限时稳定 --event-id season:2
```

新赛季保留技能、错题、申论答题册、勋章和历史战绩，当前模块、科目与综合段位回到未定级并只使用新赛季排位重新计算。

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
│   ├── catalogs.py
│   ├── dashboard.py
│   ├── progression.py
│   ├── rankings.py
│   ├── state_store.py
│   └── validate_project.py
└── tests/
    ├── test_dashboard.py
    ├── test_progression.py
    ├── test_rankings.py
    ├── test_state_store.py
    └── fixtures/state-v1.0.json
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
