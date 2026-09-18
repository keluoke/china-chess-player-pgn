# 对外数据指标口径

公开页面与 API 共用 `/data/public-metrics.json` 的同一快照指标。

| 字段 | 含义 |
|---|---|
| players | 注册表中的棋手人数，含覆盖的已转出棋手 |
| playersWithGames | 存在可用棋谱数据的棋手人数 |
| playableUniqueGames | 公共目录可下载、可复盘的独立棋局数；用于对外“可复盘棋谱”总量 |
| archivedUniqueGames | 全部归档事实中的独立棋局数，不能当作默认可复盘总量 |
| legalArchivedUniqueGames | 归档事实中具有合法棋谱主线的独立棋局数，不等于公共目录范围 |
| excludedUniqueGames | 质量门禁排除的独立棋局数 |
| games / playerGameLinks | 棋手与棋局的关联次数；同一局可能计入双方棋手，不能称为独立棋局数 |
| playablePlayerGameLinks | 可复盘棋谱的棋手关联次数 |

`metricVersion` 表示指标契约版本，`snapshotId` 标识整次发布，`generatedAt` 是指标生成时间。它们不代表官方等级分榜单生效月；榜单月份读取注册表 manifest 的 `listDate`。

名单/成绩完整、公开直播范围完整、全台棋谱完整分别计量。来源未公开棋谱不等于采集失败。具体说明见 [数据与更新方法](/methodology)。

Lichess Broadcast 派生数据继续保留 CC BY-SA 4.0 署名；其他资料依各自许可，不因聚合而获得统一授权。
