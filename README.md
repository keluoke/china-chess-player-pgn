# 中国棋手 PGN

原生 macOS SwiftUI 应用，用中文名、拼音或英文名查询中国国际象棋棋手，按唯一棋手 ID 聚合近十年赛事记录，并把用户选择赛事中该棋手的 PGN 合并保存为一个 `.pgn` 文件。

当前数据源以 Chess-Results 为主，同时已经加入本地 SQLite 棋手库、中文别名索引和 PGN 归档层。应用默认本地优先：本地有候选和赛事时不会联网；只有本地没有赛事，或用户打开“联网补齐本地结果”时，才访问 Chess-Results。

首页默认展示 U8/U10/U12/U14/U16/U18 六个青少年年龄组的 FIDE ELO 排行榜。年龄段按李成智杯自然年龄组口径：以比赛年度减出生年份，两年一组；例如 2026 年为 U8=2018-2019 出生、U10=2016-2017、U12=2014-2015、U14=2012-2013、U16=2010-2011、U18=2008-2009。榜单优先使用本地缓存里的棋手分数；首次启动时会写入一组中国青少年 FIDE 排名种子，保证离线也能看到基础榜单。若本地赛事记录里有李成智杯或全国青少年锦标赛前三名，会在对应年龄组榜单行上自动标注。

## 运行

```bash
swift run
```

## 网页版

静态网页版在 `docs/` 目录，可直接用于 GitHub Pages：

```bash
python3 -m http.server 4173 -d docs
```

本地打开 `http://localhost:4173/`。推送到 GitHub 后，仓库已包含 Pages workflow，会把 `docs/` 作为静态站点发布。网页版读取 `docs/data/youth-leaderboards.json`、`docs/data/registry/`、`docs/data/index/` 和 `docs/data/index/by-player/`，用于公开榜单、搜索和棋手看板；点进棋手时再加载单棋手明细 JSON。下载 PGN 时优先读取 `docs/data/pgn/by-player/fide-<FIDE_ID>/all.pgn`，没有按棋手聚合包时才回退到已归档赛事 PGN 或 bulk 青少年包。macOS 版继续负责本地 SQLite、联网补齐和把更多 PGN 同步进静态归档。

GitHub Pages 是静态托管，不运行服务器进程。网页版不能稳定地替用户实时抓取 Chess-Results PGN；未归档的 PGN 需要先通过 macOS 版或后续 GitHub Actions 数据同步写入 `docs/data/pgn/`。

网页版同时读取 `docs/data/bulk/` 的百万级压缩分片。当前 bulk 层镜像 Lichess official broadcast PGN archive：77 个 `.pgn.zst` 分片、1,109,301 盘棋，并从中生成 U8/U10/U12/U14/U16/U18 中国青少年对局 PGN 包。首页可按年龄段一键下载；`by-player` 派生层会把这些青少年包进一步整理成按棋手下载的 PGN。

网页版的棋手看板已接入模拟对局入口：每个棋手详情页会显示“与 XXX 模拟对局”，进入 `docs/mimic/` 后按 FIDE ID 动态加载对应画像。模拟画像由该棋手自己的开局库 + 浏览器 Stockfish 限强候选 + 风格/失误模型生成，静态 profile 位于：

```text
docs/mimic/profiles/fide-<FIDE_ID>/profile.js
docs/mimic/profiles/manifest.json
docs/data/mimic/profiles/manifest.json
```

批量刷新所有青少年棋手模拟 profile：

```bash
python3 Scripts/build_youth_mimic_profiles.py
```

脚本按 PGN SHA-256 跳过未变化棋手；GitHub Actions 的 `Update mimic profiles` workflow 会每周运行，`Update static PGN archive` 更新后也会触发一次 profile 刷新。

## PGN 静态归档

仓库内的 PGN 采用可被多个前端直接读取的静态结构：

```text
docs/data/pgn/<source>/tnr<tournamentID>/fide-<fideID>-<tournamentID>.pgn
docs/data/index/manifest.json
docs/data/index/players.json
docs/data/index/events.json
docs/data/index/players/fide-<fideID>.json
```

`players.json` 是轻量总表，适合首页和搜索；`players/fide-*.json` 是单棋手完整赛事、名次、PGN 路径和校验信息；`manifest.json` 记录总量、路径规则和来源。当前仓库已从本机缓存同步有效 PGN，脚本会排除 Chess-Results 返回的 HTML 错误页，只有能解析出 PGN header 的文件才进入索引。

统一按棋手聚合层由 `Scripts/build_static_player_pgn.py` 生成：

