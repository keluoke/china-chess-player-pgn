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
2. Chess-Results 当前是 link-only：原始页面、解析排名/配对、PGN、姓名候选只写
   仓库外私有运行区，禁止进入 git、local-data、API 或公开对象存储。
3. FIDE/Lichess 机器发布必须经过 staging、验证和 release manifest；只能把 manifest
   精确列出的文件投递到单写者 `local-data`，由云端 ingest 到 main 后离线 rebuild。
4. 采集机永不 pull/rebase。GitHub 网络失败时只重投已生成的 release/outbox，禁止
   为了 push 失败重新抓取。GitHub 代理只用于 Git/GitHub API，来源请求必须直连
   住宅 IP（数据中心 IP 会被来源封禁）。
5. 代码和人工数据不得通过 local-data 发布。**默认只在 `main` 上工作和提交；
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
9. 修改管线后至少运行：`python3 -m unittest Scripts.tests.test_local_pipeline`、
   `python3 -m unittest Scripts.tests.test_chess_results_parser`、
   `python3 -m unittest Scripts.tests.test_docs_consistency`、
   `python3 -m compileall -q Scripts`、`bash -n Scripts/local/refresh.sh`、
   `git diff --check`；新增赛事格式必须配合成/脱敏 parser fixture。

## 管线架构要点

- 本地永不 pull:抓取 → 本地提交 → release 包写入仓库外 outbox → force-push
  `local-data` 分支 → 云端按触发 SHA ingest 镜像进 main → rebuild → 部署。
- 采集与投递解耦:GitHub push 失败只把发布包标记为 delivery-pending 留在 outbox，
  不阻塞后续采集；恢复后运行 `refresh.sh deliver` 重投。
- chess-results / FIDE 抓取必须直连住宅 IP(封数据中心 IP);git 推送自动探测本地
  代理(Veee/Clash 等,读 scutil 系统代理)并逐路线实测 Git smart HTTP。
- CI 里绝不能回抓 chess-results(GitHub IP 被封);Chess-Results 私有采集结果只在
  维护者本机核对,入口是 `refresh.sh event-queue`。
