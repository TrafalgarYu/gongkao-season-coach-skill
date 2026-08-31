# 公考赛季教练

把公务员考试行测与申论备考组织成由用户设定起止日期的备考赛季，每季独立排位。它用真实答案、正确率、用时、订正和复测作为能力证据，提供每日三选一任务、技能总览、错题类型统计、战绩、申论答题册、段位、勋章墙和赛季结算。

当前版本：规则协议 `1.7.0`，状态结构 `1.7`。支持 Hermes Agent 与 OpenAI Codex，状态只保存在本机。

## 为什么设计它

这个 Skill 有三个相互衔接的目的：

1. **把备考变成能完成的任务。** 按考试日期、当前短板、复测到期和可用时间，合理拆分行测与申论学习；完成任务是打基础，不是为了刷签到。
2. **把零散练习变成可信记录。** 统一收集每日任务、自主练习和全卷模拟，保存题型、题量、正确数、用时、错题、订正与申论作答，让历史数据可以复查、分类和比较。
3. **用反馈推动持续练习。** 段位反映正式排位表现；单次战绩、长期实力、真实成长、生涯累计和赛季进度提供及时反馈。体系允许重复升星而没有人为终点，同时把 11 项能力的正确率与速度分开展示，让强项持续积累、弱项明确暴露并进入刻意练习。

## 设计边界

- 本 Skill 负责任务编排、状态、验收和反馈，不充当权威题库。
- 有效出勤、任务完成、练习战绩、勋章、错题与类型统计、段位分别判定。
- 未校准的 AI 申论内部估分不能改变段位。
- 技能页直接显示样本、正确率或得分率，不再使用“考场可用”等主观标签。
- 成就墙分为单次战绩、实力勋章、成长成就、生涯成就和赛季成就；排位战绩与日常练习分开。
- 实力勋章覆盖 11 项能力，正确率或得分率与用时分别五档；达到的档位永久保留。

## HTML 备考总览

生成一次当前总览：

```powershell
python -X utf8 scripts/dashboard.py
```

默认输出到状态文件同目录的 `dashboard.html`。页面使用“今日任务、技能地图、练习记录、错题本、申论答题本、战绩段位、成就墙”七栏目闭环。技能地图固定展示 70 项标准技能；练习记录合并行测和申论；错题本按模块和错题类型显示错误次数与纵向明细；成就墙展示五类 234 项成就。实力勋章集中展示 11 项双战线五档阶梯，已达档位使用不同颜色，并明确样本进度、下一档和剩余档位。

保持页面随状态文件更新：

```powershell
python -X utf8 scripts/dashboard.py --watch
```

监听脚本检测到状态变化后会重建 HTML，但不会强制浏览器反复重载。需要指定独立存档时可增加 `--state-path <路径>`。

轻量云服务器建议使用内置只读服务：

```bash
python scripts/dashboard.py --state-path /srv/gongkao/state.json --serve --host 127.0.0.1 --port 8080
```

服务启动时生成一次，此后每天 08:00 自动生成新快照；点击页面右上角“手动刷新”会立即读取最新状态并重建。生产环境用 systemd 保持进程常驻，再由 Nginx 配置 HTTPS、密码或其他访问控制。若确实要让脚本直接监听公网，可以把 `--host` 改为 `0.0.0.0`，但不建议在没有访问控制时这样做。

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

到达用户设置的结束日期后结算赛季：

```bash
python scripts/state_store.py settle-season --event-id season:1:settle
```

结算后系统进入等待状态，不会自动开启下一季。用户确定下一段备考期后再开启新赛季：

```bash
python scripts/state_store.py new-season --start-date 2026-11-01 --end-date 2027-03-31 --theme 公考备考季 --event-id season:2
```

每个赛季对应用户确认的一段备考期，开始和结束日期都由用户设置，系统不代猜固定时长。新赛季保留技能、错题、申论答题册、生涯成就和历史战绩；当前模块、科目、综合段位与赛季成就从零开始，旧段位和旧赛季成就仍可在历届赛季档案查看。

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
