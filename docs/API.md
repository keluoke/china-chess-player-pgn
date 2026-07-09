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

元信息:`apiVersion`、`generatedAt`、totals(棋手总数/有中文名数/有对局数据数/总对局数)、可用年龄组、端点列表、许可信息。**轮询此文件的 `generatedAt` 判断有无增量**。

### `GET /api/v1/players.json`

全量注册表(约 1.2 万人,~2MB)。每行字段:`fideID`、`displayName`、`chineseName?`、`pinyin?`、`name`、`federation`、`formerFederation?`、`transfer?`(`{type: transferred_out|transferred_in, effective?, evidence?}`)、`sex?`、`title?`、`birthYear?`、`standard?`、`rapid?`、`blitz?`、`inactive`。

转会口径:注册表收录 FIDE 现联邦 CHN 的全部棋手,加上社区覆盖表标注的已转出棋手(`transfer.type == "transferred_out"`,其 `federation` 为现联邦)。

### `GET /api/v1/search.json`

搜索索引:`rows: [[fideID, "别名1|别名2|..."], ...]`,别名含中文名、拼音、英文名及其变体。

### `GET /api/v1/leaderboards.json`

全年龄组排行榜。年龄组:`U8 U10 U12 U14 U16 U18`(李成智杯两年一组口径)、`U20`、`OPEN`(成年 19+)、`S50`、`S65`。`age = basisYear - birthYear`。

### `GET /api/v1/players/fide-{fideID}.json`

单棋手详情(**仅对有对局数据的棋手生成**,约 1600 人;是否存在可查 manifest 或直接 404 兜底)。含注册表元数据 + `gameCount`/`eventCount` + `events[]`(赛事历史)+ `packages[]`(PGN 分段包)。

### PGN 下载

`packages[].pgnPath` 给出绝对路径,规则:

```
/data/pgn/by-player/fide-{fideID}/all.pgn      全部对局
/data/pgn/by-player/fide-{fideID}/U8.pgn ... U18.pgn   青少年分段
/data/pgn/by-player/fide-{fideID}/adult.pgn    成年期(19+)对局
```

每个包附 `sha256`,增量同步时对比即可跳过未变化的包。

## 消费示例(风格引擎)

```bash
# 1. 有无更新?
curl -s $BASE/api/v1/manifest.json | jq -r .generatedAt

# 2. 拉某棋手全部对局
curl -s $BASE/api/v1/players/fide-8622388.json | jq -r '.packages[] | select(.id=="all").pgnPath'
curl -sO $BASE/data/pgn/by-player/fide-8622388/all.pgn
```

## 许可

数据以 CC BY 4.0 提供(署名:china-chess-player-pgn contributors)。PGN 棋谱源自 Chess-Results / Lichess broadcast 公开页面,再分发请保留来源信息,详见仓库 LICENSE-DATA.md。
