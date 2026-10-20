# 实战妙手：自动裁决与自动发布

历史与新增候选共用 `sacrifice-quality-gate-2`，只按局面棋艺裁决。FIDE ID、内部赛事 ID 和姓名只用于展示，不决定资格。人工精选仍由 `data/manual/brilliancies/curated.json` 维护；机器批准层独立写入 `data/generated/brilliancies-approved/`。

## 固定棋艺定义

- 同一候选正文、原局合法回放、FEN、实际着法和稳定 ID 必须一致。
- Stockfish 16 与 17.1 均完成 100 万和 500 万节点搜索；每个实际着法、最佳替代、接受弃子和可用的拒吃分支都须达到预算与深度 14，PV 必须合法。材料变化从 PV 重新计算，不信任试验 S/A。
- 两档搜索、两个引擎均须证明：接受弃子立即至少少 150cp 材料；补偿首轮不低于 -50cp、深度不低于 -25cp。
- S：持续弃子，最佳替代差距首轮至少 100cp、深度至少 140cp；首轮补偿不低于 0cp、深度不低于 80cp。
- A：持续弃子且差距至少 50/70cp，或强制战术获利且差距至少 100/140cp。持久少子与先弃后取均可通过。
- 缺证据保持 pending；冲突、非法或未达搜索预算隔离；近似等价替代、补偿不稳定等保持未证明。不会为了凑数量放宽条件。

`data/manual/brilliancies/auto-policy.json` 显式授权这一定义，并钉死裁决代码 SHA-256。代码改变而未同步校准/策略时停止发布。现有精选对照中，部分局面因有等价替代被保守留在人工精选；这不是全量召回保证，也不宣称人工主观误判率或与其他平台 `!!` 等价。

## 生产链

1. `analyze-brilliancies.yml` 每四小时及统一重建后处理新增归档；历史继续使用已完成的本机检查点。没有重新抓取来源或重跑完成记录。
2. 成功后触发 `publish-brilliancies.yml`，也可手动运行。先确认有成功 CI 对应同一 Scripts、工作流和策略正文，再用条件 ETag 分段回读两队列及四组质量分片，验证密文/正文、profile、候选哈希和合法棋艺证据。
3. `brilliancy_auto_publication.py collect` 只在暂存区生成批准分片、逐文件哈希 manifest 和精确 release.json；完整私有引擎证据、密钥、被拒候选不进入 artifact。生成前再核对全部远端 ETag，任一变化取消整批。
4. `publish` 复用 `monthly_release` 的基线哈希、不可变 main 与快进提交。并发 main 变化时拒绝覆盖，不 rebase。失败只重试保存的 artifact；已提交但未成功触发重建的重试仍会继续派发重建。公开内容无变化时不提交、不重复重建，不形成发布循环。
5. 显式把实际提交 SHA 交给唯一 `build_release_snapshot.py` 重建，再认证 R2 回执、部署。部署的产品 canary 同时验证妙手静态正文哈希、列表总数、各层样本详情、打包 PGN 与旧快照请求拒绝，均通过才算上线。

机器批准内容绑定原候选哈希、完整证据摘要和公开正文哈希。重建仍核验原局、registry 权威、许可证/署名和公共去来源化。没有 FIDE ID 的对局通过全库规范化 PGN 回放匹配，PGN 中的注释变例不会改变主线匹配。

## 撤回、容量与运行

- `data/manual/brilliancies/withdrawals.json` 可按稳定 ID 撤回机器条目；保留 tombstone，列表移除、详情/API 返回 410。人工精选的显示优先权保留，其撤回仍修改 curated。
- 已发布局面失去通过证据时转为 withdrawn；来源队列中整条候选消失则停止本批并诊断队列漂移，禁止静默丢失。
- 超过 255 条使用 256 个两位 ID 前缀分片，PGN 与详情打包。每片不超过 2 MiB；列表不重复整局着法，前台每页 40 条。API 用字典压缩筛选索引和每 256 条一块的列表，稀疏筛选仅解析本页 JSON 行；全部索引/块都进入统一快照哈希清单。部署的 19,000 文件及 24 MiB 单文件门禁继续生效。
- 历史双引擎深度复核已完成 59,318 条。正式证据裁决 335 S、6,132 A；971 条搜索不满足门槛隔离，其余未证明。2026-10-20 首次真实 R2 暂存还包括新增 780 个候选，通过 2 S、81 A，机器批准总数 6,550。它们只有完成生产链后才计为线上公开数量。

查看运行：GitHub Actions 的 **Adjudicate and publish brilliancies**、其后 **Rebuild indexes and deploy**、**Deploy static site**。进度日志只展示聚合；发布包保留 30 天。线上页面 `/brilliancies.html`，API `/api/v1/brilliancies`、`/<id>.json` 与 `/<id>.pgn` 读取同一快照。
