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

- 阶段 1 已完成：明确三维只读评审范围。远端 main 已实查为 `18f52e470c96dd995c7879cb6b52703d3e3ca91b`。
- 阶段 2 HTTP/数据核验已完成：线上快照 `20260915T063851Z-1850db6a`，5 个 snapshot 输出 hash/bytes 一致，主页及核心 JS 与代码工作区一致；公开 PGN 做逐局解析。证据：`review-evidence/2026-09-15-production.json`。浏览器交互部分未完成，工具连接超时。
- 核心新发现：1458883 原赛事包 162 局中 12 局非法 SAN，目录过滤后 150 局，但详情仍 playableComplete=true；1227491 为 89 局中 3 局非法 SAN，目录 86；居文君 717 局包含 6 局解析错误。R2 哈希正确不代表棋谱语义正确。
- API 与搜索 eventCount 不同口径：居文君 API=0，bootstrap=135；待在报告解释。覆盖页仍展示退役 cr-contrib 漏斗。

- 阶段 3 已完成：排行榜 60 维/3717 行校验无错误；9 条搜索 query 在当前线上 bootstrap + 已验证同正文 search-core 上离线检查，正常（不等于浏览器交互通过）。已有 search-core 测试及 presentation-names 5 项测试通过。
- 质量队列含 43 项 result-mismatch/15 个赛事，实核 1059818 第1轮第62台，成绩 0-1 而 localGame 为 1-0；真实结果尚未裁决。
- 最新 deploy run 34943857597 成功，部署 ce03b7f39c，产物 2384 文件/475928 KiB；末次快照正文未变化。
- 架构决定：评审 JSON 证据移到仓库根 `review-evidence/`，因为站点组装仅排除 Markdown/HTML，放 docs/reviews 下的 JSON 会随未来部署复制。无需改产品代码。

## 当前正在做

阶段 4：架构与维护根因审查完成，正在写最终报告。下一步完成方案、验收矩阵，核对引用与 git diff 后提交交付。

## 待完成

- 完成阶段 4：最终报告、证据引用、分期计划与文档验证。
- 真实浏览器交互与移动端视觉检查受 CUA 连接超时阻塞；不得标记通过，最终报告必须注明限制。

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

### 本次评审临时证据

- 完整 HTTP 正文与只读 probe 脚本位于 `/tmp/chessdb-review-20260915/`；可丢失，关键摘要已存入上述 Git 文档。
- 已运行 python-chess 对 6 个目录包、3 个原赛事包、2 个棋手包（R2/代理各一份）解析。尚未跑浏览器交互、移动端或性能指标测试；curl 耗时不能称 LCP。
- 不修改源代码、不推送、不触发部署、不回抓赛事源站。仅提交进度与报告证据。
