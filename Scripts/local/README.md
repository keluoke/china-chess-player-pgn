# 维护者本地数据采集

所有网络采集只能由维护者在本机住宅网络执行。GitHub Actions、社区贡献工具和
公开 runner 不访问 Chess-Results、FIDE 或 Lichess；CI 只接收经过校验的发布
manifest，并离线重建派生索引。

## 数据边界

- `data/community/`、`data/manual/`：人工维护，抓取脚本禁止写入或自动提交。
- `data/generated/`：允许发布的机器投影；禁止存放 Chess-Results 原始 HTML。
- 本地私有运行区：原始响应、日志、配额和诊断文件，默认位于
  `~/Library/Application Support/ChinaChessPlayerPGN/`。
- Chess-Results 为 `full-data`（顶层契约 AGENTS.md，目标是赛事/对局数据完备性）：
  维护者本机全量抓取（名单、逐轮对阵、结果、最终排名、对局 PGN），本地清洗
  校验后与已发布副本比对合并，变化部分随 manifest 管线发布，不标注来源。
  原始 HTML 永不入库。旧 link-only 政策已退役（可用环境变量
  `CHESS_RESULTS_RELEASE_POLICY=link-only` 临时禁发）。发布路径：
  `data/generated/chess-results-event-details/`（结构化赛事数据）、
  `data/generated/chess-results-event-pgn/`（全赛事 PGN 归档）、
  `docs/data/pgn/chess-results/`（按棋手拆分的 PGN）。
- Lichess Broadcast 数据保留 CC BY-SA 4.0 名称、许可证 URL 和来源署名。
- 国内赛事 PGN 分为“全台完整”和“公开直播范围完整”：来源只公开前若干台时，
  只要实际公开的每局都已唯一匹配归档，可标 `source-published-complete`；
  该状态不得对外表述为全台完整。没有公开链接标 `not-published`，空响应或少局
  仍按缺口处理。
- 亚少赛、世少赛通过 Lichess Broadcast 月度库补充：严格校验系列、慢棋项目、
  年份/日期、年龄/性别组、轮次和双方身份，歧义局拒绝；投影写入
  `docs/data/bulk/lichess-events/` 并保留 CC BY-SA 4.0 署名。月度 `.pgn.zst`
  原档只在本地/R2，不能被加入 Git 发布 manifest。
- registry 是姓名和等级分的唯一权威；`name-corrections.csv` 在每次 FIDE 重建中
  最后强制应用，并在发布前再次断言。

## 唯一控制面板

双击仓库根目录的「一键抓取面板.command」或「一键抓取.app」，也可运行：

```bash
python3 Scripts/local/panel.py
```

启动器先读取 Git 的 `chessdb.workspaceRole`：从 `code` 工作区误开时会按
`chessdb.collectorRoot` 自动跳转到采集工作区；路径未配置或角色异常则
fail-closed。面板进程和 `refresh.sh` 入口还会分别复核角色，非 `collector`
一律报 `WRONG_WORKSPACE_ROLE`，不会创建 run、抓取来源或生成发布包。

这是唯一现行采集控制面。旧 `local-data-center`、`targeted_capture_panel.py`、
`targeted_series_capture.py` 和 `import_event_list.py` 已删除，不能再作为入口。
面板只监听 `127.0.0.1`，POST 请求带随机本地令牌。任务锁、run-id、状态和日志
都持久化在仓库外；关闭或重启面板不会遗失正在运行的任务。
面板服务断开时蓝色运行指示会立即停止；重开后若发现记录仍为 running 但对应
进程已经退出，会明确恢复为失败结果并保留日志/outbox 状态，不会误报仍在抓取。

面板顶部提供「抓取指定赛事」粘贴框：粘贴纯 TNR、`tnr123456` 或完整
chess-results.com 链接（一行一个，最多 10 场），前端立即标准化、去重并显示
chips；采集中逐场显示页面进度与缓存命中；成功后可打开只读本地清洗结果预览；
partial 目标可一键"续跑补缺页"。队列汇总栏区分：
历史公开完成、新私有完成、待抓、部分（可续跑）、等待重试、已隔离、需解析器。

