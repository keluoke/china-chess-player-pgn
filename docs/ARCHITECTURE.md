# 架构说明

## 目标

逐步形成中国国际象棋棋手、赛事和 PGN 的静态资料库，通过 GitHub Pages 提供网页查询。

核心原则：

- 用户可以用中文名、拼音、英文 PGN 名或 FIDE ID 查询同一名棋手。
- 棋手唯一身份使用 `player_id`，有 FIDE ID 时固定为 `fide-<FIDE_ID>`；无 FIDE ID 的国内赛事棋手使用 `domestic-<hash>` 临时身份。
- 数据源可扩展，Chess-Results 只是第一批 provider。
- 抓取只在本地 / 自托管住宅 IP 运行；GitHub Actions 只做离线索引重建与部署，`docs/` 为纯静态发布。

## 静态数据存储

```text
docs/data/
├── registry/            # FIDE CHN 全量棋手注册表
├── index/               # 赛事目录、棋手索引、按棋手 PGN 索引
├── pgn/                 # 按来源/赛事/棋手拆分的 PGN
└── bulk/                # 百万级 Lichess broadcast 压缩分片
```

全量由 Python 脚本生成，GitHub Actions 自动运行。

## 全量棋手注册表

`docs/data/registry/players.json` 由 `Scripts/sync_chinese_players.py` 从 FIDE rating list legacy XML 生成，按 federation=`CHN` 过滤。

中文名、拼音和别名来自 `data/manual/player-aliases.csv`。这些字段只作为查询 alias，不参与唯一性判断。

## 国内临时身份注册表

无 FIDE ID 棋手进入独立的 domestic registry：

- `data/manual/domestic-player-sightings.csv`：不可变证据
- `data/manual/player-identity-links.csv`：人工审核链接
- `docs/data/registry/domestic/players.json`：输出

## 赛事索引

由 `Scripts/crawl_player_events.py` 遍历 registry 中所有 CHN FIDE ID，通过 Chess-Results SpielerSuche 爬取赛事记录。输出：

- `data/manual/chess-results-player-events.csv`：棋手-赛事关系表
- `data/manual/chess-results-player-name-map.csv`：中文名映射证据
- `docs/data/index/chess-results-tournaments.json`：赛事目录
- `docs/data/index/chess-results-spielersuche-manifest.json`：爬取清单

`Scripts/build_event_catalog.py` 在离线构建时把 Chess-Results 赛事目录、已归档
PGN 覆盖和 `data/community/tournament-name-mappings.csv` 合并为
`docs/data/index/events.json`。其中 `name` 始终保留信源原文，`chineseName` 只来自
社区核验映射；两者不可相互覆盖。每项赛事都带稳定 `source:tournamentID`、中国棋手
FIDE ID 列表和 PGN 覆盖计数，供网站完成赛事 → 棋手 → 对局的链接。

增量爬取，断点续爬，支持 `--refresh-days` 参数。

## PGN 静态归档

PGN 按来源、赛事和棋手拆成小文件：

```text
docs/data/pgn/chess-results/tnr1210266/fide-8657238-1210266.pgn
docs/data/pgn/by-player/fide-8657238/all.pgn
docs/data/pgn/by-player/fide-8657238/U12.pgn
```

好处：
- Git diff 可读
- GitHub Pages 直接按 URL 提供
- 各端复用同一套路径规则

## 静态网页版

网页版是纯静态前端，位于 `docs/`：

- `index.html` + `app.js` + CSS
- 首屏加载 `youth-leaderboards.json` + `registry/players.json`
- 搜索加载 `index/players.json`
- 棋手看板加载 `index/players/fide-*.json`
- 赛事看板按需加载 `index/events.json`，通过 URL 参数 `?event=chess-results:<tnrID>` 直达
- PGN 优先读取 `pgn/by-player/` 聚合包

## 同步脚本

### `sync_static_pgn.py`

统一同步入口。在 GitHub Actions 上读取静态索引，按 FIDE ID 和赛事 ID 抓取缺失 PGN，校验后写入 `docs/data/pgn/`，生成 manifest/索引。

### `build_static_player_pgn.py`

派生入口。从已晋升的赛事 PGN + bulk 青少年数据按 FIDE ID 去重，生成 `by-player` 聚合层。

### `crawl_player_events.py`

核心爬虫。遍历 CHN FIDE ID，从 Chess-Results SpielerSuche 爬取每名棋手的完整赛事历史，持久化到 CSV + JSON。可选 `--fetch-games` 联动 `fetch_event_pgn.py` 下载对局。

### `fetch_event_pgn.py`

批量赛事 PGN 下载。按 tnrid 从 Chess-Results 下载完整赛事 PGN，按 FIDE ID / 别名匹配分配给中国棋手。

## 数据流水线

```
FIDE legacy XML  →  sync_chinese_players.py  →  registry/players.json
Chess-Results    →  crawl_player_events.py   →  player-events.csv + tournament catalog
                 →  fetch_event_pgn.py        →  docs/data/pgn/<source>/tnr<id>/*.pgn
                                              →  build_static_player_pgn.py → by-player/
                                              →  build_event_catalog.py → events.json
```
