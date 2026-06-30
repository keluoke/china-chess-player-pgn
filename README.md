# 中国棋手 PGN

原生 macOS SwiftUI 应用，用中文名、拼音或英文名查询中国国际象棋棋手，按唯一棋手 ID 聚合近十年赛事记录，并把用户选择赛事中该棋手的 PGN 合并保存为一个 `.pgn` 文件。

当前数据源以 Chess-Results 为主，同时已经加入本地 SQLite 棋手库、中文别名索引和 PGN 归档层。应用默认本地优先：本地有候选和赛事时不会联网；只有本地没有赛事，或用户打开“联网补齐本地结果”时，才访问 Chess-Results。

首页默认展示 U8/U10/U12/U14/U16/U18 六个青少年年龄组的 FIDE ELO 排行榜。年龄段按李成智杯自然年龄组口径：以比赛年度减出生年份，两年一组；例如 2026 年为 U8=2018-2019 出生、U10=2016-2017、U12=2014-2015、U14=2012-2013、U16=2010-2011、U18=2008-2009。榜单优先使用本地缓存里的棋手分数；首次启动时会写入一组中国青少年 FIDE 排名种子，保证离线也能看到基础榜单。若本地赛事记录里有李成智杯或全国青少年锦标赛前三名，会在对应年龄组榜单行上自动标注。

## 运行

```bash
swift run
```

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
