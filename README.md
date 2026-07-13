# 中国国际象棋棋手数据库

社区共建的开源中国国际象棋棋手数据库:全量 CHN 棋手注册表(含转出/转入棋手标注)、赛事记录、对局 PGN、全年龄组排行榜(U8-U18 / U20 / 成年 / S50 / S65),并以[静态数据 API](docs/API.md) 对外提供数据。

- 贡献数据:见 [CONTRIBUTING.md](CONTRIBUTING.md)(网页上改 CSV 提 PR 即可；赛事中文名维护在 `data/community/tournament-name-mappings.csv`)
- **轻量贡献**:网页向导授权 GitHub 后直接开 Issue；完整赛事抓取使用[独立桌面工具](https://github.com/keluoke/china-chess-contributor/releases/latest)，无需下载主数据库。审核入库后才进入鸣谢名录(治理机制见 [docs/GOVERNANCE.md](docs/GOVERNANCE.md))
- 数据 API:见 [docs/API.md](docs/API.md)(风格引擎等外部项目请走 API,本仓库不再内置风格模拟)
- 许可:代码 MIT,数据 CC BY 4.0(见 [LICENSE-DATA.md](LICENSE-DATA.md))

数据源以 Chess-Results 为主，辅以 FIDE/Lichess 公开资料和中文别名索引。

**架构：抓取与索引/部署分离。** chess-results.com 和 ratings.fide.com 会封 GitHub Actions 的数据中心 IP，因此**抓取在本地（住宅 IP）跑**，见 [`Scripts/local/refresh.sh`](Scripts/local/README.md)；**索引重建和站点部署由 GitHub Actions 完成**。本地 push 原始数据后，`rebuild-indexes.yml` 会纯计算重建全部派生索引并触发 `deploy.yml` 发布。

## 网页版

静态网页版在 `docs/` 目录:

```bash
python3 -m http.server 4173 -d docs
```

打开 `http://localhost:4173/`。推送到 GitHub 后，`deploy.yml` 会把 `docs/` 发布到 Cloudflare Pages(唯一线上出口:GitHub 管代码,Cloudflare 管网页)。

网页版首页展示 U8-U18 FIDE ELO 排行榜（李成智杯年龄组口径）和数据看板，支持中文/拼音/英文/FIDE ID 搜索。赛事、棋手和对局可以互相跳转：赛事页展示核验中文名、Chess-Results 信源、参赛中国棋手和已归档 PGN 覆盖；棋手页可回到赛事或打开对局。PGN 优先读取 `docs/data/pgn/by-player/` 聚合包，其次回退到赛事级 PGN 或 bulk 青少年包。

## 数据同步

分两类：**抓取类**（需住宅 IP，本地脚本或自托管 runner 手动跑）和**索引/部署类**（纯计算，GitHub-hosted Actions 自动跑）。

### 抓取类（本地 / 自托管，仅手动）

在本地跑 [`Scripts/local/refresh.sh`](Scripts/local/README.md)（推荐），或在挂了自托管 runner 时从 GitHub UI 手动 dispatch 对应 workflow。它们只提交**原始**数据，随后由 Actions 重建索引并部署。

| workflow / 本地命令 | 数据源 | 功能 |
|---------------------|--------|------|
| `crawl` · Crawl player events | Chess-Results | 爬取赛事记录，生成棋手-赛事索引和中文名映射 |
| `pgn` · Update static PGN archive | Chess-Results | 抓取缺失 PGN 到 `docs/data/pgn/` |
| `promote` · Promote public PGN | Chess-Results | 晋升可公开分发的新棋局到静态归档 |
| `events` · Ingest event archive | Chess-Results | 起始排名名字 + 整赛事 PGN 分棋手 |
| `aliases` · Update name aliases | Chess-Results | 抓中文名并入注册表 |
| `reconcile` · Reconcile PGN sources | Chess-Results | 核对覆盖、探测缺口、补抓 |
| `registry` · Update Chinese player registry | FIDE | 从 legacy XML 同步 CHN 全量注册表 |
| `bulk` · Update Lichess broadcast bulk | Lichess | 镜像 broadcast PGN 并生成 U8-U18 包 |

### 索引/部署类（GitHub Actions，自动）

| Workflow | 触发 | 功能 |
|----------|------|------|
| **Rebuild indexes and deploy** | 原始数据 push / 抓取后 dispatch | 纯计算重建全部派生索引（`sync_static_pgn`、`build_static_player_pgn`、离线注册表/别名），提交后触发部署 |
| **Deploy static site** | `docs/**` 变化 / 被调用 | 把 `docs/` 发布到 Cloudflare Pages |
| **Update domestic player registry** | CSV 变化 | 纯计算从 CSV 生成国内临时身份层 |
| **CI** | push / PR | 字节编译脚本、校验 workflow 与 action YAML |

复用逻辑收在 `.github/actions/`（`setup-python-deps`、`rebuild-indexes`、`prepare-static-site`、`dispatch-workflow`）。

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

```

## 百万级 bulk PGN

Lichess broadcast 数据压缩分片存放于 `docs/data/bulk/`，包含 77 个 `.pgn.zst` 分片、1,109,301 盘棋，并按年龄段生成全部 CHN 棋手对局 PGN 包(U8-U18 + 成年 19+)。

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

## 数据源

- 棋手和赛事：Chess-Results Player-Database
- 对局 PGN：Chess-Results Game-Database `Download as PGN-File`
- FIDE 分：FIDE / Lichess FIDE 公开资料
- 中文别名：`data/manual/player-aliases.csv` + 爬虫自动采集

## 架构说明

详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。
