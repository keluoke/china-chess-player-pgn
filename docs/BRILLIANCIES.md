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

## 本地历史补扫与云端新增分析

切换日以前已登记的棋局作为固定历史集合；两个旧检查点由本地独占续跑，新增棋局由独立的云端检查点处理。两个集合按规范化棋局 ID 互斥，历史检查点和新增检查点使用不同 R2 key，分析结果仍可统一导出审核。切换前必须停止旧版 Actions、等待运行结束，再依次执行两个分片的 `--freeze-history` 与 `--lane incremental --initialize-incremental`。初始化操作保留旧检查点，重复执行只会核对相同基线；失败时不可启用新版云端任务。

`Analyze new archived brilliancies`（`.github/workflows/analyze-brilliancies.yml`）只处理切换后首次出现的棋局：

- 每 4 小时定时检查；统一重建成功后再触发一次；维护者可手动启动。没有新增棋局时直接结束。
- 两个固定分片，各最多分析 50 分钟或 5,000 局；并发运行互不覆盖。
- 从经过同快照 R2 回执认证的 `by-player/*/all.pgn` 读取，按规范化对局指纹与初始局面去重。不访问赛事来源，不复制棋谱到队列。
- 全库登记按棋谱包 SHA-256 缓存原文件偏移；后续只解析变化包，不变包直接复用目录，R2 不保存重复 PGN 正文。
- 新发现棋局进入云端增量分片，旧棋局只进入本地历史分片。记录“已扫描但无候选”；相同版本不重算已完成棋局。
- 引擎名称、分析器代码、python-chess 版本和搜索预算共同决定分析版本；升级后同一队列重新分析。当前是牺牲类筛选，不等于所有类型妙手的穷举。
- 每 25 局或满一分钟后保存一次检查点；中断最多重算尚未保存的批次，未完成的单局不能记为完成。坏局延迟重试，引擎进程失败直接报错，不伪装成零候选。
- 任务只读 Git，不提交结果、不触发重建，避免发布循环，也不阻塞正常入库部署。

新增分析成功后，同一工作流的 `quality` 阶段只从增量加密队列读取已完成候选，使用固定 SHA-256 的官方 Stockfish 16 与 17.1 Linux 二进制做双引擎 100 万/500 万节点复核。每次各分片最多新增 100 条质控证据并在时间预算内退出；先完成全体首轮，再按局面棋艺的优先级做深度复核，低优先级仍会续跑而非永久排除。首轮和深度分别写入 `quality-incremental-first-shard-{0,1}.bin`、`quality-incremental-deep-shard-{0,1}.bin`，与本地历史质控检查点隔离。每批以条件 ETag 更新加密 R2 对象并回读正文；失败只重试未同步的小批，不回抓原局。GitHub 摘要/artifact 只有聚合进度，没有候选正文或棋手身份；此阶段不提交 Git、不自动公开。

历史检查点仍在 `private/brilliancies/queue-v1/shard-{0,1}.bin`；云端新增检查点在同目录 `incremental-shard-{0,1}.bin`。**生产桶有公开域名，因此检查点使用 AES-256-GCM 加密，而非依靠路径保密**。每次写入以 ETag 条件更新防并发覆盖，并 GET 回读比对正文。专用 GitHub secret 为 `BRILLIANCY_QUEUE_KEY`（Base64 编码的 32 字节密钥）；丢失密钥无法恢复检查点，禁止随意替换。对象只保留当前状态，不累积每次全量历史副本。

队列合计最多 500 MB，每分片 250 MB。每轮开始及运行中至少每十分钟列举生产桶，要求“桶内其他对象 + 500 MB 预留”不超过 8 GB；两次全桶核对之间逐次计入本队列写入的大小。每分片每次限制 10,000 次 A 类、5,000 次 B 类请求。达到预算直接停止，保留先前检查点。**这是当前桶的保守容量保护，不是全账号账单硬上限**；其他桶、并发写入和其他业务的当月请求仍须合并核对。

Actions summary 与保留 14 天的 `brilliancy-progress-0/1` artifact 只统计云端新增；本机 `~/Library/Application Support/ChinaChessPlayerPGN/brilliancies/history-shard-{0,1}.json` 统计历史。全库进度须把两种分片的局数和候选数相加；`archiveOccurrences` 是各 worker 读取的同一全库关联条目数，不可相加。候选正文不进入公开日志或 artifact，也不自动冒充人工精选。公开页面/API 仍只发布经审核记录；其“未计量”覆盖语义保留到正式接入可验证扫描统计为止。

维护者在配置现有 `R2_*` 环境变量及 `BRILLIANCY_QUEUE_KEY` 后，可只读导出当前版本候选到仓库外：

```sh
python3 Scripts/brilliancy_queue.py --lane history --shard 0 --export-candidates /tmp/history-0.json
python3 Scripts/brilliancy_queue.py --lane history --shard 1 --export-candidates /tmp/history-1.json
python3 Scripts/brilliancy_queue.py --lane incremental --shard 0 --export-candidates /tmp/new-0.json
python3 Scripts/brilliancy_queue.py --lane incremental --shard 1 --export-candidates /tmp/new-1.json
```

审核候选仍走人工维护记录 → 统一重建 → 部署。队列只持久保存机器分析结果，永不自动写 `data/manual`。

