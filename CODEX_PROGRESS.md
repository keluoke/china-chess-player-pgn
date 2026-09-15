# CODEX_PROGRESS

更新时间：2026-09-15（Asia/Shanghai）

## 最终目标

对 https://chessdb.aigclabs.cc/ 在真实环境中进行完整只读 review，从技术架构、产品体验、数据维护三个角度交付有复现证据、优先级、验收指标和迭代路线的中文报告。仅修改进度和评审文档，不改产品代码、不采集来源、不发布。

## 当前计划

1. 阶段 1：建立评审范围与基线；记录本地版本、线上快照、页面与接口清单。
2. 阶段 2：真实浏览器走查核心用户旅程，核验公开 HTTP/JSON/PGN、缓存及数据一致性，形成证据记录并提交。
3. 阶段 3：结合源码、流水线和维护契约审查架构及数据维护机制，区分已证实问题与风险，更新记录并提交。
4. 阶段 4：形成完整评审报告及分阶段优化方案；检查证据、链接和文档差异，提交交付。

验收：覆盖首页/搜索/棋手/赛事/排行榜/棋谱/贡献与数据覆盖说明；重要结论带线上或源码证据；标明检查时间、样本范围与无法核实项；所有方案具备优先级、依赖、成本级别和验收条件。

## 已完成

- 已读取项目 AGENTS.md 与 Scripts/local/README.md，核对采集/代码工作区边界。
- 已检查两个工作区的 git status、最近提交；此前均无 CODEX_PROGRESS.md。
- 已建立包含七项必需内容的进度记录，完成独立文档提交；暂存 diff 格式检查通过。阶段 0 的提交标题为 `docs: initialize Codex progress handoff`；其提交结果以代码工作区 git log 为准。

## 当前正在做

阶段 1：已收到具体任务，正在建立线上评审基线；下一步真实浏览器走查并保存证据。

## 待完成

- 完成阶段 2–4；每阶段结束立即更新记录并提交对应评审材料。

## 重要技术决策

- 当前项目入口：`/Volumes/AI/coding/kimi/CODEX_PROGRESS.md`。
- Git 版本记录：`/Volumes/AI/coding/kimi-code/CODEX_PROGRESS.md`；该文件是受版本管理的主本。每次更新同时更新当前项目入口副本，保持内容一致；发生差异先核对 git diff 和日志。
- 代码、人工文档提交位于 `/Volumes/AI/coding/kimi-code` 的 main。采集工作区不 pull/rebase，不将进度文档加入 local-data 发布包。
- 仅暂存本任务精确路径，不使用 git add .，不清理或提交既有改动。
- 文档初始化只需内容与 diff 校验。修改管线后必须执行 AGENTS.md 指定测试；测试未通过时明确记录，不标为阶段完成。
- 被中断前先记录当前步骤、未提交文件、最后验证结果、失败原因和下一步；提交哈希通过最近 git log 获取，避免把本次提交自身哈希写进自身。

## 已知问题

- 单次真实环境评审是时间点抽样；线上数据/缓存可持续更新，需保留快照与检查时间。
- 采集工作区初始化 HEAD 为 `1f3914ea0ee`，有大量既有 tracked/untracked 改动，均不属于本轮工作。相对本地 origin/main 显示 ahead 309 / behind 1；未查询远端，此数值不代表线上状态。
- 代码工作区初始化 HEAD 为 `18f52e470c`，main 与本地 origin/main 一致；无 tracked 改动。已有未跟踪路径：`.workbuddy/`、`HANDOFF_2026-07-22.md`、`REVIEW_2026-07-24.md`、`data/manual/event-time-controls.csv`、`docs/reviews/REVIEW_2026-08-01_完整项目评审.md`、`experiments/estimated-ratings/Scripts/`、`experiments/estimated-ratings/docs/`。这些不是本轮产物。

## 接续入口

1. 阅读本文件及最近提交中的评审材料，从当前阶段继续，不重复已完成检查。
2. 在 `/Volumes/AI/coding/kimi-code` 查看 `git status --short --branch`、`git diff`、`git diff --cached`、`git log -5 --oneline`。
3. 如从采集目录开始，同时查看该目录的 status/diff/log；既有脏状态不得视为需要同步或清理。
4. 从“当前正在做”续接；每阶段更新本文件、运行相应验证并精确提交。
