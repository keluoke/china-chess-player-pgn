# 中国国际象棋棋手数据库

社区共建的开源中国国际象棋棋手数据库:全量 CHN 棋手注册表(含转出/转入棋手标注)、赛事记录、对局 PGN、全年龄组排行榜(U8-U18 / U20 / 成年 / S50 / S65),并以[静态数据 API](docs/API.md) 对外提供数据。

- 贡献数据:见 [CONTRIBUTING.md](CONTRIBUTING.md)(网页上改 CSV 提 PR 即可；赛事中文名维护在 `data/community/tournament-name-mappings.csv`)
- **轻量贡献**:网页向导授权 GitHub 后直接开 Issue；社区提交目标线索和人工勘误，不运行或上传抓取结果（见 [docs/GOVERNANCE.md](docs/GOVERNANCE.md)）
- 数据 API:见 [docs/API.md](docs/API.md)(风格引擎等外部项目请走 API,本仓库不再内置风格模拟)
- 许可:代码 MIT；人工数据见 [LICENSE-DATA.md](LICENSE-DATA.md)，第三方来源按文件 manifest 分别标注（Lichess Broadcast 为 CC BY-SA 4.0）

数据源包括 FIDE、Lichess、Chess-Results 和人工审核资料。Chess-Results 赛事
数据由维护者本机全量抓取（名单、对阵、结果、排名、对局 PGN），本地清洗后
与已发布副本比对合并发布；原始 HTML 不入库（旧 link-only 政策已退役）。

**架构：私有采集、审批发布、离线构建。** 所有网络采集只在维护者本机运行，
见 [`Scripts/local/refresh.sh`](Scripts/local/README.md)。原始响应留在仓库外；本地只
push 带精确路径和 SHA-256 的发布 manifest，Actions 验证后离线重建并部署。

## 网页版

静态网页版在 `docs/` 目录:

```bash
python3 -m http.server 4173 -d docs
```

打开 `http://localhost:4173/`。推送到 GitHub 后，`deploy.yml` 会把 `docs/` 发布到 Cloudflare Pages(唯一线上出口:GitHub 管代码,Cloudflare 管网页)。

网页版首页展示 U8-U18 FIDE ELO 排行榜（李成智杯年龄组口径）和数据看板，支持中文/拼音/英文/FIDE ID 搜索。赛事、棋手和对局可以互相跳转：赛事页展示核验中文名、Chess-Results 信源、参赛中国棋手和已归档 PGN 覆盖；棋手页可回到赛事或打开对局。PGN 优先读取 `docs/data/pgn/by-player/` 聚合包，其次回退到赛事级 PGN 或 bulk 青少年包。

## 数据同步

分两类：**维护者本地采集/发布**和**GitHub 离线索引/部署**。

### 维护者本地（唯一网络采集入口）

运行 [`Scripts/local/refresh.sh`](Scripts/local/README.md) 或本地控制面板。仓库不再
提供抓取 workflow，也不接受社区抓取载荷。

| 本地命令 | 数据源 | 功能 |
|---------------------|--------|------|
| `health` | 全部 | 检查缓存、发布路径、TLS、配额和连通性 |
| `event-queue` / `candidates` | Chess-Results | 私有采集，不写人工层或公开数据 |
| `registry` | FIDE | staging + last-good + registry/勘误校验 + manifest |
| `bulk` | Lichess | 验证分片，保留 CC BY-SA 4.0 署名并按 manifest 发布 |
| `push` | GitHub | 重投最近发布包，不重新抓取 |

### 索引/部署类（GitHub Actions，自动）

| Workflow | 触发 | 功能 |
|----------|------|------|
| **Ingest local data branch** | local-data push | 校验 manifest 并只应用精确文件清单 |
| **Rebuild indexes and deploy** | ingest / 人工数据 push | 纯计算重建派生索引，提交后触发部署 |
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
docs/data/index/public-events.json
docs/data/index/players/fide-<fideID>.json
docs/data/index/by-player/manifest.json
docs/data/index/by-player/players.json
docs/data/index/by-player/fide-<fideID>.json
```

`by-player` 层聚合同一棋手全部可公开 PGN 和青少年阶段分段包，供网页版直接读取。

## 常用命令

```bash
# 健康检查与安全常规刷新
bash Scripts/local/refresh.sh health
bash Scripts/local/refresh.sh all

# 单独更新可发布来源
bash Scripts/local/refresh.sh registry
bash Scripts/local/refresh.sh bulk

# 私有采集目标赛事
bash Scripts/local/refresh.sh event-queue -- 1110333

# 重建 by-player 聚合层
python3 Scripts/build_static_player_pgn.py

```

## 百万级 bulk PGN

Lichess broadcast 数据压缩分片存放于 `docs/data/bulk/`，包含 77 个 `.pgn.zst` 分片、1,109,301 盘棋，并按年龄段生成全部 CHN 棋手对局 PGN 包(U8-U18 + 成年 19+)。

```bash
bash Scripts/local/refresh.sh bulk
```

## 中国棋手全量注册表

```bash
bash Scripts/local/refresh.sh registry
```

输出 `docs/data/registry/players.json`（按 CHN federation 过滤的 FIDE 全量棋手）。中文别名维护在 `data/manual/player-aliases.csv`。

## 国内临时身份注册表

无 FIDE ID 的棋手（李成智杯低龄组、棋协大师赛等）录入 `data/manual/domestic-player-sightings.csv`，关联证据记录在 `player-identity-links.csv`。处理后生成 `docs/data/registry/domestic/`。

```bash
python3 Scripts/sync_domestic_players.py
```

## 数据源

- 棋手身份与等级分权威：FIDE rating list
- 大批量授权棋谱：Lichess Broadcast（CC BY-SA 4.0）
- 赛事全量数据：Chess-Results（维护者本机抓取，本地清洗后比对合并发布；原始 HTML 不入库）
- 中文别名和勘误：人工审核的 `data/manual/`、`data/community/`

## 架构说明

详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。