“按棋手发现最近赛事”使用 FIDE ID 查询最近参赛记录。留空时按“最久未检查优先”
轮询 10 名中国棋手，每人最多取最近 5 个 TNR；也可显式输入一个或多个 FIDE ID。
查询原始响应、棋手 ID 与发现关系只写仓库外私有区，候选 TNR 合并进待抓池，但
不会自动抓赛事详情或发布。`data/community/tournament-target-overrides.csv` 是
误目标门禁：聚合名单、重复入口和确认不应抓取的 TNR 会在调度前被压制。

每次 `event-queue` 结束后，“本批结果”固定保留最近一批的完整清单，即使随后
执行 `deliver`、`receipts` 或健康检查也不会被覆盖。它分别展示完整成功与
部分/失败目标、逐场人数/轮次/排名、逐场直接更新文件数、整批文件数/字节数，
以及 outbox → main → 部署 → 线上验证的实际阶段。混合批次显示为“部分完成”，
不再笼统标成整批 `failed`；发布状态只取 manifest 和 delivery receipt，不从
采集退出码推断。成功清洗且无 manifest 才显示“与已发布数据一致”；运行在发布
准备前失败且无 manifest 时显示“采集/发布失败，未生成发布包”，不得误报成
“没有数据变化”。

“已抓赛事”按赛事日期列出本机已有清洗结果，并合并显示抓取状态、人数/轮次/
排名、完整性门禁和发布阶段；支持搜索、日期/采集时间排序、发布状态筛选与分页。
面板把发布拆成两个互不授权的开关：“GitHub 生产自动推进”默认开启，只执行
`publish`/`receipts`；“Cloudflare 自动影子双写”默认关闭，只有维护者确认开启后，
才把同一不可变 outbox 双写到影子 Worker。两个开关都不会自动访问任何数据源。
网络型投递失败按 30 秒、120 秒、300 秒退避重试；基线冲突、manifest
错误、路径/哈希错误和已证实的线上哈希不一致会停止自动重试并列入“需要人工处理”。
影子逻辑包若超过 384 文件、64 MiB 或单文件 16 MiB，会在本机预检中标为
`ineligible`；不会上传、不会拆包，也不会阻塞 GitHub 生产发布。
合格逻辑包内部按 10 文件分片登记与合并，分片不产生可见快照；所有分片完成后
才原子切换一个影子 snapshot。因此 50 文件赛事包可正常双写，仍保持单一回执。

## 安全命令

