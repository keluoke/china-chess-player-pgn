# CODEX_PROGRESS

更新时间：2026-09-15（Asia/Shanghai）

## 最终目标

建立可跨 Codex session 接续的阶段进度记录，并在每个可运行阶段完成后提交 Git。
用户本轮只提供了工作规则，尚未提供具体开发、修复或审查目标；不得从历史记录推断新任务。

## 当前计划

1. 阶段 0：核对项目契约、双工作区状态，初始化本文件并单独提交。
2. 阶段 1：收到具体任务后，记录目标、范围、验收标准，拆成可独立完成的阶段。
3. 阶段 2 起：逐阶段实施、验证，完成一个阶段立即更新本文件；每个可运行阶段做一次 git commit。
4. 交付阶段：核对目标与验证证据，记录最终状态、剩余问题和恢复入口；涉及上线时核实实际发布回执。

## 已完成

- 已读取项目 AGENTS.md 与 Scripts/local/README.md，核对采集/代码工作区边界。
- 已检查两个工作区的 git status、最近提交；此前均无 CODEX_PROGRESS.md。
- 已建立包含七项必需内容的进度记录，完成独立文档提交；暂存 diff 格式检查通过。阶段 0 的提交标题为 `docs: initialize Codex progress handoff`；其提交结果以代码工作区 git log 为准。

## 当前正在做

阶段 0 已完成。当前等待用户提供具体任务目标，然后开始阶段 1。

## 待完成

- 用户提供具体任务后，将上述通用流程替换为该任务的具体阶段及验收条件。
- 每阶段记录改动文件、验证命令与结果、重要决策、问题及下一条可执行步骤。
- 每个可运行阶段在代码工作区 main 精确提交本阶段文件和进度记录。

## 重要技术决策

- 当前项目入口：`/Volumes/AI/coding/kimi/CODEX_PROGRESS.md`。
- Git 版本记录：`/Volumes/AI/coding/kimi-code/CODEX_PROGRESS.md`；该文件是受版本管理的主本。每次更新同时更新当前项目入口副本，保持内容一致；发生差异先核对 git diff 和日志。
- 代码、人工文档提交位于 `/Volumes/AI/coding/kimi-code` 的 main。采集工作区不 pull/rebase，不将进度文档加入 local-data 发布包。
- 仅暂存本任务精确路径，不使用 git add .，不清理或提交既有改动。
- 文档初始化只需内容与 diff 校验。修改管线后必须执行 AGENTS.md 指定测试；测试未通过时明确记录，不标为阶段完成。
- 被中断前先记录当前步骤、未提交文件、最后验证结果、失败原因和下一步；提交哈希通过最近 git log 获取，避免把本次提交自身哈希写进自身。

## 已知问题

- 缺少具体任务目标，目前只能完成工作记录初始化。
- 采集工作区初始化 HEAD 为 `1f3914ea0ee`，有大量既有 tracked/untracked 改动，均不属于本轮工作。相对本地 origin/main 显示 ahead 309 / behind 1；未查询远端，此数值不代表线上状态。
- 代码工作区初始化 HEAD 为 `18f52e470c`，main 与本地 origin/main 一致；无 tracked 改动。已有未跟踪路径：`.workbuddy/`、`HANDOFF_2026-07-22.md`、`REVIEW_2026-07-24.md`、`data/manual/event-time-controls.csv`、`docs/reviews/REVIEW_2026-08-01_完整项目评审.md`、`experiments/estimated-ratings/Scripts/`、`experiments/estimated-ratings/docs/`。这些不是本轮产物。

## 接续入口

1. 阅读本文件，确认具体目标是否已经补充；尚未补充则等待用户任务，不启动历史任务。
2. 在 `/Volumes/AI/coding/kimi-code` 查看 `git status --short --branch`、`git diff`、`git diff --cached`、`git log -5 --oneline`。
3. 如从采集目录开始，同时查看该目录的 status/diff/log；既有脏状态不得视为需要同步或清理。
4. 从“当前正在做”续接；每阶段更新本文件、运行相应验证并精确提交。
