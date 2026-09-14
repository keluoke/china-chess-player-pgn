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

## 来源说明

- [FIDE 官方榜单下载页](https://ratings.fide.com/download_lists.phtml)
- [Lichess 广播格式样例](https://database.lichess.org/#broadcasts)
- [Lichess 广播等级分与覆盖规则](https://lichess.org/broadcast/help)

## 实施验收

待记录首次云端执行及线上实际更新结果；不能仅以新增 cron 宣称等级分已更新。
