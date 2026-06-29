# 中国棋手 PGN

原生 macOS SwiftUI 应用，用中文名、拼音或英文名查询中国国际象棋棋手，按唯一棋手 ID 聚合近十年赛事记录，并把用户选择赛事中该棋手的 PGN 合并保存为一个 `.pgn` 文件。

当前数据源以 Chess-Results 为主，同时已经加入本地 SQLite 棋手库、中文别名索引和 PGN 归档层。应用会先查本地库和缓存，缺失时再联网补齐。

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

## 数据源

- 棋手和赛事：Chess-Results Player-Database。
- 对局 PGN：Chess-Results Game-Database 的 `Download as PGN-File`。
- 中文查询：内置一批中国棋手中文名/拼音/FIDE ID 种子，联网和 PGN 导入后会继续扩充本地别名。

并非每个赛事都上传 PGN。下载时应用会逐项标记“已下载 / 无棋谱 / 失败”，只合并有内容的 PGN。

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