```text
docs/data/index/by-player/manifest.json
docs/data/index/by-player/players.json
docs/data/index/by-player/fide-<fideID>.json
docs/data/pgn/by-player/fide-<fideID>/all.pgn
docs/data/pgn/by-player/fide-<fideID>/U8.pgn
docs/data/pgn/by-player/fide-<fideID>/U10.pgn
...
```

该层只从已经入库的公开 PGN 派生，不联网抓取。macOS 版和网页版搜索到棋手后都优先读取这里的 `all.pgn`；U8-U18 单独包用于青少年阶段筛选。当前派生层覆盖 1,287 名棋手、22,110 盘去重对局、2,854 个 PGN 包。

从 macOS 本地缓存同步到仓库：

```bash
python3 Scripts/sync_static_pgn.py --from-local-cache
```

从已登记的静态索引继续抓取缺失 PGN：

```bash
python3 Scripts/sync_static_pgn.py --fetch-missing --max-downloads 50
```

只更新某个棋手：

```bash
python3 Scripts/sync_static_pgn.py --player 8657238 --fetch-missing --max-downloads 20
```

只从某个数据源更新：

```bash
python3 Scripts/sync_static_pgn.py --source chess-results --fetch-missing --max-downloads 50
```

按 FIDE ID 扫 Chess-Results 全局 PGN 搜索，并把可公开分发、质量合格的新棋局按赛事晋升到静态归档：

```bash
python3 Scripts/promote_public_pgn.py --scan-chess-results --player 8657238 --max-players 0
```

TWIC、Lichess、Chess.com 和国内赛事官网先进入本地侦察兵；只有 `data/manual/public-pgn-sources.csv` 标记为可公开分发的来源，才会被 `promote_public_pgn.py --promote-scout` 发布到 `docs/data/pgn/`。

GitHub Actions 里也有 `Update static PGN archive` workflow，可在 GitHub 页面手动运行；填 FIDE ID 时只更新该棋手，不填则按当前索引批量尝试；数据源可选 `all` 或 `chess-results`。workflow 成功后会自动提交 `docs/data/` 的变化。
另有 `Promote public PGN` workflow，用于按 FIDE ID 触发 Chess-Results 全局 PGN 搜索并提交新增静态 PGN。
这些 workflow 会在提交前自动运行 `Scripts/build_static_player_pgn.py`，确保 `by-player` 查询层同步更新。

## 百万级 bulk PGN

Lichess broadcast bulk 层使用压缩分片，避免把百万盘棋拆成百万个小文件：

```text
docs/data/bulk/manifest.json
docs/data/bulk/lichess-broadcast/shards/*.pgn.zst
docs/data/bulk/youth/manifest.json
docs/data/bulk/youth/pgn/U8/lichess-broadcast-youth.pgn
docs/data/bulk/youth/pgn/U10/lichess-broadcast-youth.pgn
...
```

更新 bulk 层：

```bash
python3 Scripts/sync_lichess_broadcast_bulk.py --metadata-only --mirror --index-youth
```

GitHub Actions 里有 `Update Lichess broadcast bulk archive` workflow，会按月刷新分片和青少年年龄段 PGN 包。Lichess broadcast 数据按 CC BY-SA 4.0 发布，授权说明保存在 `docs/data/bulk/NOTICE.md`。

macOS 版也会读取同一套 `docs/data/`：开发运行时从仓库 `docs/data` 发现数据；打包后的 `.app` 从 `Contents/Resources/data` 读取。首页会显示 bulk 总量、按棋手 PGN 总量和 U8-U18 分段包；棋手看板会优先使用 `by-player` 统一棋手 PGN，缺失时才回退到本地青少年 bulk 抽取。

## 中国棋手全量注册表

棋手身份库和 PGN 归档是两条流水线。身份库以 FIDE ID 为唯一主键，月度同步 FIDE legacy XML rating list，并按 `CHN` federation 过滤：

```bash
python3 Scripts/sync_chinese_players.py
```

脚本输出：

```text
docs/data/registry/manifest.json
docs/data/registry/players.json
docs/data/registry/shards/fide-prefix-<first3>.json
```

中文名、拼音和常见别名维护在 `data/manual/player-aliases.csv`，只作为 alias 挂到 FIDE ID，不能作为唯一身份。GitHub Actions 里有 `Update Chinese player registry` workflow，可手动或按月刷新全量 CHN 棋手注册表。

李成智杯、棋协大师赛一级组/候补组等无 FIDE ID 棋手不要硬塞进 FIDE 表。先录入：

