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
| D1 rows written | 100,000/日 | 30,000/日预留 |
| D1 单库/账户存储 | 500 MB/5 GB | 本服务 128 MiB；只存元数据 |
| Worker Free 单次调用子请求 | 50 次 | 每个登记/合并分片最多 10 文件，最坏不超过 30 次 |
| Queue operations | 10,000/日 | 1,000/日预留 |
| R2 Class A | 1,000,000/月 | 100,000/月预留 |
| R2 Class B | 10,000,000/月 | 250,000/月预留 |
| R2 存储 | 10 GB-month/月 | 专用影子桶最多预留 1 GiB |

单日最多 8 个逻辑发布包；单包最多 **384 个文件、64 MiB**；单文件最多 16 MiB。
客户端把一个逻辑 manifest 分成每片最多 10 文件的登记请求，Queue consumer 也按
每次最多 10 文件计算三方决策。分片只属于内部可恢复状态机，不产生子快照，也不
改变原始 run-id、manifest 哈希、三方合并边界或回执边界。发布头必须预先提交按
序排列的分片 SHA-256；Worker 对每个已签名分片重新规范化并计算哈希，任一不匹配
均拒绝，不能只靠文件数/字节数凑齐登记门禁。

384 是免费层内的联合硬上限：最坏 39 个登记片；40 个 Queue 消息（39 个合并片加
1 个 finalize），按写入/读取/删除各计一次为每包 120 Queue operations，8 包为
960，低于本服务 1,000/日预留。D1 写行按 `6 × files + 4 × registrationChunks + 300`
保守预留，384 文件为每包 2,760、8 包为 22,080，低于本服务 30,000/日；余量覆盖
重试与计量偏差。Worker 请求最坏预留也必须小于 5,000/日。每个合并 invocation
最多执行 10 次 R2 HEAD、10 次 D1
head 查询及少量固定查询/批写，低于 Worker Free 单次 50 子请求限制。

预算按最坏情况在发布头登记时一次性预留，即使对象已存在也不返还预算。免费额度
或用量无法确认时按不可用处理。本机客户端必须在任何网络请求前执行同一逻辑包
门禁。真正超过 384 文件、64 MiB 或 16 MiB 单文件的包写入
`shadow-delivery.json` 为 `ineligible`；只有上游本来就语义独立的发布才可分别形成
独立 manifest，禁止客户端把一个原子发布包拆成多个可见快照来规避配额。

D1 Worker binding 不允许用 `PRAGMA page_count/page_size` 查询实际库大小，因此不得
把该查询当运行时门禁。服务改用只增不减的 `quota_storage` 账本：迁移时先为既有库
预留 4 MiB，之后每包按 `4096 × files + 1024 × registrationChunks + 64 KiB`
预留元数据空间；累计达到 128 MiB 即拒绝新包。只有完成实际清理并通过迁移审计后
才能调低账本，不得在请求路径中乐观返还。该估算刻意高于实际行尺寸，以提前停机。

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
- R2 对象先写；分片合并只把决策暂存在 `release_files`，不得更新 `path_heads`。
  所有分片完成且无冲突后，finalize 用一个 D1 `batch()` 内的
  `INSERT ... SELECT ... ON CONFLICT` 更新全部 path head，并同时写 snapshot、receipt
  指针和 `current_snapshot`。D1 失败时旧快照继续服务；未引用 R2 对象可稍后清理。
- registry 仍是姓名和等级分唯一权威。第二阶段字段级自然键合并上线前，保持当前
  路径级 fail-closed 语义。
- Queue consumer 固定 `max_concurrency=1`，避免两个发布包并发读取同一 current；
  不得为追求吞吐量开启自动并发。

## 回执与切流门禁

状态链为 `registering → registered → queued → processing → complete/conflict/failed`。只有 D1 已
原子切换影子快照、R2 manifest/receipt 可回读且哈希一致，才算影子完成。
Queue 暂时性错误在前三次失败时回到 `queued` 并保留错误详情；第四次仍失败才写
`failed` 终态，避免客户端把仍会自动重试的消息误判为不可恢复失败。

生产切流必须另行满足：

1. 至少连续 7 天且不少于 20 个符合本契约上限的真实逻辑发布包双写；`ineligible`
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
