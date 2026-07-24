# Agent 操作契约（顶层权威文档）

任何涉及抓取、数据发布、GitHub 投递的任务，行动前必须先读
`Scripts/local/README.md`；本文件是可由 CI 检查的顶层契约摘要。

## 数据正确性铁律(历史教训,不可再犯)

1. **身份错标教训**:8602980 曾被错标为"居文君"数月——它实际是**侯逸凡**(居文君是 8603006);8608288 曾被早期种子行写成"徐翔宇"——正确是**许翔宇**。根因:派生索引把上一次构建的输出读回来当作数据源,错误一旦进入就自我延续。**规则:注册表(registry)是姓名与等级分的唯一权威;任何派生层(index/by-player/leaderboards/api)禁止覆盖注册表的值。**
2. **勘误必须落进机制,不靠记忆**:所有已确认的身份/姓名勘误写入 `data/community/name-corrections.csv`(强制修正层,可覆盖已有值并清除错误别名),CI(`validate_community_data.py`)对提交产物做钉死断言——错误值再次出现即红灯。新勘误一律走这个文件,不要只改数据。
3. **转会棋手**:注册表按"现联邦 CHN ∪ 覆盖表转出棋手"收录;转出/转入必须录入 `data/community/federation-overrides.csv` 并带证据,禁止直接改注册表 JSON。每次 registry 抓取会输出 `data/generated/transfer-candidates.json` 供核对。
4. **机器数据与人工数据边界**:`data/generated/` 是爬虫产出(会被覆盖,禁止手改);人工修正只进 `data/manual/` 与 `data/community/`。爬虫采集的中文名必须过 `sanitize_person_name`(2-6 汉字,防赛事标题/尾逗号混入)。

## 本地采集与 GitHub 投递铁律

1. 所有 Chess-Results/FIDE/Lichess 来源访问只允许维护者本机住宅网络执行；
   GitHub Actions、云主机、社区贡献工具不得回抓。唯一入口是
   `bash Scripts/local/refresh.sh <safe-command>`。
2. Chess-Results 采集以**赛事/对局数据完备性**为最高目标：维护者本机抓取赛事
   全量数据，本地完成清洗与校验后随发布管线上传，与云端比对合并后推送。
   旧的 link-only 政策已退役，文档提及时只能作"已退役"说明；本条与其他文档
   冲突时以本条为准。具体契约：
   - **可发布对象仅为清洗后的结构化数据**：赛事元数据、名单、最终排名、
     逐轮对阵/结果、完整对局 PGN。原始 HTML 永远只在维护者本机私有区。
   - **完整赛事门禁（多维）**：`Scripts/build_completeness_report.py` 是唯一
     发布门禁：`resultsStatus`（名单/排名/轮次/配对/结果）、`pgnAvailability`
     （not-published / advertised-partial / advertised-full，分母排除轮空）、
     `archiveStatus`（missing / incomplete / locally-recoverable /
     archived-advertised-complete / archived-full-board）分别计量。
     results-complete 即可进入公共投影并明确标注"来源未公开棋谱"等状态；
     只有 archived-full-board 才可称"全台棋谱完整"。旧 `complete_ids` 只是
     抓取检查点，不再充当发布门禁；`partial` 一律隔离。PGN 补件队列见
     `data/generated/pgn-supplement-queue.json`（P0=已承诺未归档，P1=可本地
     恢复/外部线索，P2=来源未发布不回抓）。
   - **公开直播范围完整（不等于全台完整）**：李成智杯、棋协大师赛等国内赛事
     常仅公开前若干台直播棋谱。若每轮来源实际公开的全部直播棋谱均已归档并与
     `round + board + 白黑 playerNo` 唯一匹配，可标
     `pgnIngestStatus=source-published-complete`，表示在客观公开范围内完整；
     仍不得标 `archived-full-board` 或宣称“全台棋谱完整”。来源未发布任何棋谱
     则标 `not-published`，不进入回抓队列；空响应、下载错误、少局或错配仍是
     `source-published-missing/partial`，不得用“通常只播前十台”豁免。
   - **Lichess 广播交叉归档**：亚少赛、世少赛须在 Chess-Results 对阵事实层上
     与 Lichess Broadcast 月度库交叉比对。只接受赛事系列、慢棋项目、年份/
     日期窗口、年龄/性别组、轮次及双方身份均相容的唯一匹配；FIDE ID 优先，
     规范化姓名只作回退，歧义局拒绝。投影写入
     `docs/data/bulk/lichess-events/`，保留 Lichess 名称、URL、CC BY-SA 4.0
     许可证及署名；广播容器中的未匹配残差必须写入 manifest，禁止静默算完整。
     `.pgn.zst` 月度原档只保存在本地/R2，不进入 Git 发布 manifest；合法的
     Zstandard skippable frame 不得误判为坏文件。
   - **合并发生在云端 ingest，不在本机**：采集机永不 pull，本机旧工作区不得
     假设等于云端 main。发布包携带赛事 ID、每类对象的自然键、基线版本/哈希；
     云端以当前 main 为基线做字段级三方合并（一致跳过 / 本地优先覆盖 / 保留
     更完整字段 / 身份冲突隔离）并产出合并回执（新增/更新/跳过/覆盖/隔离及
     旧新哈希）。自然键：排名=`playerNo`；配对=`round + board + 白黑 playerNo`；
     棋局=规范化 PGN 指纹。
   - **公共对象与前台去来源化**：Chess-Results 公共数据和界面不出现
     `source`、`sourceRefs`、外链或信源原名；Lichess 数据保留 CC BY-SA 4.0
     许可与署名义务不受此条影响。
   - **registry 压制**：赛事记录中的姓名、FIDE ID、等级分永不反向写入棋手
     主档；registry 始终压住这些字段（见铁律一）。
   - 设计基线详见 `docs/EVENT_DATA_COMPLETENESS_BASELINE.md`；现行代码与
     该基线的缺口按 P0 处理。
