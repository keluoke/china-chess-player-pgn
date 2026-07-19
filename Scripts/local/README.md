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
- registry 是姓名和等级分的唯一权威；`name-corrections.csv` 在每次 FIDE 重建中
  最后强制应用，并在发布前再次断言。

## 控制面板

双击仓库根目录的「一键抓取面板.command」或「一键抓取.app」，也可运行：

```bash
python3 Scripts/local/panel.py
```

面板只监听 `127.0.0.1`，POST 请求带随机本地令牌。任务锁、run-id、状态和日志
都持久化在仓库外；关闭或重启面板不会遗失正在运行的任务。

面板顶部提供「抓取指定赛事」粘贴框：粘贴纯 TNR、`tnr123456` 或完整
chess-results.com 链接（一行一个，最多 10 场），前端立即标准化、去重并显示
chips；采集中逐场显示页面进度与缓存命中；成功后可打开只读本地清洗结果预览；
partial 目标可一键"续跑补缺页"。队列汇总栏区分：
历史公开完成、新私有完成、待抓、部分（可续跑）、等待重试、已隔离、需解析器。

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

# Chess-Results：全量抓取（raw 只写仓库外私有区）→ 本地清洗 → 与已发布副本
# 比对合并（一致跳过，冲突以本地清洗数据为准）→ manifest 发布；逐页原子落盘，
# 坏目标隔离绕行
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
bash Scripts/local/refresh.sh bulk
bash Scripts/local/refresh.sh bulk-full

# GitHub 网络失败后只投递 outbox 中的发布包，不重新抓取（push 为兼容别名）
bash Scripts/local/refresh.sh deliver

# 同步云端回执：查询 ingest/rebuild/deploy workflow 结论并校验线上文件哈希
bash Scripts/local/refresh.sh receipts

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

## 采集语义（Chess-Results）

- **逐页检查点**：每个 HTTP 成功页面先原子写入
  `raw/chess-results/tnr<id>/<kind>.html.gz`（附 `pages.json` 的 URL/SHA-256
  元数据），然后才解析。解析失败永远留有可离线复现的证据。
- **续跑**：中断或失败后再次运行同一目标，只补缺页；已通过哈希校验保存的
  页面通过缓存复用，重复请求 = 0。
- **单目标隔离**：一个赛事失败不会终止批次；失败被记录进 capture-state 后
  继续下一个目标，批处理以退出码 4 表示部分成功（`PARTIAL_FAILURE`）。
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

## 发布事务

每次运行创建独立目录：

```text
runs/<run-id>/
  run.json
  run.log
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
7. deliver 阶段按 direct → 系统代理 → GITHUB_PROXY/常见本地代理 逐路线实测
   Git smart HTTP（检查 HTTP 状态码，502/网关页不算可用），把不可变 commit
   SHA force-push 到单写者 `local-data`；Git 全部失败且 gh 已登录时自动落到
   GitHub Git Database API（同一 manifest、同一哈希文件，复用
   `run_manager.validate_manifest`，无第二套白名单）；
8. `ingest-local-data.yml` 按触发 push 的不可变 event SHA 摄入（不重读移动的
   分支头），用同一验证器只应用 manifest 文件清单，并在 job summary 写入
   run-id / source SHA / main commit 的 receipt；
9. Actions 离线重建索引和部署。

push 成功不等于发布成功。`refresh.sh receipts`（deliver 成功后也会自动尝试）
通过 gh API 读取 ingest/rebuild/deploy workflow 结论，并从线上站点回取一个
已发布文件校验 SHA-256，把 outbox 状态沿

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

- `DIRTY_RELEASE_PATH`：机器发布路径已有未提交修改，工具不会覆盖。
- `FIDE_DOWNLOAD_OR_VALIDATION_FAILED`：新文件无效；有效 last-good 不会被替换。
- `SOURCE_CIRCUIT_OPEN`：连续失败后熔断，等待后再试。
- `VISIT_BUDGET_EXHAUSTED`：兼容旧运行记录的状态码；当前采集不设置本机日访问额度。
- `PARTIAL_FAILURE`：批次部分目标失败并已隔离；成功赛事已保留。
- `PARSER_LAYOUT_CHANGED`：来源页面不再符合解析器预期；raw 证据已保留，更新
  解析器后 `--replay` 离线重放。
- `VALIDATION_REGRESSION`：人数、分片、勘误或解析行数出现异常。
- `COMPLIANCE_POLICY_BLOCKED`：操作违反数据边界（原始 HTML 入库、人工数据
  路径、或环境显式设为已退役的 link-only 模式）。
- `GIT_DNS_FAILURE` / `GIT_TLS_FAILURE` / `GIT_PROXY_FAILURE` /
  `GIT_CONNECT_FAILURE`：网络类投递失败，deliver 会自动轮换下一路线。
- `GIT_AUTH_FAILED` / `GIT_REMOTE_REJECTED`：换路线无用；重新登录 gh 或检查
  远端策略。
- `GIT_PUSH_FAILED`：发布包已留在 outbox，恢复网络后运行 `deliver`。

## 代码发布（与数据发布分离）

代码修改永不通过 `local-data` 投递。**默认直接在 `main` 上工作和提交**，
不为普通任务创建分支；仅当用户明确要求 PR/隔离时才建短命分支。普通 Git
不通时使用 API 发布器（以远端 main 为基准，先做 dry-run 三方合并冲突检查）：

```bash
python3 Scripts/local/publish_code_via_api.py --help
```

## 本地不 pull

采集机永不执行 pull/rebase。GitHub 推送自动尝试直连、macOS 系统代理和常见
本地代理端口，并对每条路线实测 Git smart HTTP；代理只传给 Git/GitHub API，
不传给数据来源请求。新机器初始化才允许 clone；已有工作副本永不为"同步"而
重新 clone/pull。