```bash
# 建议先运行；探测三个来源直连 + GitHub direct/proxy/API 投递路线
bash Scripts/local/refresh.sh health
bash Scripts/local/refresh.sh health -- --offline

# 安全常规刷新：FIDE 满 25 天才更新，并采集发布队列前 3 个赛事。
# GitHub 投递失败只标记 delivery-pending（发布包留在 outbox），继续事件采集。
bash Scripts/local/refresh.sh all

# FIDE：唯一临时下载 -> ZIP/语义/人数/分片/勘误校验 -> 原子晋升
bash Scripts/local/refresh.sh registry

# 只发现候选 TNR；不抓赛事详情、不生成发布包。留空轮询下一批 10 名棋手。
bash Scripts/local/refresh.sh discover-events
bash Scripts/local/refresh.sh discover-events -- 8600000 8600001

# Chess-Results：全量抓取（raw 只写仓库外私有区）→ 本地清洗 → 与已发布副本
# 比对合并（一致跳过，冲突以本地清洗数据为准）→ 赛事 PGN 先上传 R2 并回读
# SHA-256 → manifest 发布；R2 失败则整批 fail-closed，逐页原子落盘，坏目标隔离绕行
bash Scripts/local/refresh.sh event-queue
bash Scripts/local/refresh.sh event-queue -- --from-queue 10
bash Scripts/local/refresh.sh event-queue -- 1110333
bash Scripts/local/refresh.sh event-queue -- https://chess-results.com/tnr1110333.aspx
# 已确认结构缺口时，重新直连抓取赛事页（不复用旧 raw）；--no-pgn 只修复名单/排名/对阵
bash Scripts/local/refresh.sh event-queue -- 1110333 --overwrite --force-source --no-pgn
# 解析器更新后离线重放已保存的私有 raw，零来源访问
bash Scripts/local/refresh.sh event-queue -- 1110333 --replay --overwrite
bash Scripts/local/refresh.sh candidates -- --tournament-id 1110333

# Lichess：暂存、验证、BY-SA manifest、精确发布
# bulk 同时重建亚少赛/世少赛的严格 TNR 交叉归档；只发布 manifest、youth
# 投影和 lichess-events 投影，不发布本地月度 .pgn.zst 原档。
bash Scripts/local/refresh.sh bulk
bash Scripts/local/refresh.sh bulk-full

# 推进 GitHub 生产投递和已显式启用的 Cloudflare 影子回执，不重新抓取
bash Scripts/local/refresh.sh publish
# deliver 仅为命令行兼容别名
bash Scripts/local/refresh.sh deliver
# 仅推进已授权且已开始的影子回执，不触碰 GitHub
bash Scripts/local/refresh.sh shadow-publish

# 同步云端回执：查询 ingest/rebuild/deploy workflow 结论并校验线上文件哈希
bash Scripts/local/refresh.sh receipts

# 显式回填一个已经存在的 outbox 包到 Cloudflare 免费层影子服务；不访问数据源，
# 不改变 GitHub 生产发布状态。默认使用契约内影子 endpoint（可由
# CLOUDFLARE_INGEST_URL 覆盖）；HMAC secret 默认从 macOS Keychain service
# china-chess-cloudflare-ingest-shadow 读取。
bash Scripts/local/refresh.sh shadow-deliver -- 20260812-081901-9ae22db0

# storage-migrate 同时处理 data/generated/chess-results-event-pgn 与
# docs/data/pgn，并在共享回执中分别记录 objects / playerObjects。
# 将当前静态 PGN 树递归上传到 R2，逐对象校验 SHA-256，并只通过
# local-data 发布合并回执；不访问任何赛事来源。默认源为 docs/data/pgn，
# 可由维护者设置 R2_PGN_SOURCE_ROOT 指向同一 main 快照的干净代码工作区。
bash Scripts/local/refresh.sh storage-migrate
# 赛事归档源可用 R2_EVENT_PGN_SOURCE_ROOT 覆盖；棋手 PGN 树仍用 R2_PGN_SOURCE_ROOT。

# 首次浏览器切流前用已登录的 Wrangler 配置桶级 CORS；策略文件可评审，
# S3 对象写入密钥不需要也不应承担桶配置权限。
npx wrangler r2 bucket cors set chess-data --file Scripts/local/r2-cors.json

# 显式接管中断后遗留、尚未进入 manifest 的机器产物并发布；逐文件做
# JSON/PGN 格式检查，不自动丢弃、回滚或重抓
bash Scripts/local/refresh.sh recover-events

# 仅本地诊断，不自动提交或推送
bash Scripts/local/refresh.sh reindex

# 查看统一身份审核队列（只读，不改 registry/人工 CSV）
python3 Scripts/local/identity_review.py --limit 30
python3 Scripts/local/identity_review.py --type domestic-fide-link --limit 20
python3 Scripts/local/identity_review.py --show <candidateID>
```

`crawl*`、`pgn*`、`events*`、`aliases`、`promote`、`reconcile`、`verify` 和
`contrib` 已从一键入口退役。社区只提交赛事 URL/tnr、优先级、身份勘误、联邦
变更证据及质量问题，不提交自动抓取文件。

