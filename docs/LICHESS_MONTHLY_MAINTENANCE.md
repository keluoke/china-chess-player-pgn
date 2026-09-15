# Lichess 广播月度维护

2026-09-11 起，开放广播库与住宅网络采集分开维护。权威入口是
`.github/workflows/update-lichess-broadcasts.yml`，每月 5 日 03:17 UTC
（北京时间 11:17）定时运行，亦支持 workflow_dispatch。GitHub 定时任务可能排队延迟。

[Lichess 官方广播目录](https://database.lichess.org/#broadcasts)明确广播数据采用
CC BY-SA 4.0；不能把网站其他数据集的 CC0 当成广播许可。每份投影保留名称、
来源 URL、许可 URL 与署名。来源范围完整不等于全台完整，未完局/匹配残差不豁免。

## 数据事务

1. 固定 main 输入 SHA，读取上次已发布广播 manifest，获取官方月度目录。
2. 检查上月已公开、历史月份不丢失、月份连续；自动处理所有缺失月份，不限最近一片。
3. 旧月片从 R2 读取，逐字节验证已发布 SHA-256；缺失对象从 Lichess 下载。
   每片完整解压校验（包括 skippable frame 和末帧截断），实际 PGN 局数必须等于目录。
4. runner 临时目录 staging 重建中国棋手年龄段与严格赛事匹配投影，读取当前 main
   registry 和对阵事实；不抓取 FIDE/Chess-Results，不反写身份主档。
5. 仅向生产 `chess-data` 桶创建不存在的月片对象，条件写入禁止覆盖旧对象；
   新对象 GET 回读哈希，旧对象本次读取已验证正文。无需开通新资源或付费计划。
6. 生成逐片 `update-receipt.json` 和精确 `release.json`；artifact 只包含清洗投影、
   校验回执及 manifest，不含原始 HTML 或 `.pgn.zst`。artifact 保留 30 天。
7. 独立 publish job 在任何落盘前验证所有路径、SHA-256、字节数和基线哈希，
   精确提交 main，禁止 rebase；与本机 local-data 写者分离，以 main 快进检测并发。
8. 显式传递实际发布 SHA 给现有 rebuild；统一构建事实表、索引/API、认证棋手 R2
   对象再 deploy。上线验收核对生产 manifest/receipt 正文与目标 Git 提交。

云端能力开关仅允许 Lichess，不能设置通用本地维护者开关绕过其他来源限制。
Secrets 复用现有 R2_ENDPOINT / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_BUCKET，
不输出凭据。本地 GitHub 代理不得进入 runner 来源访问。

## 失败恢复

- collect 失败：保留旧线上清单，不发布部分数据；下次运行自动补齐历史缺口。
- publish/dispatch 失败：使用 GitHub “Re-run failed jobs”重用 artifact，不重新访问来源。
  已提交且候选文件完全相同的重试直接恢复实际 main SHA，继续下游。
- main 输入在构建中改变：拒绝覆盖并保留 artifact；核对冲突后从当前 main 重建，
  旧 R2 月片复用，不需人工下载。禁止重复强推。
- rebuild/deploy 失败：只重跑相应工作流；不能为了部署失败重抓广播。
- 目录上月尚未发布、局数/哈希不一致：明确失败，禁止用空结果报成功。

本地 bulk / bulk-full 保留为显式补救能力，bulk-reindex 保留为离线重匹配能力。
日常更新不需要维护者开机、手动下载或维护月份列表。当前实现每次验证整库并重建
投影，优先保证 registry/人工勘误变动能覆盖所有历史月；后续可按不可变月片 + 身份
规则版本缓存中间结果，但必须证明与全量冷构建等价后才替换。

## 已有原片离线重放与赛事查谱

`workflow_dispatch` 的 `replay_only=true` 只从生产 R2 回读已登记的月片，
验证既有 SHA-256、完整压缩帧和局数后重做赛事关联；不访问广播目录或来源下载。
R2 缺片或哈希冲突即停止，不能回退回抓。发布仍使用同一不可变 manifest、
main 快进、统一 rebuild 和 R2 认证流程。每月 5 日的自动维护继续检查并补齐新月片。

专项关联覆盖亚少赛、世少赛、李成智杯和棋协大师赛。国内赛事额外核对站点、
日期窗口、组别及已结束赛果；明确 FIDE ID 冲突和白黑方颠倒不能退回姓名匹配。
未匹配广播残差继续保留，不能把“已收录棋谱”误标成“全台完整”。

赛事目录以人工确认的届次/站点关系及系列别名词典组织组别，保留旧 TNR 地址。
目录的可查看局数按唯一指纹及非空合法着法计算；赛事父级不能累加棋手关联次数。
已归档的有效棋谱不依赖 registry/FIDE ID 或赛果页存在；不完整结构化赛事仍隔离。
父页棋谱保留组别，排名和逐轮对阵只展示所选原组。派生赛事包不得反向进入棋局事实层。

赛事派生 PGN 在 runner 临时目录构建，认证到内容寻址 R2 后不进入 Git。
rebuild 认证时校验本地正文与远端回读；deploy 的 `--validate --receipt-only`
核对相同快照、完整对象集合、哈希、配额及轮换回读凭证，无需再次生成临时正文。