3. FIDE/Lichess/Chess-Results 机器发布必须经过 staging、验证和 release manifest；只能把 manifest
   精确列出的文件投递到单写者 `local-data`，由云端 ingest 到 main 后离线 rebuild。
4. 采集工作区永不 pull/rebase。GitHub 网络失败时只重投已生成的 release/outbox，
   禁止为了 push 失败重新抓取。macOS 系统代理只影响浏览器，终端 Git/GitHub API
   不得假设会继承；每条终端 GitHub 命令必须显式设置
   `HTTP_PROXY=http://127.0.0.1:15236` 与 `HTTPS_PROXY=http://127.0.0.1:15236`
   （同时设置小写变量），或调用会注入同等变量的受控脚本。GitHub 代理绝不传给
   Chess-Results/FIDE/Lichess 来源请求，来源请求必须直连住宅 IP。
5. 代码和人工数据不得通过 local-data 发布。代码修改必须在独立的轻量代码工作区
   （默认同级 `<repo>-code`）完成；采集工作区只负责采集、outbox 和 `local-data`。
   用 `Scripts/local/code_workspace.sh init|sync|push` 初始化、同步和推送代码工作区，
   其 repo-local `http.proxy` 也供侧边栏 Git 使用。**默认只在 `main` 上工作和提交；
   不要为普通任务创建新分支**——只有用户明确要求 PR/隔离分支时才建分支。
   普通 Git 不通时使用文档化的代码 API publisher
   （`Scripts/local/publish_code_via_api.py`，以远端 main 为基准，先 dry-run
   冲突检查）。
6. 禁止手改 data/generated；人工修正只进 data/manual 或 data/community。registry 是
   姓名和等级分唯一权威；姓名勘误写 name-corrections.csv，转会写 federation-overrides.csv。
7. 旧 refresh 命令已退役，不得调用（在文档中只能作为"已退役"提及）。来源健康检查
   使用 `refresh.sh health`；私有赛事采集使用 `refresh.sh event-queue -- <tnr-or-url>`；
   发布重投使用 `refresh.sh deliver`（`push` 是其兼容别名）。
8. push 成功不等于发布成功；必须确认云端 ingest、rebuild、deploy 的 receipt。任一
   云端阶段失败只重试该阶段，不回抓来源。
8a. 云端/本地派生重建唯一入口是 `Scripts/build_release_snapshot.py`：所有
   派生产物在同一 `SNAPSHOT_ID` 下重建并写入 `docs/data/snapshot.json`；任一
   构建或校验步骤失败即中止且不提交，旧快照继续服务。公共产物的去来源化由
   `validate_public_privacy.py` 扫描（禁 `source*`/`evidence`/`pgnURL` 字段与
   chess-results 链接；Lichess CC BY-SA 署名字段除外）。
9. 修改管线后至少运行：`python3 -m unittest Scripts.tests.test_local_pipeline`、
   `python3 -m unittest Scripts.tests.test_chess_results_parser`、
   `python3 -m unittest Scripts.tests.test_docs_consistency`、
   `python3 -m compileall -q Scripts`、`bash -n Scripts/local/refresh.sh`、
   `git diff --check`；新增赛事格式必须配合成/脱敏 parser fixture。

## 管线架构要点

- 采集工作区永不 pull:抓取 → 本地提交 → release 包写入仓库外 outbox → force-push
  `local-data` 分支 → 云端按触发 SHA ingest 镜像进 main → rebuild → 部署。
- 代码工作区独立从远端 `main` 做 blobless+sparse clone；允许 fetch/fast-forward，
  但不承载采集运行状态、机器产物或 outbox。
- 采集与投递解耦:GitHub push 失败只把发布包标记为 delivery-pending 留在 outbox，
  不阻塞后续采集；恢复后运行 `refresh.sh deliver` 重投。
- chess-results / FIDE 抓取必须直连住宅 IP(封数据中心 IP);终端 GitHub 访问显式
  注入 127.0.0.1:15236，`scutil` 只能用于发现候选代理，不能视为终端已继承代理。
- CI 里绝不能回抓 chess-results(GitHub IP 被封);Chess-Results 只在维护者本机
  抓取、清洗、核对,入口是 `refresh.sh event-queue`;通过完整性门禁的赛事数据
  随 local-data 发布,由云端 ingest 以 main 为基线做字段级三方合并并出回执。