Python 依赖不写入 Homebrew/system Python。入口发现缺少依赖时，在仓库外状态区
`~/Library/Application Support/ChinaChessPlayerPGN/python-runtime/` 创建私有
venv 并切换本次任务；这兼容 PEP 668，安装错误会原样进入 run 日志。赛事采集和
`recover-events` 只把本批新增/变化的赛事 PGN、棋手拆分 PGN 上传 R2 并 HEAD
校验 SHA-256，随后才允许生成 manifest；已有 R2 回执按对象 key 合并保留。

## 采集语义（Chess-Results）

- **逐页检查点**：每个 HTTP 成功页面先原子写入
  `raw/chess-results/tnr<id>/<kind>.html.gz`（附 `pages.json` 的 URL/SHA-256
  元数据），然后才解析。解析失败永远留有可离线复现的证据。
- **续跑**：中断或失败后再次运行同一目标，只补缺页；已通过哈希校验保存的
  页面通过缓存复用，重复请求 = 0。
- **单目标隔离**：一个赛事失败不会终止批次；失败被记录进 capture-state 后
  继续下一个目标，批处理以退出码 4 表示部分成功（`PARTIAL_FAILURE`）。
- **预检先于抓取与发布**：预检失败时不会访问来源、不会调用发布收尾、不会生成
  manifest/outbox。发布器还会独立要求本次运行存在有效的
  `worktree-baseline.json`，因此旧机器文件不能被误包装成新批次。
- **请求可追溯**：显式输入的 TNR 在任务创建时即写入 `run.json`；即使预检失败、
  尚无 `result.json`，面板也会显示原计划目标数和“抓取未开始”，不再显示 `0/0`。
- **错误细分**：`EVENT_EMPTY`（空赛事，隔离 7 天）、`PAIRINGS_NOT_PUBLISHED`
  （无逐轮公开，允许 standings-only partial）、`TEAM_FORMAT_UNSUPPORTED`
  （团队赛轮次页）、`PARSER_LAYOUT_CHANGED`（真实布局回归，需更新解析器）、
  `ROUND_COUNT_UNKNOWN`（轮数不可发现，保留 partial）、网络类错误
  （指数退避 `retry-wait`）。结构错误默认零网络重试；同一结构错误连续 2 次
  进入 `quarantined`（7 天或 parserVersion 更新后解除）。
- **格式探测**：起始名单先试 `art=0`，失败后试团队赛 `art=15/16` 名单；
  轮数由标题、页面 `rd=` 链接和队列元数据三路交叉验证。
- **capture-state v2** 记录每个目标的 `status`（complete/partial/retry-wait/
  quarantined/unsupported）、`attempts`、`errorCode`、`failedPage`、
  `pagesFetched/pagesExpected`、`nextRetryAt`、`parserVersion`；面板与调度器
  使用同一份口径。
- **PGN 判定**：`source-published-complete` 表示来源实际公开的直播棋谱全部入库，
  可少于全台；`full-board-complete` 才表示全台。分母来自实际公开链接/广播，
  不把“前十台”写死；每局必须与轮次、台次和双方 playerNo 唯一匹配。
- **FIDE Event 补源**：每场赛事都从规范域名额外请求独立的
  `?lan=1&art=0&turdet=YES` tournament-details 页面，并跟随服务端跳转；禁止依赖
  普通名单/排名页偶然出现的字段，也禁止固定 `S1`/`S2`/`SNode`。该详情页存在唯一
  `FIDE-Event-ID` 且 Chess-Results 未公开 PGN 时，采集器才访问 FIDE 官方赛事页并
  跟随其 PGN 下载链接。
  每局必须按轮次及双方 FIDE ID（姓名仅作唯一回退）匹配已抓取对阵，结果一致；
  歧义、错配、重复局或非 FIDE 域名链接整包拒绝。无 ID 或 FIDE 未发布 PGN 仍
  保持 `not-published`，不得把补源失败伪装成空棋谱或全台完整。
  每次 PGN 尝试另写 `data/generated/pgn-source-attempts/tnr<ID>.json` 独立事实；
  它随赛事 manifest 发布并在云端重建状态索引。严格拒绝保留具体错误码和
  `fide-event-id` 路径，前台标为 `source-published-missing`，不会误降为
  `not-published` / `none-expected`；每赛事一文件也避免共享索引的并发覆盖。
