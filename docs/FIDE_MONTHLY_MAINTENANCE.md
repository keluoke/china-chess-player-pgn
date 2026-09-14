# FIDE 官方等级分月度维护

FIDE 官方完整榜单是 registry 的数据来源；广播 PGN 中的 WhiteElo/BlackElo
只是赛事记录，可能取自相应棋种的榜单，也可被广播维护者覆盖。它们不是全体棋手
的当月权威数据，不能用于覆盖 registry。广播估算 rating diff 不等于官方结算。

## 定时与来源

GitHub Actions `update-fide-ratings.yml` 在每月 1 日 03:40 UTC（北京时间 11:40）
运行；2、3 日同一时间仅在当月尚未成功发布时补试。workflow_dispatch 用于立即
更新或核对同月官方修订。只访问官方月份下载页与 legacy combined XML ZIP；
legacy 包保留未评级棋手，同时包含 standard、rapid、blitz，避免普通 rated-only
名单误删未评级成员。

白名单由 `source_policy.require_fide_monthly_download` 强制执行，包括重定向。
专用云端能力不授权任何棋手详情或 Chess-Results 访问，不设置本地维护者标志。

## 完整性与身份

下载页必须明确是当前 UTC 月榜单，下载前后各核对一次；ZIP CRC、XML 打包日期新鲜度（允许月末提前两日打包）、
人数、重复 FIDE ID、姓名覆盖、三种等级分覆盖和数值范围均通过才能发布。
失败不退回旧缓存冒充本月。记录 ZIP SHA-256、字节数与 listDate，官方等级分历史
由既有 archive_rating_observations 在统一重建时按 listDate 归档。

复用现有 registry parser、人口回退门禁、姓名勘误与联邦覆盖表。
转会候选保留为复核信息，不能自动修改人工覆盖表。官方身份/等级分只进入 registry，
赛事和广播派生层继续受 registry 权威压制。

## 发布与恢复

原始 HTML/XML/ZIP 只在 runner 临时区。清洗输出进入独立 staging，生成包含每条
路径、基线 SHA-256、新 SHA-256 与删除项的精确 release artifact。发布前验证完整
artifact 和基线，main 移动即隔离，不 rebase、不覆盖并发提交。沿用共享
monthly_release 事务（Lichess 同样使用），随后将实际提交 SHA 传给既有
rebuild-indexes → R2 认证 → deploy，禁止另建派生入口。

采集失败由后续月初补试或手动运行恢复；发布阶段失败只重跑 publish job，读取已保存
artifact，不重新访问 FIDE。云端 rebuild/deploy 失败只重试对应阶段。main 已移动时
保留 artifact，先审核差异；禁止强制推送。最终成功以线上 snapshot/inputCommit 和
回执正文哈希核验为准。

部署额外核对 registry players/manifest 与 snapshot.outputs 中经过重建验证的
SHA-256 和字节数，并运行公开派生身份/等级分权威门禁。已退役且不发布的
index/players.json 不作为公共校验对象，当前 by-player/search/API 仍必须一致。
因此后续定时任务不能把新 registry 与旧快照一起上线。

## 来源说明

- [FIDE 官方榜单下载页](https://ratings.fide.com/download_lists.phtml)
- [Lichess 广播格式样例](https://database.lichess.org/#broadcasts)
- [Lichess 广播等级分与覆盖规则](https://lichess.org/broadcast/help)

## 实施验收

首次采集发布 [34806401455](https://github.com/keluoke/china-chess-player-pgn/actions/runs/34806401455)
已通过官方月份、ZIP CRC、姓名及等级分门禁，入库提交
`825e08a0ac33637e336c549ab5516baf687903b2`。榜单生效日期 `2026-09-01`，
12,013 名棋手；相对旧注册表，慢棋/快棋/超快棋分别 1,180 / 876 / 575 条等级分变化。
官方 ZIP 49,552,082 字节，SHA-256
`c167f4bda07e320fc63a368cbddde7563a893b08ceb1ca8a4c321694c59d6595`。

首次重建被已退役的旧索引等级分阻断，修复后仅重建，不重复下载来源。
一并清除搜索入口对历史青少年榜单的年份依赖，以及审计从旧索引覆盖当前等级分的回流。
修复回归测试共 432 项，2 项跳过；部署门禁运行
[34848052370](https://github.com/keluoke/china-chess-player-pgn/actions/runs/34848052370)
实际拒绝旧 snapshot 搭配新 registry（players 与 manifest 哈希均不一致）。

最终 [重建与 R2 认证 34848869413](https://github.com/keluoke/china-chess-player-pgn/actions/runs/34848869413)
及 [部署 34849638307](https://github.com/keluoke/china-chess-player-pgn/actions/runs/34849638307)
均成功。发布提交 `1630620534c0a153f8fc2b5fc55e6842c5d32120`；
快照 `20260914T132352Z-73723fe6`，输入提交
`e2c3a185e6bc01b6a606ad9f6634a453c6f91341`。7,692 个棋手包、178 个赛事包通过 R2 认证。

2026-09-14 普通生产 URL 核验：下列静态 JSON 均返回 JSON MIME，正文 SHA-256
与发布提交一致；棋手 API 由 Pages Function 重序列化，核对完整 JSON 内容一致。
注册表、搜索、API 中居文君（8603006）的慢棋等级分均为 2553（旧值 2560），
搜索/API 的 snapshotId 与新快照一致，registry.listDate 为 2026-09-01。

| 线上路径 | 正文 SHA-256 |
| --- | --- |
| `data/registry/manifest.json` | `400d1879e1d488c051d70e7edc3d7a96d1e937937d3a5c63ef8685c789045408` |
| `data/registry/players.json` | `678d6a740585316cff907c458d7a58df442e371809054c1e22ac197f399591cf` |
| `data/search-bootstrap.json` | `c6e82dfad3f3ad16334098bdd52727fea452365f19af1317e5f412f4f2efb2d3` |
| `api/v1/players/fide-8603006.json` | `990edaf30141d2bbc3ae0f1d2f26dc867017c9a2692f841d1801444b45541055` |
| `data/snapshot.json` | `40b03b3da15024b3e4ba10db63c75a19672efaa1992f21bce55cdbc096d75b4d` |
| `data/index/player-pgn-r2-receipt.json` | `bdc1e3a6930667bfee7d2714d6c27bbf77b6e2ff9d5cab963a411d8f0d8fc514` |
| `data/index/event-pgn-r2-receipt.json` | `02d821abcb1fe2977d39f221128ac1cc21cf0059932698b7d8171a258902cba2` |
