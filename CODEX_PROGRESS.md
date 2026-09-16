# CODEX_PROGRESS

更新时间：2026-09-16（Asia/Shanghai）

## 最终目标

用户已授权报告 A+B，继续工作至交付：修复 R2 正式域名跨域读取，统一棋局合法性、完整性和结果争议状态，统一指标与事件计数字段，完成测试、精确 main 发布、统一重建/R2 认证、部署及正常生产 URL 验证。
本次不实施 C/D/E 全量体验或架构改造，不回抓赛事来源、不改变 registry 权威、不切换 Cloudflare shadow 为生产。

## 当前计划

1. A0 基线与设计：受控同步代码 main，确认数据契约和受影响出口，补充进度并提交。
2. A1 统一质量：事实层版本化解析/主线/终局质量；默认棋手包与赛事包一致过滤；完整性按合法配对覆盖判定；保留原档证据。补回归、验证并提交。
3. A2 可信主路径：正式域名 R2 CORS 配置和生产 canary；新旧 origin 与 fallback 均验证，提交。
4. B1 统一表达：指标字典、明确的参赛/有谱赛事计数、API v1 兼容新增字段；主站/专题/API 共用覆盖状态，测试并提交。
5. B2 结果争议：稳定逐局问题 ID、人工裁决输入、证据门禁、公开争议标记和统计排除；处理高优先级样本为有证据的裁决或明确待核，绝不猜比分。测试并提交。
6. 交付：必需回归与完整隔离快照构建，精确推送 main，追踪 CI→rebuild→R2→deploy，核对线上普通 URL、hash/MIME、CORS、质量/指标样本。记录最终回执并提交。

## 已完成

- 前轮评审报告：`docs/reviews/REVIEW_2026-09-15_PRODUCTION_PRODUCT.md`，证据：`review-evidence/2026-09-15-production.json`；最终评审提交 `9238a5c6aa`。
- 已读取当前 AGENTS.md 与 Scripts/local/README.md；通过 `code_workspace.sh sync` 同步检查，main 未落后，本地有 5 个尚未推送的评审文档提交。
- 历史线上基线：snapshot `20260915T063851Z-1850db6a`，input `482f395360a302037bac27073e3121dc96bf7859`，deploy run `34943857597`。
- 已确认现有事实层保留全量原始记录，但合法主线过滤只在赛事库；完整性与棋手包尚不消费同一质量结论。

- A1 已实现 `game_quality.py` 版本化合法性/非空/结束状态，事实层写入 quality；赛事包消费共享判定；棋手默认包过滤非法/空记录，原始 metadata 与旧计数保留，新增 archived/playable/excluded 计数；完整性不再以归档认证旁路主线校验；公共 localGame 带质量。
- A1 回归：82 项测试通过（quality/player_facts/event_library/completeness），compileall、diff --check 通过；包含“归档已认证但非法 SAN 不能算可复盘完整”反例。

- A2：通过 Wrangler 实际更新 chess-data 桶 CORS，保留旧 allowlist 并加入正式域名；4 个 origin × GET/HEAD 共8项 canary 已通过，回执 `/tmp/chessdb-delivery-20260916/cors-verified.json`。新增部署后生产 canary（包正文 hash/MIME/合法性、版本409、指标、快照）。
- B1：保留 v1 旧含义，新增 archived/playable/excluded 与 participationEventCount/pgnEventCount；首页改用目录独立可复盘局数；主站与专题消费同一 replayCoverage 契约。
- B2：稳定结果冲突 ID/当期绑定哈希、证据哈希门禁与人工 CSV；未核定保持 disputed/pending，PGN 标签和公开详情标识，统计资格关闭。无证据的43候选未擅自裁决，等待全量重建重算（包含白黑方向排错）。
- 必需回归及新增质量/争议/事实/目录/完整性测试共297项通过；compileall、bash -n、diff --check、3个前端文件语法检查通过。指标字典与裁决流程见 `docs/GAME_QUALITY_AND_METRICS.md`。

