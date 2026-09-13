# 赛事档案组别与人数展示修复

案例：`?player=domestic-eb644c5e741b` → `?event=1227492`。

## 定位与修复

- 组别解析按字典顺序匹配，先命中“棋协大师组”，吞掉了“男子/女子候补棋协大师组”的特定含义。改为最长标签优先；男女候补组均有回归用例。现有目录 52 个同类档案受影响，统一重建修正，不手改生成数据。
- 删除“中国棋手（名单标 CHN）”、可跳转/已收录棋手统计及整个已收录棋手区块。旧 CHN 数量按名单 federation 字段统计，并非直接按 FIDE ID 计数；但原始名单该字段常空，部分身份补全后才有 CHN，不能代表本组中国参赛者总数。
- 参赛人数直接取本赛事完整 players，成绩人数仅取 standings；不再把名单人数冒充最终成绩人数。成绩区标题改为“本组共 131 人”，区分人数与个人名次。轮次数从本赛事 detail 获取。

## 数据核验（2026-09-12）

普通线上 URL 与当前 main 一致：赛事 1227492 为男子候补棋协大师组，131 人、9 轮；胡安蓬 playerNo=107，最终第 21 名、6 分。第 6 名是马子沐，第 131 名是周翰文。未复现胡安蓬“第 6 名”或“第 131 名”；这两个数不能据此改写真实名次。

全库离线审计覆盖 1,024 个已发布赛事：

- 文件编号与 payload tournamentID 不一致：0。
- 681,588 个白黑方 playerNo 引用均在各自赛事名单内。
- 全部 1,024 份公开赛事的逐轮 round/board/白黑编号/result 及最终 playerNo/rank/score，与对应清洗事实一致。
- 27,524 条具有赛事编号、选手编号与名次、且能唯一对应赛事成绩行的国内棋手记录，名次差异为 0。

该检查证明当前事实→公开投影→棋手记录间的一致性，不声称重新验证了所有来源网页。未重新抓取 Chess-Results/FIDE，浏览器自动化超时；本案例通过生产 JSON 正文与代码路径核验。

## 验证

目录与前端契约 42 项测试通过；管线、parser、文档 209 项通过；compileall、refresh.sh 语法、JavaScript 语法、git diff --check 通过。上线验收于 2026-09-13 完成。


## 上线回执

- 修复提交：`51424529c2a42a18abcefe91a8d68c3f2a7a3a7d`。
- [CI 34673000593](https://github.com/keluoke/china-chess-player-pgn/actions/runs/34673000593)、[重建 34673000607](https://github.com/keluoke/china-chess-player-pgn/actions/runs/34673000607)、[部署 34673827061](https://github.com/keluoke/china-chess-player-pgn/actions/runs/34673827061) 全部 success。
- 派生提交 `a049f2ee0918833ac383bac60dc89054f6781718`；随后 contribution-funnel 更新提交 `6042e7e3a5419597377c87a7c81ec2fc13296667` 也已部署成功，未改变本次修复文件。
- 普通生产域名 chessdb.aigclabs.cc 上 app.js、public-events.json、tnr1227492.json、国内棋手 16.json 分片及 snapshot.json 正文均与当前已部署 main 一致；JSON MIME 正确。
- 线上 snapshot：`20260912T042708Z-27a9e4f8`；inputCommit 为上述修复提交。52 个受影响组别全部修正，1227492 明确为“男子候补棋协大师组”。
- 本案例的 9 轮记录为 3 胜 6 和，合计 6 分；最终第 21 名、成绩表共 131 人。未擅改名次。
- collector 的受管目录与解析代码已通过 collector-runtime-sync 精确安装。
