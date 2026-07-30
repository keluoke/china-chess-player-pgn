# 架构说明

## 目标

逐步形成中国国际象棋棋手、赛事和 PGN 的静态资料库，通过 GitHub Pages 提供网页查询。

核心原则：

- 用户可以用中文名、拼音、英文 PGN 名或 FIDE ID 查询同一名棋手。
- 棋手唯一身份使用 `player_id`，有 FIDE ID 时固定为 `fide-<FIDE_ID>`；无 FIDE ID 的国内赛事棋手使用 `domestic-<hash>` 临时身份。
- 数据源可扩展，Chess-Results 只是第一批 provider。
- 抓取只在登记的维护者本机住宅 IP 运行；社区和 GitHub Actions 不抓取，`docs/` 为经过来源策略过滤的静态发布。

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

## 赛事目标与私有采集

公开仓库只保存社区/人工登记的目标 URL、tnr、中文映射和历史上已经审核发布的
静态投影。`Scripts/local/refresh.sh event-queue` 读取目标队列，把 Chess-Results
HTML 和结构化解析结果写到仓库外 `runs/<run-id>/raw|extracted`。

Chess-Results 发布策略是 `full-data`（旧 link-only 已退役）：赛事名单、逐轮
对阵、结果、排名与对局 PGN 在本地清洗校验后，与已发布副本比对合并（一致
跳过，冲突以本地清洗数据为准），经 release manifest 进入公开层；原始 HTML
和机器姓名候选仍不进入公开索引。`build_event_catalog.py` 只离线组合已经获准保留的仓库输入，且
registry 姓名字段不得被赛事索引覆盖。

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
- 首屏加载 `search-bootstrap.json`（国内实体分片后台补载）
- 棋手看板按需加载 `index/by-player/fide-*.json`
- 赛事看板按需加载 `index/public-events.json`；四类重点赛事保留独立分类，已发布详情与事件级棋谱归档也必须有中性目录记录。公开 URL 使用 `?event=<赛事编号>`，旧格式仅作兼容并自动改写
- PGN 优先读取 `pgn/by-player/` 聚合包

仓库仍生成 `index/manifest.json`、`index/players.json` 与
`index/players/fide-*.json` 供离线兼容；线上 Pages 包为遵守平台 20,000 文件上限
不包含这组已被 `search-bootstrap.json` + `index/by-player/` 取代的旧索引。
装配超过 16,000 文件会告警，超过 19,000 文件直接拒绝发布，为对象存储迁移
保留硬余量。

## 同步脚本

### `sync_static_pgn.py`

离线静态索引维护入口。Chess-Results 网络下载功能受授权策略硬门控；GitHub
Actions 只读取既有文件并重建 manifest/索引。

### `build_static_player_pgn.py`

派生入口。从已晋升的赛事 PGN + bulk 青少年数据按 FIDE ID 去重，生成 `by-player` 聚合层。

### `crawl_player_events.py` / `fetch_event_pgn.py`

保留为历史兼容/授权后工具，默认由 `COMPLIANCE_POLICY_BLOCKED` 阻止公开写入；
不在面板、社区流程或 GitHub workflow 中执行。

## 数据流水线

```
社区目标线索 → 人工目标队列 → 维护者本地 Chess-Results 私有运行区（默认不发布）
FIDE XML → staging → registry/勘误校验 → release manifest → local-data
Lichess Broadcast → staging/文件签名校验 → BY-SA manifest → local-data
local-data manifest → CI 精确 ingest → 离线派生索引 → 部署
```

## 社区贡献流水线

社区只贡献目标和人工知识（治理机制见 `GOVERNANCE.md`）：

```
网页 Issue / target-only manifest → CI 拒绝抓取附件 → 人工审核和排队
  → 维护者本地私有采集 → 合规/隐私/质量过滤 → 获准来源才形成发布包
```

> 历史废案说明：早期版本曾允许贡献者上传解析结果与 HTML/PGN 证据
> （旧 contrib 流程）。该流程已被治理规则废止：社区只提交目标线索与
> 人工勘误，抓取载荷一律由 CI 拒绝，任何采集只在维护者本机执行。