- 完整离线构建（输入28ed7d9cf5）30步全部通过，snapshot `20260915T231320Z-13dbeafd`；验证1458883为162归档/150可复盘/12排除，1227491为89/86/3，居文君717/711/6；原始归档保留。目录独立可复盘仍74550，全部合法归档75177，排除1343；棋手关联80270保留。
- 新的统一冲突检测得到46个pending（此前公共详情审计43个），1059818/1/62稳定ID `result-da142782be4833c34acb6cd8`，未猜测比分。
- 补充 `validate_game_quality.py` 对事实→棋手包→API→参赛计数→公共指标做全量门禁；修正首次实现中新建 bucket 尚未赋参赛计数的问题，回归覆盖空bucket构建及非法PGN排除但原始计数保留。
- 454项广泛单测通过（2跳过），另12项最新投影回归通过；前端6项测试通过。浏览器9/16再次getState超时，交互仍未核验。

- 最新455项Python单测通过（2跳过）。前端9项通过，覆盖零可复盘数量不得回退成归档数、原档非法时禁止默认复盘fallback、共享覆盖状态优先级。
- 完成前端出口收尾：赛果行优先用过滤后的目录包，原始归档不再作为含非法记录时的默认复盘回退；棋手页明确显示归档/可复盘/待修复；复盘器标注争议。部署canary补充registry正文快照哈希、原始归档保留与1059818争议样本。
- 第二次隔离完整构建输入 `8c45ddc3a7d7bed3d59e92c6dd81f03954ccd4fd` 正在执行；`/tmp/chessdb-delivery-20260916/final-build.log`。已受控sync复核远端main无新增输入，当前main尚未推送。

- 第二次完整隔离构建通过：snapshot `20260916T053913Z-7a36d339`，输入 `8c45ddc3a7d7bed3d59e92c6dd81f03954ccd4fd`，31/31步骤成功，质量门禁核对76520事实与3127棋手；registry权威、公共隐私、快照一致性全部通过。
- 发布前收尾：部署阶段再次执行全量质量门禁；覆盖页改用同一独立可复盘总数，API文档说明兼容口径；公共详情/指标携带snapshotId，生产canary严格拒绝混合快照。

- GitHub首轮：c306fa98ce170c2bd33f1a6dc99d47989f819853已推送；CI35060901570，rebuild35060901566，直接deploy35060901594，community35060901545通过。旧快照直接deploy应被GAME_QUALITY_REBUILD_REQUIRED门禁拒绝，需等待成功重建后正式deploy。
- 发现并修复warm构建残留：目录实际7505个包而有效清单7487；18个只有非法棋局的年龄段包未清理。prune改用共享replayable规则，新增全量文件集合门禁及回归；12项质量/R2测试通过。为避免旧候选进入认证，已取消首轮rebuild并准备后继提交。

- 首轮CI35060901570失败原因已定位：2个旧测试写死app/styles资源版本号；已保留版本化和加载顺序断言、去掉旧日期耦合，并把新增前端质量测试接入CI。重跑全部455项通过（2跳过）。
- b9760e360f定向重新生成棋手包/事件详情/指标后，质量门禁76520事实/3127棋手通过，文件集合7487/7487零残留，公开隐私通过。相关日志`final-projections.log`、`final-details.log`、`prepush-unit-tests.log`、`prepush-frontend-tests.log`均在临时交付目录。

- 第二轮云端：CI35061248675成功；rebuild35061248668在Commit rebuilt indexes失败，日志明确GH001，facts.json108.45MiB超100MiB。R2阶段已成功，旧线上快照继续服务；不需要回抓来源。失败日志`oversize-failure.log`。
- 修复内部事实存储：标准确定性gzip、manifest校验压缩字节SHA256、共享reader兼容旧JSON、构建后删旧大文件，质量门禁限制单文件100MiB。全量实际数据精确往返，压缩11673528字节，457项测试通过（2跳过），其中新回归覆盖时间戳稳定、旧文件清理、hash篡改拒绝。

