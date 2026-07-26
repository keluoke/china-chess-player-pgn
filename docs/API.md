# 静态数据 API v1

面向外部项目(风格引擎、统计分析、App 等)的只读数据接口。所有端点是构建期生成的静态文件,由 Cloudflare Pages 提供,自带 `Access-Control-Allow-Origin: *`,无运行时、无鉴权、无频率限制(请善用缓存)。

Base URL:

```
https://china-chess-player-pgn.pages.dev
```

## 稳定性承诺

`/api/v1/` 路径与已发布字段冻结:只加字段、不删改;破坏性变更将发布为 `/api/v2/`。`/data/pgn/by-player/` 的 PGN 路径规则同属 v1 契约。

## 端点

### `GET /api/v1/manifest.json`

元信息:`apiVersion`、`generatedAt`、totals(棋手总数/有中文名数/有对局数据数/总对局数)、`metricContract`、可用年龄组、端点列表、许可信息。**轮询此文件的 `generatedAt` 判断有无增量**。

指标统一采用“按棋手聚合、去重后的全部可用 PGN（含 bulk）”口径，详见 [`PUBLIC_METRICS.md`](PUBLIC_METRICS.md)。`/data/public-metrics.json` 是机器可读的唯一指标契约。

### `GET /api/v1/players.json`

全量注册表(约 1.2 万人,~2MB)。每行字段:`fideID`、`displayName`、`chineseName?`、`pinyin?`、`name`、`federation`、`formerFederation?`、`transfer?`(`{type: transferred_out|transferred_in, effective?}`)、`sex?`、`title?`、`birthYear?`、`standard?`、`rapid?`、`blitz?`、`inactive`。

转会口径:注册表收录 FIDE 现联邦 CHN 的全部棋手,加上社区覆盖表标注的已转出棋手(`transfer.type == "transferred_out"`,其 `federation` 为现联邦)。

### `GET /api/v1/search.json`

搜索索引:`rows: [[fideID, "别名1|别名2|..."], ...]`,别名含中文名、拼音、英文名及其变体。

### `GET /api/v1/leaderboards.json`

全年龄组排行榜。年龄组:`U8 U10 U12 U14 U16 U18`(李成智杯两年一组口径)、`U20`、`OPEN`(成年 19+)、`S50`、`S65`。`age = basisYear - birthYear`。v2 数据同时提供标准棋、快棋、超快棋和女子子榜；三种等级分独立排序，不互相回退。

### `GET /api/v1/players/fide-{fideID}.json`

单棋手详情(**仅对有对局数据的棋手生成**；数量以 manifest 的 `withGameData` 为准)。含注册表元数据 + `gameCount`/`eventCount` + `events[]`(赛事历史)+ `packages[]`(PGN 分段包)。

### PGN 下载

`packages[].pgnPath` 给出绝对路径,规则:

```
/data/pgn/by-player/fide-{fideID}/all.pgn      全部对局
/data/pgn/by-player/fide-{fideID}/U8.pgn ... U18.pgn   青少年分段
/data/pgn/by-player/fide-{fideID}/adult.pgn    成年期(19+)对局
```

每个包附 `sha256`,增量同步时对比即可跳过未变化的包。

## API v2（预览）

v2 按资源分片，所有响应带 `schemaVersion` / `snapshotId`（同一次发布的全部
产物引用同一 snapshotId，见 `/data/snapshot.json`）：

```text
GET /api/v2/manifest.json
GET /api/v2/rankings/official/current/{control}/{cohort}.json
```

`control` 为 `standard` / `rapid` / `blitz`；cohort 响应的 `rankings`
包含 `all` 与 `female`，并内嵌组内 `birthYears` 切分；为遵守部署文件数上限，
出生年份暂不拆成独立文件。

受托管平台单次部署文件数上限约束，v2 的棋手/赛事/搜索分片端点将在对象存储
迁移后上线；在此之前请继续使用 v1 兼容端点。官方榜与未来的参考估分榜永久分轨。

## 消费示例(风格引擎)

```bash
# 1. 有无更新?
curl -s $BASE/api/v1/manifest.json | jq -r .generatedAt

# 2. 拉某棋手全部对局
curl -s $BASE/api/v1/players/fide-8622388.json | jq -r '.packages[] | select(.id=="all").pgnPath'
curl -sO $BASE/data/pgn/by-player/fide-8622388/all.pgn
```

## 许可

API 不对所有字段作统一许可：社区原创审核数据为 CC BY 4.0，
Lichess Broadcast 派生数据为 CC BY-SA 4.0 并须保留署名，FIDE 为事实
注册表投影；其他历史静态资料不因本项目声明而自动获得 CC BY 许可。
详见仓库中的 `LICENSE-DATA.md` 和各数据 manifest。