- **对阵引用三态**：双方都能唯一回查名单才是普通对局；只有来源明确写出
  `bye` / `not paired` 才是轮空；其余缺 `playerNo` 的记录都是 unresolved，
  整场降为 partial，并进入 `repair-pairing-player-numbers` P0 队列。奇数名单
  的 `minRoundRosterCoverage < 1` 不单独触发降级。
- **Lichess 交叉比对**：只接受标准慢棋亚少赛/世少赛的唯一匹配，FIDE ID 优先、
  规范化姓名回退。manifest 必须保留每个广播容器的总局数、匹配数、未匹配数及
  歧义拒绝数；未匹配残差或范围未验证时标
  `source-published-coverage-unresolved`，不能静默视为完整。

## 发布事务

### Cloudflare 免费层影子发布

`cloudflare/ingest/` 是机器数据直达 Cloudflare 的第一阶段影子服务。它不取代当前
GitHub 生产链路；本机 outbox 可双写到该服务做 SHA-256/三方冲突/回执对账。
免费额度、鉴权、不可变快照和生产切流门禁以
`docs/CLOUDFLARE_INGEST_CONTRACT.md` 为准。达到任一硬上限时影子发布必须停止，
不得降低校验、借用生产桶或自动切换付费计划。

每次运行创建独立目录：

```text
runs/<run-id>/
  run.json
  run.log
  error.json              # 结构化失败阶段、错误码、提示和证据
  raw/
  extracted/
  staging/
  diagnostics/
  progress.json           # 采集中的逐场页面进度
  release-manifest.json   # 仅存在于可发布运行
```

FIDE/Lichess/Chess-Results 发布前必须满足：

1. 发布归属路径和 Git 暂存区在运行前是干净的；
2. 下载写入唯一临时文件，长度、文件签名和内容校验通过；
3. 输出先写 staging，再原子晋升或逐文件原子 overlay；
4. `run_manager.py` 生成精确路径、操作和 SHA-256 manifest；
5. Git 只暂存 manifest 中的文件，不再执行宽目录 `git add`；
6. 提交后立即把发布包（manifest + 哈希文件 + delivery 状态）写入仓库外
   outbox；采集事务到此结束，不等待 GitHub；
7. deliver 阶段优先通过 GitHub Git Database API 投递 manifest 精确列出的文件，
   避免采集结束后为整个 Git 历史打包；API 不可用时再按 direct → 显式代理 →
   macOS 系统代理 → 常见本地代理逐路线实测 Git smart HTTP。两种传输复用同一
   `run_manager.validate_manifest` 与三方基线门禁，无第二套白名单。网络或投递
   失败只把发布包保留为 `delivery-pending`，不把已完成采集判成失败；
8. `ingest-local-data.yml` 按触发 push 的不可变 event SHA 摄入（不重读移动的
   分支头），先用 manifest 的 `baseBlobOid` / `baseSha256` 对每个路径完成
   baseline/current/candidate 三方检查；真实并发修改以
   `RELEASE_BASE_CONFLICT` 整包隔离且不修改 worktree/index。检查通过后才应用
   manifest 文件清单，并在 job summary 写入 run-id / source SHA / baseline /
   main commit 的 receipt；
9. Actions 离线重建索引和部署。ingest 必须把实际推入 main 的提交 SHA 作为
   `target_sha` 传给 rebuild；rebuild 精确检出并核对该提交。构建期间 main 若有
   更新，派生快照禁止 rebase 到新输入后继续发布，必须 fail-closed 并由新提交的
   rebuild 接管。`docs/data/snapshot.json` 同时记录本次
   实际输入提交 `inputCommit`。回执沿该提交可达的 local-release-manifest 历史
   核对发布 `runId`；即使后续发布替换当前 manifest，也只有确认该发布已进入
   线上快照的输入历史时才能推进到 `online-verified`。

