# 中国棋手 PGN

查询中国国际象棋棋手，聚合赛事记录，合并 PGN。

数据源以 Chess-Results 为主，辅以 FIDE/Lichess 公开资料和中文别名索引。项目由 GitHub Actions 自动爬取和更新。

## 网页版

静态网页版在 `docs/` 目录，可直接用于 GitHub Pages：

```bash
python3 -m http.server 4173 -d docs
```

打开 `http://localhost:4173/`。推送到 GitHub 后，Pages workflow 会把 `docs/` 作为静态站点发布。

网页版首页展示 U8-U18 FIDE ELO 排行榜（李成智杯年龄组口径），支持中文/拼音/英文/FIDE ID 搜索，以及按棋手浏览赛事和下载 PGN。PGN 优先读取 `docs/data/pgn/by-player/` 聚合包，其次回退到赛事级 PGN 或 bulk 青少年包。

## 数据同步（GitHub Actions）

所有数据由 GitHub Actions 自动维护。支持的 workflow：

| Workflow | 触发 | 功能 |
|----------|------|------|
| **Crawl player events** | 手动 + 每周一 03:30 UTC | 遍历 CHN FIDE ID，爬取 Chess-Results 赛事记录，生成棋手-赛事索引和中文名映射 |
| **Update static PGN archive** | 手动 | 抓取缺失 PGN 到 `docs/data/pgn/` 并更新索引 |
| **Promote public PGN** | 手动 | 按 FIDE ID 搜索可公开分发的新棋局并晋升到静态归档 |
| **Update Chinese player registry** | 手动 + 每月 | 从 FIDE legacy XML 同步 CHN 棋手全量注册表 |
| **Update domestic player registry** | 自动 | 从 CSV 生成国内临时身份层 |
| **Update name aliases** | 自动 | 从 `data/manual/player-aliases.csv` 更新别名索引 |
| **Update mimic profiles** | 手动 + 每周 | 刷新所有青少年棋手模拟 profile |
| **Update Lichess broadcast bulk** | 按月 | 镜像 Lichess broadcast PGN 并生成 U8-U18 包 |
| **Reconcile PGN sources** | 自动 | 验证 `docs/data/pgn/` 文件完整性 |

## PGN 静态归档

```text
docs/data/pgn/<source>/tnr<tournamentID>/fide-<fideID>-<tournamentID>.pgn
docs/data/pgn/by-player/fide-<fideID>/all.pgn
docs/data/pgn/by-player/fide-<fideID>/U8.pgn
...
docs/data/index/manifest.json
docs/data/index/players.json
docs/data/index/events.json
docs/data/index/players/fide-<fideID>.json
docs/data/index/by-player/manifest.json
docs/data/index/by-player/players.json
docs/data/index/by-player/fide-<fideID>.json
```

`by-player` 层聚合同一棋手全部可公开 PGN 和青少年阶段分段包，供网页版直接读取。

## 常用命令

```bash
# 从静态索引抓取缺失 PGN
python3 Scripts/sync_static_pgn.py --fetch-missing --max-downloads 50

# 按 FIDE ID 更新
python3 Scripts/sync_static_pgn.py --player 8657238 --fetch-missing

# 爬取全部 CHN 棋手赛事记录（增量、断点续爬）
python3 Scripts/crawl_player_events.py

# 爬取并立即下载对局 PGN
python3 Scripts/crawl_player_events.py --player 8603677 --fetch-games

# 刷新棋手注册表
python3 Scripts/sync_chinese_players.py

# 重建 by-player 聚合层
python3 Scripts/build_static_player_pgn.py

# 刷新青少年模拟 profile
python3 Scripts/build_youth_mimic_profiles.py
```

## 百万级 bulk PGN

Lichess broadcast 数据压缩分片存放于 `docs/data/bulk/`，包含 77 个 `.pgn.zst` 分片、1,109,301 盘棋，并按年龄段生成 U8-U18 中国青少年对局 PGN 包。

```bash
python3 Scripts/sync_lichess_broadcast_bulk.py --metadata-only --mirror --index-youth
```

## 中国棋手全量注册表

```bash
python3 Scripts/sync_chinese_players.py
```

输出 `docs/data/registry/players.json`（按 CHN federation 过滤的 FIDE 全量棋手）。中文别名维护在 `data/manual/player-aliases.csv`。

## 国内临时身份注册表

无 FIDE ID 的棋手（李成智杯低龄组、棋协大师赛等）录入 `data/manual/domestic-player-sightings.csv`，关联证据记录在 `player-identity-links.csv`。处理后生成 `docs/data/registry/domestic/`。

```bash
python3 Scripts/sync_domestic_players.py
```

## 模拟对局（Mimic Engine）

棋手看板可进入模拟对局，由开局库 + 浏览器 Stockfish 限强 + 风格模型生成。profile 位于：

```text
docs/mimic/profiles/fide-<FIDE_ID>/profile.js
```

## 数据源

- 棋手和赛事：Chess-Results Player-Database
- 对局 PGN：Chess-Results Game-Database `Download as PGN-File`
- FIDE 分：FIDE / Lichess FIDE 公开资料
- 中文别名：`data/manual/player-aliases.csv` + 爬虫自动采集

## 架构说明

详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。
