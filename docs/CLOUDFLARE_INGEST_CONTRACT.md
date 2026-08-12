# Cloudflare 免费层机器数据 Ingest 契约

状态：第一阶段影子发布（shadow），不得作为生产数据主入口。

已部署影子入口：`https://chess-data-ingest-shadow.seanyan099.workers.dev`；专用资源
名称分别为 Worker/D1/Queue `chess-data-ingest-shadow` 与 R2
`chess-ingest-shadow`。这些名称只用于影子服务，禁止绑定生产数据域名。

## 目标与边界

机器采集结果从本机不可变 outbox 经鉴权 Worker 流式写入专用 R2 桶，由 Queue
异步执行路径级 baseline/current/candidate 三方判断，D1 只保存事务、对象头、
快照指针和回执。代码、schema、`data/manual/`、`data/community/` 始终留在 Git。
原始 HTML/WARC 永不离开维护者本机。

第一阶段只做影子双写和对账，不修改生产 Pages/R2 数据读取。连续发布验证通过前，
GitHub `local-data → main → rebuild → deploy` 仍是生产发布链路。

面板中的 Cloudflare 自动双写必须是独立、显式授权且默认关闭的开关；不得因
GitHub 生产自动推进已开启而推定影子上传也获授权。开关开启后，只对新生成的
合格 outbox 自动双写；历史包只能由维护者显式执行 `shadow-deliver` 回填。

## 免费层硬门禁

以下是服务自身的绝对上限，不是告警阈值；达到任一门禁立即 fail-closed，不排队、
不上传、不切换快照，也不得自动升级到付费计划：

| 项目 | Cloudflare 免费额度 | 本服务硬上限 |
|---|---:|---:|
| Worker 请求 | 100,000/日 | 5,000/日预留 |
| 普通 HTTP Worker CPU | 10 ms/请求 | 只做鉴权、D1 小查询、R2 流式写；禁止整库构建 |
| D1 rows read | 5,000,000/日 | 100,000/日预留 |
| D1 rows written | 100,000/日 | 10,000/日预留 |
| D1 单库/账户存储 | 500 MB/5 GB | 本服务 128 MiB；只存元数据 |
| D1 单次 Worker 查询 | 50 次 | 合并最坏 43 次；保留 7 次余量 |
| Queue operations | 10,000/日 | 1,000/日预留 |
| R2 Class A | 1,000,000/月 | 100,000/月预留 |
| R2 Class B | 10,000,000/月 | 250,000/月预留 |
| R2 存储 | 10 GB-month/月 | 专用影子桶最多预留 1 GiB |

单日最多 8 个发布包；单包最多 12 个文件、64 MiB；单文件最多 16 MiB。12 文件
上限来自 D1 Free 每次 Worker invocation 最多 50 次查询：当前最坏合并为 43 次，
固定保留 7 次余量。不得在不拆分 Queue 状态机的前提下提高此值。预算按
最坏情况在发布登记时一次性预留，即使对象已存在也不返还预算。这样会保守地提前
停机，但不会因去重估算乐观而突破门禁。免费额度或用量无法确认时按不可用处理。
本机客户端必须在任何网络请求前执行相同门禁。超限包写入
`shadow-delivery.json`，状态为 `ineligible` 并保留明确的 `FREE_TIER_*` 错误码；
GitHub 生产 outbox 不受影响。禁止为了塞进免费层自动拆分一个原子发布包：这会
改变三方合并和快照原子性，也不能规避 D1 Free 单次 50 查询上限。

Worker Free 到达平台账户级请求上限时由 Cloudflare 拒绝后续请求；本服务不配置
Paid Workers。Cloudflare 账户内新增其他 R2/Worker/D1/Queue 消费者前，必须重新
分配本表预算，否则视为违反契约。

## 鉴权与重放防护

- 使用独立随机 Worker Secret `INGEST_HMAC_SECRET`；本机副本只存 macOS Keychain
  service `china-chess-cloudflare-ingest-shadow`，不得复用 R2/GitHub 密钥；仓库、
  manifest 和日志不得出现密钥。
- 每次请求签名：`HMAC-SHA256(method + path + timestamp + nonce + bodySha256)`。
- 时间偏差最多 300 秒；nonce 只能使用一次。
- nonce 保留 10 分钟后惰性清理；覆盖完整签名有效期且不允许 D1 表无限增长。
- 上传正文由 R2 的 SHA-256 checksum 校验；路径和哈希必须已在签名 manifest 中声明。
- 只有专用 Worker 的 R2 binding 能写影子桶；客户端不取得新的桶级凭据。

## 不可变对象、三方判断与原子晋升

- 正文键：`ingest/blobs/sha256/<前两位>/<sha256>`。
- snapshot/receipt 均按 ID 写不可变对象，不覆盖全局共享大回执。
- `current == candidate`：幂等；`current == base`：应用；`candidate == base`：跳过；
  其余情况 `RELEASE_BASE_CONFLICT`，整包隔离。
- 首次影子观察到既有路径而 D1 尚无 head 时，可从签名 manifest 的 `baseSha256`
  bootstrap；回执必须报告 bootstrap 数量。此行为仅用于建立影子基线。
- R2 对象先写，D1 `batch()` 最后原子写入所有 path head、snapshot、receipt 指针和
  `current_snapshot`。D1 失败时旧快照继续服务；未引用 R2 对象可稍后清理。
- registry 仍是姓名和等级分唯一权威。第二阶段字段级自然键合并上线前，保持当前
  路径级 fail-closed 语义。
- Queue consumer 固定 `max_concurrency=1`，避免两个发布包并发读取同一 current；
  不得为追求吞吐量开启自动并发。

## 回执与切流门禁

状态链为 `registered → queued → processing → complete/conflict/failed`。只有 D1 已
原子切换影子快照、R2 manifest/receipt 可回读且哈希一致，才算影子完成。

生产切流必须另行满足：

1. 至少连续 7 天且不少于 20 个符合本契约上限的真实发布包双写；`ineligible`
   包只计入容量证据，不计入成功对账包数；
2. Git 生产结果与 Cloudflare 影子结果逐路径 SHA-256 一致；
3. 幂等重投、乱序发布、真实冲突、Queue 重试、配额耗尽均通过演练；
4. 生产回滚、旧 URL 兼容、MIME/CORS/cache 和原始 URL 验证完成；
5. 顶层契约再次评审并显式将服务从 shadow 改为 production。

任何一项未满足，禁止绕过 Git 生产链路。

## 免费层依据

额度和单次调用边界以 Cloudflare 官方文档为准；每次生产切流评审必须重新核对：

- Workers：<https://developers.cloudflare.com/workers/platform/pricing/>
- D1 价格：<https://developers.cloudflare.com/d1/platform/pricing/>
- D1 限制：<https://developers.cloudflare.com/d1/platform/limits/>
- Queues：<https://developers.cloudflare.com/queues/platform/pricing/>
- R2：<https://developers.cloudflare.com/r2/pricing/>

官方额度发生降低时立即采用更低值并停发复核，不得依赖本文旧数字继续运行。
