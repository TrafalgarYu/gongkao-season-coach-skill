# 状态、迁移与持久化规范

本文件是所有动态学习事实的唯一数据规范。凡涉及读取、创建、修改、迁移、恢复或保存状态，必须完整读取本文件。

## 目录

1. 状态与算法的边界
2. 存储位置与事务写入
3. 标准状态结构
4. 字段与不变量
5. 幂等事件
6. 日期切换
7. 旧版本迁移
8. 写入失败与恢复
9. 历史保留与压缩

## 1. 状态与算法的边界

- 把状态文件视为学习事实，把 `SKILL.md` 与 references 视为固定算法。
- 不把用户成绩、任务、卡片或奖励写回规则文件。
- 不用对话中的旧副本覆盖磁盘中的较新状态。
- 每次读取后校验 `schema_version`、`engine.state_revision` 和关键不变量。
- 每次成功变更令 `state_revision` 加 1，并更新 `updated_at`。
- 正式赛季使用 `season.ruleset_version` 锁定规则；协议升级不能追溯重算已结算事件。

## 2. 存储位置与事务写入

默认路径：`~/.hermes/data/gongkao-season-coach/state.json`。

用户可显式指定其他路径；一旦选定，把它作为本用户的固定状态源。不得同时维护两个相互竞争的主状态。

每次变更执行：

1. 读取并解析正式文件；记录当前 `state_revision`。
2. 完成迁移并生成内存副本，不立即改写正式文件。
3. 在副本上应用一次完整事件，校验 JSON 和所有不变量。
4. 若正式文件已经存在，把旧文件复制为同目录 `state.backup.json`；首次创建时跳过备份。
5. 把新状态写入同目录临时文件；重新读取并确认可解析。
6. 原子替换正式文件；再次读取并核对新 revision。
7. 只有第 6 步成功后才向用户宣告入账、签到、领奖或结算成功。

若执行环境支持文件锁，在“读取 revision”到“原子替换”期间使用独占锁。若保存时发现正式文件 revision 已变化，放弃本次写入、重新读取并重新计算，不做盲目覆盖。

## 3. 标准状态结构

缺失字段使用下列默认值补齐；不得用默认值覆盖已有非空事实。

```json
{
  "schema_version": "1.1",
  "engine": {
    "ruleset_version": "1.1.0",
    "state_revision": 0,
    "created_at": null,
    "updated_at": null,
    "last_local_date": null,
    "processed_event_ids": [],
    "last_error": null
  },
  "profile": {
    "exam_type": null,
    "paper_type": null,
    "target_position": null,
    "exam_date": null,
    "timezone": "Asia/Shanghai",
    "daily_minutes": null,
    "weekend_minutes": null,
    "planned_study_days_per_week": null,
    "planned_weekdays": [],
    "task_delivery_mode": "pull",
    "task_delivery_time": null
  },
  "goal_contract": {
    "xingce_target": null,
    "shenlun_target": null,
    "total_target": null,
    "target_basis": null,
    "confirmed_at": null,
    "locked_until": null
  },
  "campaign": {
    "started_at": null,
    "days_to_exam": null,
    "readiness_status": "calibrating",
    "readiness_percent": null,
    "readiness_components": {},
    "career_best": {}
  },
  "season": {
    "number": 1,
    "status": "preseason",
    "phase": "calibration",
    "ruleset_version": "1.1.0",
    "start_date": null,
    "end_date": null,
    "length_days": 7,
    "theme": null,
    "rank": "未定级",
    "stars": 0,
    "highest_rank": "未定级",
    "locked_catalog_ids": [],
    "locked_reward_catalog": [],
    "catalog_locked_at": null,
    "reward_catalog_locked_at": null,
    "revenge_quest": null
  },
  "catalog": [],
  "error_hunts": [],
  "shenlun_portfolio": [],
  "assessments": [],
  "review_queue": [],
  "attendance": {
    "today_status": "not_started",
    "momentum_level": 0,
    "current_effective_streak": 0,
    "longest_effective_streak": 0,
    "weekly_planned": 0,
    "weekly_effective": 0,
    "weekly_rate": null,
    "records": []
  },
  "daily_quest": {
    "date": null,
    "status": "not_generated",
    "offer_id": null,
    "options": [],
    "accepted_task_id": null,
    "accepted_at": null,
    "locked_conditions": null,
    "submission_refs": [],
    "verification": null,
    "reward_bundle_id": null,
    "rerolls_used": 0
  },
  "economy": {
    "command_points": 0,
    "command_points_cap": 6,
    "reward_bundles": [],
    "transactions": []
  },
  "weekly_settlements": [],
  "task_history": [],
  "season_history": [],
  "rule_change_proposals": []
}
```

## 4. 字段与不变量

保存前必须同时满足：

