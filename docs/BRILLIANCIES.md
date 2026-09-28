# 实战妙手

产品入口 `/brilliancies`；公开 API 见 [API 文档](API.md) 与 `/api/v1/brilliancies/openapi.json`。

## 数据与发布

人工审核记录在 `data/manual/brilliancies/curated.json`。每条绑定原始 PGN 的 SHA-256、规范化棋局指纹、半回合位置与实际招法。注册表决定棋手展示姓名。`!!` 是本站精选标记，不是其他平台评级，也不保证唯一最佳着法。

`Scripts/build_release_snapshot.py` 统一构建分片、列表、片段 PGN、API 和 SEO，再验证原局回放、SAN、完整 FEN、实际续着、分析合法性及署名。GitHub main → 统一重建 → R2 认证 → Pages 部署；前端与只读 Functions 使用同一快照，无新增数据库。单项分享使用查询参数；列表可收录，查询页不重复收录。

候选分析只读已归档 PGN，不抓取来源，不自动发布。可手动运行单次分析，也可由下述 Actions 队列持续处理：

```sh
python3 Scripts/local/analyze_brilliancies.py --input /absolute/path/to/archive.pgn --limit-games 100
```

默认结果写入用户私有运行目录 `~/Library/Application Support/ChinaChessPlayerPGN/brilliancies/candidates.json`。候选不是已审核内容；人工核对原局、引擎、文字和许可后才能进入 curated。当前精选集不代表全库已扫描，扫描覆盖记为未计量。

## 维护

撤回条目保留 ID 和原局引用，将状态改成 `withdrawn` 后走同一发布链：列表/SEO 移除、片段清理、API 返回 410。分析数值反映记录中的引擎与预算；真实实战续着和引擎变化分开显示。

验证：`python3 -m unittest Scripts.tests.test_brilliancies Scripts.tests.test_seo_pages`、`node Scripts/tests/brilliancies_api_test.mjs`、`node Scripts/tests/test_seo_middleware.mjs`；完整交付仍需统一构建和线上原局跳转验收。

## 历史补扫与新增自动分析

`Analyze archived brilliancies`（`.github/workflows/analyze-brilliancies.yml`）使用同一队列：

- 每 4 小时定时续跑；统一重建成功后再触发一次；维护者可手动启动。
- 两个固定分片，各最多分析 50 分钟或 5,000 局；并发运行互不覆盖。
- 从经过同快照 R2 回执认证的 `by-player/*/all.pgn` 读取，按规范化对局指纹与初始局面去重。不访问赛事来源，不复制棋谱到队列。
- 新发现棋局优先，历史未完成棋局持续补扫。记录“已扫描但无候选”；相同版本不重算已完成棋局。
- 引擎名称、分析器代码、python-chess 版本和搜索预算共同决定分析版本；升级后同一队列重新分析。当前是牺牲类筛选，不等于所有类型妙手的穷举。
- 每 25 局或满一分钟后保存一次检查点；中断最多重算尚未保存的批次，未完成的单局不能记为完成。坏局延迟重试，引擎进程失败直接报错，不伪装成零候选。
- 任务只读 Git，不提交结果、不触发重建，避免发布循环，也不阻塞正常入库部署。

R2 的两个检查点在 `private/brilliancies/queue-v1/shard-{0,1}.bin`。**生产桶有公开域名，因此检查点使用 AES-256-GCM 加密，而非依靠路径保密**。每次写入以 ETag 条件更新防并发覆盖，并 GET 回读比对正文。专用 GitHub secret 为 `BRILLIANCY_QUEUE_KEY`（Base64 编码的 32 字节密钥）；丢失密钥无法恢复检查点，禁止随意替换。对象只保留当前状态，不累积每次全量历史副本。

队列合计最多 500 MB，每分片 250 MB。写入前列举生产桶，要求“桶内其他对象 + 500 MB 预留”不超过 8 GB；每分片每次限制 10,000 次 A 类、5,000 次 B 类请求。达到预算直接停止，保留先前检查点。**这是当前桶的保守容量保护，不是全账号账单硬上限**；其他桶、并发写入和其他业务的当月请求仍须合并核对。

Actions summary 与保留 14 天的 `brilliancy-progress-0/1` artifact 提供两个分片各自的符合条件局数、已完成、待扫描、重试、候选和空间占用。两分片的局数和候选数相加；`archiveOccurrences` 是各 worker 读取的同一全库关联条目数，不可相加。候选正文不进入公开日志或 artifact，也不自动冒充人工精选。公开页面/API 仍只发布经审核记录；其“未计量”覆盖语义保留到正式接入可验证扫描统计为止。

维护者在配置现有 `R2_*` 环境变量及 `BRILLIANCY_QUEUE_KEY` 后，可只读导出当前版本候选到仓库外：

```sh
python3 Scripts/brilliancy_queue.py --shard 0 --export-candidates /tmp/brilliancies-0.json
python3 Scripts/brilliancy_queue.py --shard 1 --export-candidates /tmp/brilliancies-1.json
```

审核候选仍走人工维护记录 → 统一重建 → 部署。队列只持久保存机器分析结果，永不自动写 `data/manual`。