```text
data/manual/domestic-player-sightings.csv
data/manual/player-identity-links.csv
```

再生成国内临时身份层：

```bash
python3 Scripts/sync_domestic_players.py
```

输出在 `docs/data/registry/domestic/`。未来确认 FIDE ID 后，只在 `player-identity-links.csv` 里加证据链接，历史低龄组成绩会归并到同一 canonical player。
GitHub Actions 里的 `Update domestic player registry` workflow 会在这些 CSV 更新后重生成静态 domestic registry。

完整工作机制见 [docs/DATA_WORKFLOW.md](docs/DATA_WORKFLOW.md)。

## 本地 PGN 侦察兵

原始 PGN、TWIC ZIP、Lichess `.pgn.zst`、ChessBase 导出和国内直播抓包文件，先进入本机侦察兵资产库：

```bash
python3 Scripts/pgn_scout.py init
python3 Scripts/pgn_scout.py seed-chess-results-targets
python3 Scripts/pgn_scout.py fetch-chess-results --max-requests 50
python3 Scripts/pgn_scout.py fetch-lichess --database-broadcasts --year 2025 --max-downloads 3 --extract
python3 Scripts/pgn_scout.py ingest ~/Downloads/Historical_CHN_Base.pgn --source chessbase-mega
```

默认目录是 `~/Library/Application Support/ChinaChessPlayerPGN/RawPGNScout/`，不会提交进仓库。操作手册见 [docs/PGN_SCOUT.md](docs/PGN_SCOUT.md)。

## 打包

```bash
Scripts/package_app.sh
```

打包结果在 `dist/中国棋手 PGN.app`。

打包脚本会把 `docs/data` 复制到 app 的 `Contents/Resources/data`，所以 mac 版离线启动后也能使用百万级 bulk manifest、青少年索引、分段 PGN 包和按棋手聚合的 `by-player` PGN。

## 本地数据库

运行后本地资料保存在：

```text
~/Library/Application Support/ChinaChessPlayerPGN/
```

包含：

- `china-chess-player-pgn.sqlite`：棋手、别名、赛事、参赛关系、PGN 索引、单盘棋索引。
- `PGNArchive/`：按来源和赛事统一保存的原始 PGN。

棋手唯一性：

- 有 FIDE ID 的棋手使用稳定 `player_id = fide-<FIDE_ID>`。
- 中文名、拼音、英文名、PGN 名只是 alias，统一映射到同一个 `player_id`。
- 同名棋手不会自动合并，UI 会列出候选资料供用户选择。

首次启动会写入默认种子索引：

- 中国常见强手中文名、拼音名、英文名、FIDE ID。
- 一批国内顶级赛事、中国甲级联赛、全国冠军赛、女子/男子世界顶级赛事的 Chess-Results 赛事索引。
- U8/U10/U12/U14/U16/U18 中国青少年 FIDE 排行榜种子，包含 FIDE ID、英文名、出生年和 standard/rapid/blitz 分。

SQLite 种子库只包含索引和身份映射；随仓库/随 app 分发的静态资产放在 `docs/data/` 或 app 的 `Contents/Resources/data/`，其中 `data/pgn/by-player/` 是 UI 优先读取的按棋手 PGN 层。用户通过 Chess-Results 下载或后续导入的 PGN 仍会进入 `PGNArchive/`，之后同一赛事会直接读本地缓存，并可通过同步脚本晋升到仓库静态层。

## 数据源

- 棋手和赛事：Chess-Results Player-Database。
- 对局 PGN：Chess-Results Game-Database 的 `Download as PGN-File`。
- 当前 FIDE 分和年龄组榜单种子：FIDE/Lichess FIDE 公开资料镜像。
- 中文查询：内置一批中国棋手中文名/拼音/FIDE ID 种子，联网和 PGN 导入后会继续扩充本地别名。

并非每个赛事都上传 PGN。下载时应用会逐项标记“本地缓存 / 已下载 / 无棋谱 / 失败”，只合并有内容的 PGN。

## GitHub 托管

项目已经包含 `.gitignore` 和 GitHub Actions CI。

如果本机安装并登录了 GitHub CLI：

```bash
Scripts/create_github_repo.sh china-chess-player-pgn private
```

如果不用 `gh`：

```bash
git init
git branch -M main
git add .
git commit -m "Initial macOS China chess PGN app"
git remote add origin git@github.com:<owner>/china-chess-player-pgn.git
git push -u origin main
```

架构说明见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。
