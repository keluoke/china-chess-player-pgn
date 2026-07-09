## Imported Claude Cowork project instructions

## 数据正确性铁律(历史教训,不可再犯)

1. **身份错标教训**:8602980 曾被错标为"居文君"数月——它实际是**侯逸凡**(居文君是 8603006);8608288 曾被早期种子行写成"徐翔宇"——正确是**许翔宇**。根因:派生索引把上一次构建的输出读回来当作数据源,错误一旦进入就自我延续。**规则:注册表(registry)是姓名与等级分的唯一权威;任何派生层(index/by-player/leaderboards/api)禁止覆盖注册表的值。**
2. **勘误必须落进机制,不靠记忆**:所有已确认的身份/姓名勘误写入 `data/community/name-corrections.csv`(强制修正层,可覆盖已有值并清除错误别名),CI(`validate_community_data.py`)对提交产物做钉死断言——错误值再次出现即红灯。新勘误一律走这个文件,不要只改数据。
3. **转会棋手**:注册表按"现联邦 CHN ∪ 覆盖表转出棋手"收录;转出/转入必须录入 `data/community/federation-overrides.csv` 并带证据,禁止直接改注册表 JSON。每次 registry 抓取会输出 `data/generated/transfer-candidates.json` 供核对。
4. **机器数据与人工数据边界**:`data/generated/` 是爬虫产出(会被覆盖,禁止手改);人工修正只进 `data/manual/` 与 `data/community/`。爬虫采集的中文名必须过 `sanitize_person_name`(2-6 汉字,防赛事标题/尾逗号混入)。

## 管线架构要点

- 本地永不 pull:抓取 → 本地提交 → force-push `local-data` 分支 → 云端 ingest 镜像进 main → rebuild → 部署。
- chess-results / FIDE 抓取必须直连住宅 IP(封数据中心 IP);git 推送自动探测本地代理(Veee/Clash 等,读 scutil 系统代理)。
- CI 里绝不能回抓 chess-results(GitHub IP 被封),来源核验走本地 `refresh.sh verify`。
