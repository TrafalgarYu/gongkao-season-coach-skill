# 状态、迁移与持久化规范

当前状态结构：`1.5`；当前规则协议：`1.5.0`。

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
- 不把用户技能、错题、战绩、作答或勋章写回规则文件。

界面对用户使用“技能、技能熟练度、技能总览、错题本、易错点、战绩、申论答题册、勋章墙、调整点”。为兼容既有状态，JSON 继续保留 `catalog`、`forms`、`error_hunts`、`shenlun_portfolio`、`command_points` 和 `reward_bundles`；不要把这些内部字段名直接显示给用户。
- 不用对话中的旧副本覆盖磁盘中的较新状态。
- 每次读取后校验 `schema_version`、`engine.state_revision` 和关键不变量。
- 每次成功变更令 `state_revision` 加 1，并更新 `updated_at`。
- 正式赛季使用 `season.ruleset_version` 锁定规则；协议升级不能追溯重算已结算事件。

## 2. 存储位置与事务写入

使用 `scripts/state_store.py resolve-path` 决定唯一主状态。解析顺序为：显式 `--state-path`、环境变量 `GONGKAO_SEASON_COACH_STATE`、唯一已存在状态、跨运行时系统数据目录。系统数据目录为：

- Windows：`%LOCALAPPDATA%/gongkao-season-coach/state.json`；
- macOS：`~/Library/Application Support/gongkao-season-coach/state.json`；
- Linux：`$XDG_DATA_HOME/gongkao-season-coach/state.json`，未设置时使用 `~/.local/share/`。

为兼容旧版，若 `~/.hermes/data/gongkao-season-coach/state.json` 或 `~/.codex/data/gongkao-season-coach/state.json` 中恰有一个已存在，则继续沿用原路径，不自动搬迁。发现两个及以上候选主状态时停止，要求用户明确指定；不得自动选较新文件或合并。

每次变更由状态脚本执行：

1. 读取并解析正式文件；记录当前 `state_revision`。
2. 完成迁移并生成内存副本，不立即改写正式文件。
3. 在副本上应用一次完整事件，保存为临时候选 JSON，校验全部业务判定。
4. 若正式文件已经存在，把旧文件复制为同目录 `state.backup.json`；首次创建时跳过备份。
5. 调用 `commit`，由脚本把候选写入同目录临时文件并重新读取校验。
6. 原子替换正式文件；再次读取并核对新 revision。
7. 只有第 6 步成功后才向用户宣告入账、签到、领奖或结算成功。

脚本在“读取 revision”到“原子替换”期间使用跨平台独占锁。若保存时发现正式文件 revision 已变化，返回 `REVISION_CONFLICT`；放弃旧候选、重新读取并重新计算，不做盲目覆盖。

## 3. 标准状态结构

缺失字段使用下列默认值补齐；不得用默认值覆盖已有非空事实。`catalog` 和 `medals` 在初始化时由 `scripts/catalogs.py` 分别注入 70 项标准技能和 27 枚固定勋章，下面保留空数组只表示这两个位置由固定目录填充。

