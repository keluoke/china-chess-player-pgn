# Cloudflare 影子入库归档（2026-09-17）

维护者决定退役影子双写，生产继续使用 local-data → main → rebuild → deploy。
source.tar.gz 保存退役前已提交的 Worker、客户端、迁移工具、测试和契约；来源提交为 f773dd63a4b8435868bebb09bf728cf9d17032b8。归档不参与构建或运行。

历史 outbox、影子回执及远端影子 R2/D1 数据保留为只读历史证据；不允许恢复自动写入。生产 Pages、Functions、chess-data R2 不属于退役范围。
旧客户端保留拒绝执行的占位入口，防止采集工作区残留依赖意外恢复双写。

云端执行结果：影子 Worker 已删除，专用 Queue 的生产者和消费者均为 0；R2/D1 和脱离绑定的队列保留历史归档。可核对 retirement-receipt.json。生产资源未修改。线上旧 Worker 的实际部署源码及绑定信息另存维护者私有归档。