本地历史 worker 在独立、只读的完整 Git 快照运行，先用 `validate_player_pgn_r2_receipt.py` 对 3,133 个棋手包及当前快照的 SHA-256/回执做验证；不从采集工作区的旧文件拼凑全库。Stockfish 16 的 UCI 名称和分析版本须与切换前一致，否则会按新版本重新分析。`Scripts/local/run_brilliancy_history.py` 同时运行两个分片，按局在本机私有目录原子保存加密检查点，并在每轮结束时向生产 R2 同步。R2 上传暂时中断时继续从本机密文续跑；恢复上传时必须核对远端基线 ETag、回读正文，冲突即停止，不覆盖其他写入。两个分片全部 `pendingGames=0` 且 R2 检查点同步成功才退出。`Scripts/local/install_brilliancy_history_agent.py` 将其安装为当前用户的 launchd 任务；电脑关机或休眠期间不能运行，恢复后继续。每个分片最近一轮日志与摘要留在上述私有目录；生产候选仍需人工审核才能进入网站。

## 自动质控试跑（尚不公开）

`Scripts/local/run_brilliancy_quality.py` 只读已完成的加密历史分片，在本机用 Stockfish 16 与 17.1 分别复核局面、着法、对手接受/拒吃变化及补偿。断点 SQLite 文件为 mode 0600，证据正文逐条经 AES-GCM 加密。缺 FIDE ID 或内部赛事 ID 不影响质控。`S/A/B/C/D` 当前仅是待校准的试验等级，`classification.symbol=!!` 仍不可直接当成已审核结果；程序没有公开发布能力。断点按候选正文、规则代码、引擎二进制和节点预算核对，匹配则续跑，变化则重算。

先按棋艺特征对两个分片各取 300 个候选，确认通过率和误判后再定全库规则：

```bash
python3 Scripts/local/run_brilliancy_quality.py --shard 0 --sample-size 300 --nodes 1000000
python3 Scripts/local/run_brilliancy_quality.py --shard 1 --sample-size 300 --nodes 1000000
```

`--sample-size 0` 才代表全量。结果文件位于私有运行目录，不能加入 Git 或发布 artifact；上线仍需完成质量校准、发布投影和公开验证门禁。

全量时可加 `--backup-every 1000`：每千条把当前本机断点同步到 R2 的独立加密质量对象，使用条件 ETag 写入、正文回读和配额门禁；恢复时先比对两端记录，冲突停止而不覆盖。试跑不写 R2 质量对象。

抽样规则确定后，用 `python3 Scripts/local/install_brilliancy_quality_agent.py` 安装本机 launchd 全量任务；它分批运行两个分片，结果可从私有目录的 `quality-full.json` 和 `quality-agent.out.log` 查看。任务只形成内部质控证据，不会因 S/A 达到数量目标而停扫，也不会自动绕过公开发布门禁。

抽样进行中或完成后可用 `python3 Scripts/local/report_brilliancy_quality.py` 查看仅含聚合数量的进度与分层投影。投影标为试验规则结果，不等于已审核可上线数量；调整分层阈值时先复用已保存的引擎证据，不能为凑比例重跑全库。

深度校准仍取同一 600 个分层样本，使用独立的 `--lane deep` 断点和 R2 加密对象；首轮全库继续运行时可并行验证。它把每次搜索预算提高到 500 万节点，仍只产生证据，不能直接把试验等级发布为 `!!`：

```bash
python3 Scripts/local/run_brilliancy_quality.py --lane deep --shard 0 --sample-size 300 --nodes 5000000 --backup-every 50
python3 Scripts/local/run_brilliancy_quality.py --lane deep --shard 1 --sample-size 300 --nodes 5000000 --backup-every 50
python3 Scripts/local/report_brilliancy_quality.py --lane deep --nodes 5000000
```

长时间校准可运行 `python3 Scripts/local/install_brilliancy_quality_deep_agent.py`。两个用户级 launchd 任务各处理 300 个样本，退出后不会循环重算；重启机器后同一断点可续跑。深度断点、日志与 R2 对象均与首轮全库分开。

首轮 `quality-full.json` 确认两个分片全部完成且 R2 已同步后，在固定提交的干净、detached 本机代码工作树运行 `python3 Scripts/local/install_brilliancy_quality_deep_full_agent.py`。深度全量任务复用现有 600 个样本和 7 个对照局面的加密断点，按首轮棋艺证据优先复核可能的妙手，再继续复核其余局面；没有按棋手身份、赛事 ID 或先验数量丢弃候选。两个分片继续使用条件 ETag、远端冲突隔离和正文回读；`quality-deep-full.json` 的 `graded == candidatePositions` 且两个 R2 分片同步后才算深度历史质控结束。安装器拒绝首轮未完成或可变代码工作树，避免重扫与版本漂移。

`python3 Scripts/local/report_brilliancy_quality.py --lane deep --nodes 5000000 --gate-preview` 会把同一候选的 100 万、500 万节点加密证据配对，按双方独立引擎的最佳替代着法差距、接受/拒吃后的安全性、持续少子或强制战术获利给出仅供校准的 S/A/未证明分层。规则只读取棋局位置及引擎证据，不读取 FIDE ID、内部赛事 ID 或棋手姓名；试验等级和分层投影均不构成公开许可。对照样本揭示，只认持续少子会漏掉先弃后取的真实战术；当前机器发布门禁因此仍关闭，正式放行还需要复核规则、生成可审计的发布包并完成生产验收。

公开原局验证按棋局指纹查已归档 PGN；双方没有 FIDE ID 时仍核对原局哈希、完整走法和指定局面，若同指纹对应多个不同原档则隔离。缺失棋手 ID 和内部赛事 ID 时对应站内链接留空，不伪造身份或赛事。
