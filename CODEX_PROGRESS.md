# CODEX_PROGRESS

更新时间：2026-09-17（Asia/Shanghai）

## 最终目标

完成线上产品全面评审，并按用户“授权 A+B 继续工作至交付”落实可信复盘主路径、统一质量/指标/覆盖状态及比分争议机制，验证真实生产发布。C/D/E未授权实施，方案保留在评审报告。

## 当前计划

1. 基线评审与优化方案：完成。
2. A1共享棋局质量与完整性、A2生产CORS/固定样本验收：完成并上线。
3. B1指标/状态契约、B2比分争议机制：完成并上线。
4. 完整构建、CI、R2认证、部署、普通URL验收：完成。
5. 文档链接修复部署及最终交付记录：完成。

## 已完成

- 评审：`docs/reviews/REVIEW_2026-09-15_PRODUCTION_PRODUCT.md`；原始线上证据`review-evidence/2026-09-15-production.json`。
- 共享质量模块与版本化事实；默认包只含合法非空主线，原始PGN/Result保留；清理18个仅含非法记录的旧阶段包；原档完整与可复盘完整分离。
- R2正式origin加入allowlist，原三个origin保留；4origin×GET/HEAD通过。
- v1旧计数兼容；新增归档/可复盘/排除和参赛/公开参赛/有谱赛事字段；首页、覆盖页、专题共享状态和独立可复盘指标。
- 稳定结果争议ID、当前事实绑定哈希、人工证据哈希门禁与CSV裁决台账。46条pending（原43条全部纳入），争议统计资格关闭，原始比分不改。
- 统一重建及部署质量门禁、固定生产canary；内部76520条事实确定性gzip，解决GitHub100MiB单文件上限，内容未删减。
- 457项Python测试通过（2跳过），9项前端测试通过；compileall、shell语法、diff检查通过。隔离完整31步构建与云端冷构建通过。
- 生产输入`b06d347755ee76e19ffbf005ea835a7acdb293f6`；派生提交`3f0864b716ed27c9315dc8a6927c651414e85ccc`；快照`20260916T121909Z-7a2d1aed`。
- CI35095031758、重建/R2认证35095031805、部署35097317431全部success。云端及本机独立普通URL canary通过，生产首页/app.js/data-status.js/styles.css正文与本地提交逐字节一致。
- 线上样本：1458883原162/可复盘150/排除12；1227491为89/86/3；居文君717/711/6；侯逸凡346/345/1；许翔宇572/559/13。原始赛事归档哈希保留。
- 全库76520归档事实/75177合法/1343排除；公共目录74550可复盘独立局；80270棋手关联不能称独立局数；registry12013人且正文匹配快照。
- 文档链接修复提交`399f3a3024154a1d956178737b07204bedccb98b`已推送，只修API文档指向质量契约的GitHub链接。

## 当前正在做

A+B交付完成。文档部署35159492309及其生产canary成功；普通/API.md已确认新链接。最终报告、证据和本文件完成并提交。文档CI35159492324是相同产品代码的重复验证，产品输入CI35095031758已经成功。

## 待完成

- A+B实施和发布无待完成项。
- 后续人工任务：46条比分争议逐局提供证据核定；浏览器工具恢复后补真实交互/移动端验收。两项不得冒充已完成。

## 重要技术决策

- 代码只在`/Volumes/AI/coding/kimi-code` main；collector `/Volumes/AI/coding/kimi`不pull/rebase/清理。两处根进度保持一致。
- 机器产物只经统一构建器；registry始终是身份与官方等级分权威；不回抓赛事来源，不切换Cloudflare shadow，不配置付费资源。
- 原始归档、合法可复盘、结果争议、结束状态、来源公开范围、全台覆盖分别计量。未知/残差保守保留，不能用归档认证豁免合法性。
- 保持旧API字段含义；新增明确字段。裁决通过`data/community/game-result-decisions.csv`及`data/manual/result-evidence/`证据，不能猜比分。
- 发布必须精确输入SHA→统一快照→R2认证→部署→普通URL验证；失败只恢复失败阶段。GitHub命令显式设置大小写HTTP/HTTPS代理127.0.0.1:15236。
- 每个可运行阶段均独立提交；最后文档提交避免与正在运行的重建竞争。

## 已知问题

- 46条比分争议仍pending；1059818/1/62的稳定ID`result-da142782be4833c34acb6cd8`，表0-1与原PGN1-0，缺独立裁决证据。
- 1356509广播范围仍未核清，保持source-published-coverage-unresolved/unknown，未宣称全台完整。
- 浏览器控制持续超时，真实视觉/点击/移动端与性能指标未验收；HTTP/源码/自动测试不替代浏览器实测。
- 原始非法棋谱保留，默认包已排除；修复原始记谱需来源证据。
- collector大量既有运行时/数据改动保持原样。code既有未跟踪项保持：`.workbuddy/`、`HANDOFF_2026-07-22.md`、`REVIEW_2026-07-24.md`、`data/manual/event-time-controls.csv`、`docs/reviews/REVIEW_2026-08-01_完整项目评审.md`、`experiments/estimated-ratings/Scripts/`、`experiments/estimated-ratings/docs/`。

## 接续入口与证据

1. 本文件 + git status/diff + 最近commits足以恢复；不重做已完成工作。用户已授权A+B上线，无需重复确认。
2. 最终交付：`review-evidence/DELIVERY_2026-09-16_AB.md`；生产canary与静态资源证据：`review-evidence/2026-09-17-production-{canary,assets}.json`。
3. 临时完整日志：`/tmp/chessdb-delivery-20260916/`；隔离构建`build/`有生成产物，不应提交到collector。
4. 历史失败已修复：旧测试资源版本硬编码；18个残留阶段包；facts.json108.45MiB超限。旧来源提交运行无需重试。来源未重新抓取。
5. 文档契约：`docs/GAME_QUALITY_AND_METRICS.md`；重建35095031805、部署35097317431回执可以恢复生产证据。