发布准备使用与 preflight 相同的机器路径口径，包括 Git 忽略但实际存在的孤儿
产物；发现时一律 fail-closed，并在 `diagnostics/recovery-candidates.json` 写出
完整候选清单。只有维护者显式执行 `refresh.sh recover-events` 才会接管，工具
不会自动删除、回滚或重新抓取。运行目录按“最近 30 个 + 每类命令至少 5 个”保留；
本批结果同时写入对应 outbox，因此普通 run 被轮转后仍可在面板追溯。

GitHub 投递成功不等于发布成功。为避免投递完成后被慢回执查询阻塞，`publish` 负责
把发布包送达 `local-data`；`refresh.sh receipts` 独立通过 gh API 读取
ingest/rebuild/deploy workflow 结论，并从线上站点回取一个已发布文件校验
SHA-256，把 outbox 状态沿

```text
pending → pushed → ingested-to-main → indexes-rebuilt → deployed → online-verified
```

逐段推进；只有 `online-verified` 才算"线上已发布"。面板发布中心展示每个
run-id 的回执链接与当前阶段。任一云端阶段失败只重试该阶段，永不回抓来源。
线上站点地址默认 `https://china-chess-player-pgn.pages.dev`，可用环境变量
`CHINA_CHESS_SITE_URL` 覆盖。

任何 `data/manual`、`data/community`、`data/incoming`、原始 HTML/WARC 路径都会
被本地发布器和 CI 双重拒绝。

## 常见错误码

- `WRONG_WORKSPACE_ROLE`：从非 collector 工作区启动；入口在创建 run 和访问来源前
  已拒绝。请使用采集工作区的一键面板，或修复 `chessdb.collectorRoot` 配置。
- `DEPENDENCY_ENVIRONMENT_FAILED` / `DEPENDENCY_INSTALL_FAILED`：仓库外私有 venv
  创建失败或依赖安装失败；本次不会把缺依赖误报成“无数据变化”。
- `DIRTY_RELEASE_PATH`：机器发布路径已有未提交修改；本次不会访问来源或生成发布包。
  已校验的中断机器产物应使用 `recover-events` 接管，不能靠重新抓取消除。
- `RELEASE_BASELINE_MISSING` / `RELEASE_BASELINE_INVALID`：本次预检基线缺失或损坏，
  发布器已拒绝生成 manifest/outbox；先修复运行环境，无需投递或重新抓取。
- `FIDE_DOWNLOAD_OR_VALIDATION_FAILED`：新文件无效；有效 last-good 不会被替换。
- `SOURCE_CIRCUIT_OPEN`：连续失败后熔断，等待后再试。
- `VISIT_BUDGET_EXHAUSTED`：兼容旧运行记录的状态码；当前采集不设置本机日访问额度。
- `PARTIAL_FAILURE`：批次部分目标失败并已隔离；成功赛事已保留。
- `FINAL_STATE_WRITE_FAILED`：任务主体已结束，但最终运行状态连续三次写入失败。
  对 `discover-events`，若本次 `result.json` 已完整落盘，面板会明确显示检查人数和
  候选数；候选池已更新，不要重新访问来源。下次任务会自动清理陈旧锁；若重复
  出现，检查本次 `diagnostics/final-state-error.log`。
- `PARSER_LAYOUT_CHANGED`：来源页面不再符合解析器预期；raw 证据已保留，更新
  解析器后 `--replay` 离线重放。
- `PAIRING_REFS_MISSING`：普通对局方无法按编号或名单内唯一姓名回查；该赛事
  不得作为完整赛事保存，解析器修复后用 `--replay` 离线重放。
- `RELEASE_BASE_CONFLICT`：远端 main 在采集基线后修改了同一路径，候选整包
  已隔离且尚未写入工作树；先人工核对冲突，不得回抓来源。