- `schema_version` 等于 `1.1`。
- `state_revision` 为非负整数，且每次成功事务只增加 1。
- `daily_quest.date` 为空或等于它所代表的本地自然日。
- 同一日期最多存在一个 `offer_id`，同一 `task_id` 只能属于一个 offer。
- 每个已接取任务都保存完整 `locked_conditions`；验证时只读取锁定副本。
- 每个日期最多一条有效出勤记录和一次每日首胜。
- 每个 `reward_id` 唯一；同一 `task_id` 最多产生一个最终奖励包。
- `command_points` 始终位于 0 与 `command_points_cap` 之间；每次变化都有 transaction。
- 卡片、错因、任务、奖励和结算之间引用的 ID 必须存在。
- `rank`、`stars` 与排位历史一致；非 ranked 任务的 `rank_delta` 必须为 0。
- `weekly_settlements` 的 `week_key` 唯一；`season_history` 的 season number 唯一。
- `readiness_percent` 为空或位于 0–100；数据不足时必须为空并标记 `calibrating`。
- 时间戳使用带时区的 ISO 8601；自然日使用 `YYYY-MM-DD`。

不要把 `null` 成绩当作 0，不要把缺失证据当作失败，不要把未展示奖励当作未入账。

## 5. 幂等事件

所有可重复触发的写操作先生成稳定 `event_id`，再检查 `engine.processed_event_ids`。

推荐格式：

- 接取：`accept:<date>:<task_id>`
- 验证：`verify:<task_id>:<submission_ref>`
- 领取：`reveal:<reward_id>`
- 每日首胜：`first-win:<date>`
- 周结算：`weekly:<week_key>`
- 赛季结算：`season:<season_number>`
- 指挥点消费：`spend:<purpose>:<date>:<target_id>`

优先使用平台消息 ID、作业 ID 或稳定内容指纹作为 `submission_ref`。重复收到已处理事件时返回原结算结果，不新增出勤、卡片、星级、指挥点或历史记录。

`processed_event_ids` 至少保留当前赛季和最近一个已结算赛季的全部写事件。删除旧 ID 前，确认相应奖励、任务和结算历史本身仍能阻止重复入账。

## 6. 日期切换

每次状态操作先按 `profile.timezone` 计算本地日期。若与 `engine.last_local_date` 不同：

1. 若旧奖励已入账但未展示，按创建时间自动揭晓；只改变展示状态。
2. 把昨日 `daily_quest` 原样归档到 `task_history`；不得覆盖已有相同 task ID 的历史。
3. 若昨日是计划学习日且没有 effective，记录 `missed`；计划休整日记录 `planned_rest`。
4. 更新连续出勤、momentum 和到期复习队列。
5. 创建当天空 `daily_quest`，保留历史和所有永久资产。
6. 更新 `last_local_date`，检查是否到周结算或赛季结算节点。

跨越多天时逐日补齐计划日状态，但不得凭空创建任务或能力结果。只有带日期的可核验记录才能补记历史 effective。

## 7. 旧版本迁移

加载 `0.1`、`0.1.1` 或 `1.0` 状态时迁移到 `1.1`：

1. 先保存未经修改的备份。
2. 保留全部成绩、卡片、形态、错因、出勤、任务、奖励、历史和时间戳。
3. 添加缺失的 `engine`、`weekly_settlements`、`rule_change_proposals`、economy transactions 与新增 profile 字段。
4. 把旧 `season.phase = preseason` 映射为 `status = preseason`、`phase = calibration`；正式赛季按日期和历史映射为 `active` 或 `settled`。
5. 把已有赛季规则标记为其原协议；无法确定时使用 `legacy-1.0`，不得假称由 1.1 规则产生。
6. 把旧未揭晓奖励的 `status` 统一为 `unrevealed`；保留其原始奖励内容。
7. 从历史记录重建能够可靠推导的幂等 ID；不能可靠推导时依赖 task/reward 唯一约束，不伪造事件。
8. 校验不变量，令 revision 增加 1，再原子保存。

迁移只改变结构，不追溯改变旧判定、星级、卡片或奖励。

## 8. 写入失败与恢复

写入失败时：

- 不说“已入账”“已签到”“已领取”或“已结算”。
- 保留原正式状态，不把部分结果散落到多个文件。
- 输出一个 `待保存状态块`，至少包含原 revision、event ID、操作类型、完整待写状态或可无歧义重放的变更，以及失败原因。
- 下一次先比较 revision；未变化时可重试同一事件，已变化时重新读取并合并。

正式文件损坏时，先尝试读取 `state.backup.json`。恢复后明确告知恢复到哪个 revision，以及哪些最后事件可能尚未保存；不得静默补造记录。

## 9. 历史保留与压缩

永久保留 season trophies、正式测评、卡片与形态证据、封存错因、申论作品索引、生涯最佳和经济流水。日常原始提交可在跨两个赛季后压缩为摘要，但摘要必须保留 task ID、日期、证据引用、判定、奖励 ID 和来源。

状态过大时优先把不可变旧历史归档到同目录、带校验信息的年度文件，并在主状态保留索引。不得为了缩小文件删除尚未到期复测、未揭晓奖励或当前稳定度窗口需要的数据。
