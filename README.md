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

本地打开 `http://localhost:4173/`。推送到 GitHub 后，仓库已包含 Pages workflow，会把 `docs/` 作为静态站点发布。网页版读取 `docs/data/youth-leaderboards.json` 和 `docs/data/index/`，用于公开榜单、搜索和棋手看板；点进棋手时再加载单棋手明细 JSON。用户可以勾选 `docs/data/pgn/` 中已经归档的静态 PGN，并在浏览器里合并下载。macOS 版继续负责本地 SQLite、联网补齐和把更多 PGN 同步进静态归档。

GitHub Pages 是静态托管，不运行服务器进程。网页版不能稳定地替用户实时抓取 Chess-Results PGN；未归档的 PGN 需要先通过 macOS 版或后续 GitHub Actions 数据同步写入 `docs/data/pgn/`。

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

GitHub Actions 里也有 `Update static PGN archive` workflow，可在 GitHub 页面手动运行；填 FIDE ID 时只更新该棋手，不填则按当前索引批量尝试；数据源可选 `all` 或 `chess-results`。workflow 成功后会自动提交 `docs/data/` 的变化。

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

完整工作机制见 [docs/DATA_WORKFLOW.md](docs/DATA_WORKFLOW.md)。

## 打包

```bash
Scripts/package_app.sh
```

打包结果在 `dist/中国棋手 PGN.app`。

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

种子库只包含索引和身份映射，不内置大批 PGN 正文。PGN 正文会在用户下载或后续导入后进入 `PGNArchive/`，之后同一赛事会直接读本地缓存。

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