- 最终生产候选：b06d347755ee76e19ffbf005ea835a7acdb293f6（压缩修复+远端贡献漏斗更新的无冲突合并），已推送；重建35095031805、CI35095031758。待提交的docs/API.md仅修复指向私有部署排除文档的链接为GitHub地址，须在当前重建提交完成后再发布，避免并发输入冲突。
- 预部署补验：R2三名棋手新包711/345/559局通过正文hash/合法性/CORS；原43条比分候选全部命中新46条队列；部署产物2385文件/516632KiB通过大小与隐私门禁。最终交付报告草稿`review-evidence/DELIVERY_2026-09-16_AB.md`明确尚待生产回执。

- 最终候选 CI35095031758 已全部成功，包含冷启动完整快照重建；生产rebuild的派生重建已成功，现进入R2全量认证。

- 生产链成功：CI35095031758、rebuild35095031805、deploy35097317431均success；派生提交3f0864b716ed27c9315dc8a6927c651414e85ccc，snapshot20260916T121909Z-7a2d1aed，input b06d347755ee76e19ffbf005ea835a7acdb293f6。云端canary与本机独立生产canary均成功。

## 当前正在做

A+B已上线且普通生产URL验证通过。正在发布API文档链接修复，之后补齐最终交付报告及进度回执；不再改变产品或数据逻辑。

## 待完成

- 等待 rebuild35095031805 完成 R2 认证、派生提交和部署，运行普通生产 URL canary，记录精确快照及回执。
- 当前重建提交后发布 docs/API.md 的文档链接修复，并跟踪该次部署。
- 完成交付报告与最终进度提交。A1/A2/B1/B2实现、离线完整构建及线上CORS已完成。
- 46 个比分冲突保持明确 pending，后续人工裁决须逐局提供证据，不批量反转结果。
- 浏览器工具持续超时；可恢复则补验核心路径，否则明确记录交互未验收。

## 重要技术决策

- 代码仅在 `/Volumes/AI/coding/kimi-code` main；采集 `/Volumes/AI/coding/kimi` 不 pull/rebase/清理。两处根 CODEX_PROGRESS.md 保持一致。
- 只精确提交本任务文件，保留既有未跟踪实验/人工数据。机器数据只由构建器生成，不手改。
- 原始 PGN 与字节认证保留；合法可播放、归档记录、结果争议、结束状态、公开范围/全台覆盖独立表达。
- 保持 v1 已发布字段含义，新增明确质量/指标字段；不能用减少原档事实或删错局来伪装修复。
- 每个可运行阶段立即更新进度并 git commit。GitHub 终端请求显式注入大小写 127.0.0.1:15236 代理。
- 发布沿现有统一快照构建与 R2 认证；失败只恢复失败阶段。

## 已知问题

- 1458883 原包 162 局/12 非法，目录150，但 playableComplete=true；1227491 原包89/3非法/目录86；8603006 棋手包717/6非法。
- 1059818 第1轮62台，成绩0-1与localGame 1-0冲突；真实结果未获独立证据，不能擅自决定。
- 新站 origin 未在 R2 CORS allowlist，旧站正常；有同源 fallback，不能说全站不可用。
- v1 与 bootstrap eventCount 口径不同；80,270 是棋手关联次数，74,550 是目录可播放独立局数（均为评审基线）。
- 本任务开始前 code 工作区未跟踪项：`.workbuddy/`、`HANDOFF_2026-07-22.md`、`REVIEW_2026-07-24.md`、`data/manual/event-time-controls.csv`、`docs/reviews/REVIEW_2026-08-01_完整项目评审.md`、`experiments/estimated-ratings/Scripts/`、`experiments/estimated-ratings/docs/`。保持原样。
- collector 有大量历史 tracked/untracked 运行时及数据改动，不能广泛暂存或同步。

## 接续入口

1. 查看本文件、两工作区 git status/diff、代码工作区最近 commits。用户已授权 A+B 实施及交付，不需再次请求实施/上线确认。
2. 从“当前正在做”继续；每阶段完成记录实际命令/结果/下一步。
3. 证据与隔离构建临时目录记录在后续进度条目；若临时数据丢失，用精确提交和线上不可变对象恢复，禁止回抓来源兜底。