```json
{
  "schema_version": "1.5",
  "engine": {
    "ruleset_version": "1.5.0",
    "state_revision": 0,
    "created_at": null,
    "updated_at": null,
    "last_local_date": null,
    "processed_event_ids": [],
    "migration_history": [],
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
    "contract_id": null,
    "campaign_id": null,
    "xingce_target": null,
    "shenlun_target": null,
    "total_target": null,
    "target_basis": null,
    "module_targets": [],
    "subject_targets": [],
    "confirmed_at": null,
    "locked_until": null
  },
  "goal_contract_history": [],
  "campaign": {
    "campaign_id": null,
    "status": "calibrating",
    "started_at": null,
    "completed_at": null,
    "days_to_exam": null,
    "readiness_status": "calibrating",
    "readiness_percent": null,
    "readiness_components": {},
    "career_best": {}
  },
  "campaign_history": [],
  "season": {
    "season_id": null,
    "campaign_id": null,
    "number": 1,
    "status": "preseason",
    "phase": "calibration",
    "ruleset_version": "1.5.0",
    "start_date": null,
    "end_date": null,
    "length_days": 7,
    "theme": null,
    "rank": "未定级",
    "stars": 0,
    "highest_rank": "未定级",
    "previous_rank": "未定级",
    "previous_stars": 0,
    "season_effective_days": 0,
    "season_completed_tasks": 0,
    "challenge_progress": {},
    "placement_progress": {
      "xingce_current": 0,
      "xingce_target": 2,
      "shenlun_current": 0,
      "shenlun_target": 2
    },
    "ranking_mode": "season_only",
    "locked_catalog_ids": [],
    "locked_reward_catalog": [],
    "catalog_locked_at": null,
    "reward_catalog_locked_at": null,
    "revenge_quest": null
  },
  "catalog": [],
  "wrong_answers": [],
  "error_hunts": [],
  "shenlun_portfolio": [],
  "assessments": [],
  "module_rankings": [],
  "subject_rankings": [],
  "medals": [],
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

### 3.1 动态对象的最低字段契约

数组非空时，每个对象至少保存以下字段；允许增加向后兼容字段，但不得改变既有字段含义。所有本赛季事件同时写入 `campaign_id` 与 `season_id`，从而避免跨考试、跨年度混账。

- `catalog[]`：`id`、`subject`、`module`、`name`、`tier`、`status`、`forms`、`thresholds`、`evidence`、`last_tested_at`、`next_review_at`、`needs_retest`；有可比实测数据时增加 `recent_performance`。
- `catalog[].evidence[]`：`evidence_id`、`campaign_id`、`season_id`、`task_id`、`submission_ref`、`tested_at`、`result`、`forms_supported`。
- `catalog[].recent_performance`：`metric`、`value`、`sample_count`、`question_count`、`window_label`、`updated_at`。`metric` 只允许 `accuracy` 或 `score_rate`，`value` 位于 0–100；没有可比样本时整个对象写 `null`。
- `goal_contract.module_targets[]`：`subject`、`module`、`metric`、`total_points`、`floor_value`、`target_value`、`stretch_value`、`time_limit_minutes`。
- `goal_contract.subject_targets[]`：`subject`、`metric`、`floor_value`、`target_value`、`stretch_value`。
- `wrong_answers[]`：`wrong_id`、`campaign_id`、`season_id`、`date`、`subject`、`module`、`question_ref`、`user_answer`、`correct_answer`、`error_hunt_id`、`correction`、`status`、`next_review_at`。
- `error_hunts[]`：`error_hunt_id`、`campaign_id`、`season_id`、`subject`、`module`、`mechanism`、`status`、`evidence`、`next_review_at`。
- `shenlun_portfolio[]`：`portfolio_id`、`campaign_id`、`season_id`、`date`、`task_type`、`prompt_ref`、`submission_ref`、`score`、`score_max`、`score_rate`、`normalization_status`、`score_source`、`dimensions`、`answer_text`、`feedback`、`word_count`、`time_minutes`。
- `assessments[]`：`assessment_id`、`campaign_id`、`season_id`、`date`、`subject`、`scope`、`ranked`、`conditions`、`score`、`score_max`、`score_rate`、`normalization_status`、`score_source`、`evidence_refs`、`rank_delta`、`ruleset_version`。
- `module_rankings[]`：`ranking_id`、`campaign_id`、`season_id`、`subject`、`module`、`metric`、`stable_value`、`rank`、`stars`、`next_rank`、`gap_to_next`、`sample_size`、`assessment_refs`、`updated_at`。
- `subject_rankings[]`：`ranking_id`、`campaign_id`、`season_id`、`subject`、`metric`、`stable_value`、`rank`、`stars`、`next_rank`、`gap_to_next`、`sample_size`、`assessment_refs`、`updated_at`。
- `medals[]`：`medal_id`、`name`、`category`、`description`、`status`、`condition`、`progress_current`、`progress_target`、`progress_unit`、`evidence_refs`、`unlocked_at`。
- `review_queue[]`：`review_id`、`campaign_id`、`season_id`、`target_type`、`target_id`、`due_at`、`status`、`source_evidence_id`。
- `attendance.records[]`：`date`、`campaign_id`、`season_id`、`status`、`counts_as_effective`、`task_id`、`submission_refs`、`recorded_at`。
- `economy.reward_bundles[]`：`reward_id`、`campaign_id`、`season_id`、`date`、`task_id`、`submission_refs`、五类判定、`ranked`、`rank_delta`、`status`、`created_at`、`revealed_at`。
- `economy.transactions[]`：`transaction_id`、`campaign_id`、`season_id`、`event_id`、`date`、`type`、`delta`、`balance_after`、`reason`。
- `task_history[]`：`task_id`、`campaign_id`、`season_id`、`date`、`status`、`locked_conditions`、`submission_refs`、`verification`、`reward_id`。
- `weekly_settlements[]`：`week_key`、`campaign_id`、`season_id`、`revision`、`period_start`、`period_end`、`metrics`、`reward_ids`、`created_at`。
- `season_history[]`：`season_id`、`campaign_id`、`number`、`ruleset_version`、`start_date`、`end_date`、`rank`、`stars`、`trophy`、`settled_at`。
- `campaign_history[]`：`campaign_id`、考试口径、目标契约摘要、起止日期、最终成绩、关联 `season_id` 列表、`completed_at`。
- `goal_contract_history[]`：`contract_id`、`campaign_id`、完整目标、依据、确认与失效时间。
- `rule_change_proposals[]`：`proposal_id`、`campaign_id`、`season_id`、`proposed_at`、`reason`、`expected_benefit`、`side_effects`、`decision`、`decided_at`。

`score_source` 只使用：`official`、`institution`、`teacher`、`platform`、`user_self`、`ai_internal`。对象暂时缺少某项事实时写 `null` 或空数组，不发明默认分数、题源或判定。

`score` 保存原始得分，`score_max` 保存满分，`score_rate` 保存按 `score / score_max × 100` 计算的得分率。`normalization_status` 只允许 `exact`、`needs_review`、`not_scored`。新记录只允许 `exact` 或 `not_scored`；旧记录无法确认满分时保留原始分数并标为 `needs_review`，不参与比较或段位。

## 4. 字段与不变量

保存前必须同时满足：

- `schema_version` 等于 `1.5`，`engine.ruleset_version` 等于 `1.5.0`。
- 模块三条线必须满足 `0 ≤ 保底线 < 目标线 < 冲刺线 ≤ 100`；同一科目与模块只能有一组锁定目标。
- 段位只允许未定级、青铜、白银、黄金、钻石、大师、王者；未定级时星数为 0，其他段位为 1–3 星。
- `state_revision` 为非负整数，且每次成功事务只增加 1。
- `daily_quest.date` 为空或等于它所代表的本地自然日。
- 当前 campaign 非空时必须有唯一 `campaign_id`；当前 season 必须同时保存相同 `campaign_id` 和唯一 `season_id`。
- 同一日期最多存在一个 `offer_id`，同一 `task_id` 只能属于一个 offer。
- 每个已接取任务都保存完整 `locked_conditions`；验证时只读取锁定副本。
- 每个日期最多一条有效出勤记录和一次每日首胜。
- `recovery` 是一种出勤状态，必须同时写 `counts_as_effective = true`；`missed` 与 `planned_rest` 必须为 false。
- 每个 `reward_id` 唯一；同一 `task_id` 最多产生一个最终结算单。
- `command_points` 始终位于 0 与 `command_points_cap` 之间；每次变化都有 transaction。
- 技能、错题、易错点、任务、奖励和结算之间引用的 ID 必须存在。
- 技能近期实测的 `sample_count`、`question_count` 为非负整数或 `null`；缺少实测时不得用熟练度检查项完成比例代替正确率或得分率。
- 1.5 规则下段位由本赛季战绩重算，新增 `assessments[].rank_delta` 必须为 0；旧协议的历史值原样保留。
- 规则 1.5 下，`score_source = ai_internal` 的申论估分无论高低都不得单独完成定级。
- `catalog` 必须恰好包含 `skill-01` 至 `skill-70`，全部为 `tier = standard`；新任务不得创建自定义技能。
- 1.5 新任务的每个候选项必须用 `medal_targets` 指向至少一枚未点亮勋章；全部勋章点亮后可为空。
- 已验证申论任务的每个 `submission_ref` 必须存在唯一 `portfolio:<submission_ref>`，验证结果同时保存 `portfolio_changes`。
- `weekly_settlements` 的 `week_key` 唯一；`season_history` 的 `season_id` 唯一；`campaign_history` 的 `campaign_id` 唯一。
- `readiness_percent` 为空或位于 0–100；数据不足时必须为空并标记 `calibrating`。
- 时间戳使用带时区的 ISO 8601；自然日使用 `YYYY-MM-DD`。

不要把 `null` 成绩当作 0，不要把缺失证据当作失败，不要把未展示奖励当作未入账。

## 5. 幂等事件

所有可重复触发的写操作先生成稳定 `event_id`，再检查 `engine.processed_event_ids`。

推荐格式：

- 接取：`accept:<date>:<task_id>`
- 验证：`verify:<task_id>:<submission_ref>`
- 查看结算：`reveal:<reward_id>`
- 每日首胜：`first-win:<date>`
- 周结算：`weekly:<week_key>`
- 赛季结算：`season:<season_number>`
- 使用调整点：`spend:<purpose>:<date>:<target_id>`

优先使用平台消息 ID、作业 ID 或稳定内容指纹作为 `submission_ref`。重复收到已处理事件时返回原结算结果，不新增出勤、技能熟练度、错题、段位星、调整点或历史记录。

`processed_event_ids` 至少保留当前赛季和最近一个已结算赛季的全部写事件。删除旧 ID 前，确认相应奖励、任务和结算历史本身仍能阻止重复入账。

## 6. 日期切换

每次状态操作先按 `profile.timezone` 计算本地日期。若与 `engine.last_local_date` 不同：

1. 若旧奖励已入账但未展示，按创建时间自动揭晓；只改变展示状态。
2. 把昨日 `daily_quest` 原样归档到 `task_history`；不得覆盖已有相同 task ID 的历史。
3. 若昨日是计划学习日且没有 effective，记录 `missed`；计划休整日记录 `planned_rest`。
4. 更新连续出勤、momentum 和到期复习队列。
5. 创建当天空 `daily_quest`，把 `attendance.today_status` 重置为 `not_started`，保留历史和所有永久记录。
6. 按记录重新计算本周 planned、effective 与 rate；自然周变化时不得沿用旧周计数。
7. 根据 `profile.exam_date` 更新 `campaign.days_to_exam`；只在证据变化时重算备考度。
8. 更新 `last_local_date`，检查是否到周结算或赛季结算节点。

跨越多天时逐日补齐计划日状态，但不得凭空创建任务或能力结果。只有带日期的可核验记录才能补记历史 effective。

## 7. 旧版本迁移

加载 `0.1`、`0.1.1`、`1.0`、`1.1`、`1.2`、`1.3` 或 `1.4` 状态时，先运行 `scripts/state_store.py migrate --dry-run`，核对报告后运行 `migrate` 迁移到 `1.5`：

1. 先保存未经修改的备份。
2. 保留全部技能、错题、易错点、战绩、申论作答、出勤、奖励、历史和时间戳。
3. 添加缺失的 `engine`、`campaign_history`、`goal_contract_history`、`weekly_settlements`、`rule_change_proposals`、economy transactions 与新增 profile 字段。
4. 把旧 `season.phase = preseason` 映射为 `status = preseason`、`phase = calibration`；正式赛季按日期和历史映射为 `active` 或 `settled`。
5. 为已有备考周期生成稳定 `campaign_id`，为赛季、目标契约和历史对象补稳定 ID；只补引用，不拆分或合并历史事实。
6. 把已有赛季规则标记为其原协议；无法确定时使用 `legacy-<schema>`，不得假称由 1.5 规则产生。
7. 把旧未揭晓奖励的 `status` 统一为 `unrevealed`；保留其原始奖励内容。
8. 把旧 `recovery` 出勤补为 `counts_as_effective = true`，不额外补发每日首胜。
9. 从历史记录重建能够可靠推导的幂等 ID；不能可靠推导时依赖 task/reward 唯一约束，不伪造事件。
10. 校验不变量，令 revision 增加 1，再原子保存。

1.5 数据修复会把旧版同义技能的证据并入固定技能，把仅有基础练习的错误“考场可用”状态纠正为“练习中”，并从活动目录移除已经完成映射的自定义技能。它不会补发奖励或段位。无法映射的自定义技能会阻止迁移并出现在报告中，不静默删除。

旧版同时保存行测、申论和总分的复合考试记录拆成两条单科历史基线，原总分和来源放入迁移条件；旧版 AI 百分制单题评分补为 `score_max = 100` 和明确得分率。无法确认口径的成绩保留原始分数并标记 `needs_review`。历史申论仅在能够确认科目、提交引用和作答原文时补建答题册，无法确认的引用写入迁移报告。

## 8. 写入失败与恢复

写入失败时：

- 不说“已入账”“已签到”“已查看”或“已结算”。
- 保留原正式状态，不把部分结果散落到多个文件。
- 输出一个 `待保存状态块`，至少包含原 revision、event ID、操作类型、完整待写状态或可无歧义重放的变更，以及失败原因。
- 下一次先比较 revision；未变化时可重试同一事件，已变化时重新读取并合并。

正式文件损坏时，先校验 `state.backup.json`。得到用户明确同意后运行 `scripts/state_store.py recover --event-id <稳定ID>`；脚本必须保留 `state.corrupt.<时间>.json`，恢复后明确告知 revision 与可能尚未保存的最后事件。正式状态仍然有效时，脚本拒绝用旧备份覆盖。

## 9. 历史保留与压缩

永久保留 campaign 与 season 历史、目标契约、技能证据、错题本、易错点、战绩、申论答题册、勋章、生涯最佳和调整点流水。日常原始提交可在跨两个赛季后压缩为摘要，但摘要必须保留 campaign ID、season ID、task ID、日期、证据引用、判定、奖励 ID 和来源。

状态过大时优先把不可变旧历史归档到同目录、带校验信息的年度文件，并在主状态保留索引。不得为了缩小文件删除尚未到期复测、未揭晓奖励或当前稳定度窗口需要的数据。