- `VALIDATION_REGRESSION`：人数、分片、勘误或解析行数出现异常。
- `COMPLIANCE_POLICY_BLOCKED`：操作违反数据边界（原始 HTML 入库、人工数据
  路径、或环境显式设为已退役的 link-only 模式）。
- `GIT_DNS_FAILURE` / `GIT_TLS_FAILURE` / `GIT_PROXY_FAILURE` /
  `GIT_CONNECT_FAILURE`：网络类投递失败，deliver 会自动轮换下一路线。
- `GIT_AUTH_FAILED` / `GIT_REMOTE_REJECTED`：换路线无用；重新登录 gh 或检查
  远端策略。
- `GIT_PUSH_FAILED`：发布包已留在 outbox，恢复网络后运行 `deliver`。
- `ONLINE_HASH_MISMATCH`：云端部署完成，但线上文件实际哈希与本发布包不同；
  通常表示该包已被更新快照取代。自动推进会停止重试，需按 run-id 核对线上快照
  与后续发布包，不要重新抓取来源。

## 代码发布（与数据发布分离）

代码修改永不通过 `local-data` 投递，也不再使用采集工作区提交。首次在采集
工作区运行以下命令，会从远端 `main` 创建同级的轻量 `<repo>-code` 工作区：

```bash
bash Scripts/local/code_workspace.sh init
```

代码工作区使用 blobless partial clone，并 sparse 排除 `data/generated/`、
`docs/data/`、`docs/api/`；它可以正常 fetch/fast-forward，不承载采集状态、
机器产物或 outbox。日常在代码工作区运行：

```bash
# 查看角色、分支、代理、体积和 Git 状态
bash Scripts/local/code_workspace.sh status

# 没有未提交的 tracked 改动时同步远端 main（未跟踪实验文件不会阻断）
bash Scripts/local/code_workspace.sh sync

# 提交后推送 main
bash Scripts/local/code_workspace.sh push
```

**默认直接在 `main` 上工作和提交**，不为普通任务创建分支；仅当用户明确要求
PR/隔离时才建短命分支。普通 Git 不通时使用 API 发布器（以远端 main 为基准，
先做 dry-run 三方合并冲突检查）：

```bash
python3 Scripts/local/publish_code_via_api.py --help
```

## 本地不 pull

“永不 pull/rebase”只约束采集工作区：它继续保留本地采集提交与仓库外 outbox，
绝不能为追赶云端 `main` 而 pull/rebase/重 clone。代码工作区则以远端 `main` 为
基线，可通过上述 `sync` 做 fetch + fast-forward。

采集工作区的本地提交链用于生成 release/outbox，并且仓库是浅/部分克隆；因此在
那里运行普通 `git status` 可能显示很大的 ahead/behind 数字。这个数字既包含本地
机器发布提交，也受 shallow 边界影响，**不是代码 main 的同步状态**，禁止用
pull/rebase 去“修平”。代码是否与仓库同节奏只以代码工作区为准：每次开始先
`code_workspace.sh sync`，始终在 `main` 提交；`push` 会先 fetch 并拒绝落后或
分叉的 main。`local-data` 只保留为机器发布传送分支，不用于普通开发。

本机的系统代理工具只对浏览器生效，终端不会自动继承。任何手工终端 GitHub
访问都必须显式设置代理（脚本同时设置大小写变量）：

```bash
HTTP_PROXY=http://127.0.0.1:15236 \
HTTPS_PROXY=http://127.0.0.1:15236 \
http_proxy=http://127.0.0.1:15236 \
https_proxy=http://127.0.0.1:15236 \
git fetch origin main
```

`code_workspace.sh` 会为代码仓库写入 repo-local `http.proxy`，因此侧边栏的提交
不受网络影响，推送也能使用 15236；终端命令仍由脚本显式注入代理，不能只依赖
系统设置或 `scutil`。采集投递脚本可以探测候选路线并自行注入，但代理只能传给
Git/GitHub API，绝不能传给 Chess-Results、FIDE 或 Lichess 来源请求。
