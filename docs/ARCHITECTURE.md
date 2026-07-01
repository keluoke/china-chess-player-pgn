# 架构说明

## 目标

应用不只是临时抓网页，而是逐步形成一个本地中国国际象棋棋手、赛事和 PGN 资料库。

核心原则：

- 用户可以用中文名、拼音、英文 PGN 名或 FIDE ID 查询同一名棋手。
- 棋手唯一身份使用本地 `player_id`，有 FIDE ID 时固定为 `fide-<FIDE_ID>`。
- PGN 下载后进入本地统一归档，下次查询优先使用本地缓存。
- 数据源可扩展，Chess-Results 只是第一批 provider。
- 默认查询本地优先，不在已有本地赛事时强制联网。

## 本地存储

运行后数据保存在：

```text
~/Library/Application Support/ChinaChessPlayerPGN/
```

主要内容：

```text
china-chess-player-pgn.sqlite
PGNArchive/
```

PGN 归档路径按来源和赛事分组：

```text
PGNArchive/chess-results/tnr935824/fide-8601429-935824.pgn
```

## SQLite 表

`players`

保存唯一棋手身份。`id` 是应用内部主键，`fide_id` 有值时唯一。

`player_aliases`

保存中文名、拼音、英文名、FIDE ID、PGN 名变体。查询先标准化 alias，再映射到 `player_id`。

`events`

保存赛事身份。第一版用 `source + source_event_id` 去重，例如 `Chess-Results + 935824`。

`player_events`

保存棋手与赛事的参赛关系，包括该来源里的报名名、名次、俱乐部/队伍、协会和 source player serial。

`pgn_archives`

保存本地 PGN 文件位置、SHA256、棋局数和下载时间。

`games`

保存解析后的单盘棋 PGN Header 和完整 PGN 文本，后续可以扩展到按对手、执色、结果、ECO、年份查询。

## 查询流程

1. 标准化用户输入。
2. 查询 `player_aliases`，中文和拼音都能命中同一个 `player_id`。
3. 如果本地命中且已有赛事，直接展示本地结果。
4. 如果用户打开“联网补齐本地结果”，使用 FIDE ID 到 Chess-Results 补齐近十年赛事。
5. 如果本地没有命中，按拼音姓名走 Chess-Results 棋手搜索。
6. 联网结果写回 `players`、`player_aliases`、`events` 和 `player_events`。
7. UI 展示更新后的本地候选棋手和赛事。

## 首页排行榜

首页使用 `LocalChessRepository.youthLeaderboards` 聚合本地 `players` 表中有出生年和 FIDE 分的棋手，按李成智杯自然年龄组口径归入 U8/U10/U12/U14/U16/U18，并按 standard 分排序；如果没有 standard 分，再回退到 rapid、blitz。

年龄组定义集中在 `YouthStage` 和 `YouthStageRules`：

- 以比赛年度减出生年份计算年龄组。
- U8=7-8 岁，U10=9-10 岁，U12=11-12 岁，U14=13-14 岁，U16=15-16 岁，U18=17-18 岁。
- 例如 2026 年口径：U8=2018-2019 出生，U10=2016-2017，U12=2014-2015，U14=2012-2013，U16=2010-2011，U18=2008-2009。
- 本版本首页只展示 U8-U18；如果某届赛事另设 U20，不进入青少年阶段看板。

每个榜单条目仍然是完整 `PlayerCandidate`，用户点击后进入同一套棋手看板、赛事列表和 PGN 下载流程。这样首页不是单独的展示数据，而是本地棋手库的入口。

李成智杯备注由本地赛事记录动态生成：赛事名包含“李成智”、`Li Chengzhi`，或 Chess-Results 英文标题里的 `National Youth Chess Championship`，且该年龄段名次为前三时，榜单行显示“李成智杯第 N”。

## 种子库

首版种子库在代码中维护：

- `ChinesePlayerSeeds.swift`：中文名、拼音、英文名、FIDE ID。
- `ChineseEventSeeds.swift`：默认赛事索引和棋手参赛关系。
- `YouthLeaderboardSeeds.swift`：U8/U10/U12/U14/U16/U18 首页 FIDE 排行榜种子。

这些种子保证首次启动后不依赖网络也能查到一批中国棋手、赛事和青少年榜单。它们不是完整 PGN 库；PGN 正文进入本地库的方式是下载、导入或后续 provider 同步。

## PGN 下载流程

1. 用户选择赛事。
2. 对每个赛事先查 `pgn_archives`。
3. 本地有缓存则直接读取。
4. 本地没有缓存才调用 Chess-Results Game-Database 下载 PGN。
5. 下载成功后写入 `PGNArchive/`，并解析入 `games`。
6. 用户选择的所有有内容 PGN 合并输出。

## 后续 provider

建议新增 provider 时只实现两类接口：

- 赛事/棋手索引导入：写入 `players`、`player_aliases`、`events`、`player_events`。
- PGN 导入：写入 `PGNArchive`、`pgn_archives`、`games`。

优先级：

1. 中国棋协大师赛和全国性比赛官方 PGN/HTML。
2. 李成智杯和国内青少年比赛。
3. 中国甲级联赛和全国锦标赛。
4. 世界/亚洲青少年比赛。
5. TWIC、FIDE、赛事官网、手工 PGN 导入。

## GitHub Pages 网页版

网页版是纯静态前端，文件位于 `docs/`：

```text
docs/index.html
docs/styles.css
docs/app.js
docs/data/youth-leaderboards.json
```

它不读取用户本机 SQLite，也不直接写 PGN 归档；这些仍由 macOS 版负责。网页端使用静态 JSON 展示 U8-U18 排行榜、中文/拼音/FIDE ID 搜索和棋手看板。GitHub Actions 的 `Pages` workflow 会直接发布 `docs/`，不需要 Node、Vite 或后端服务。
