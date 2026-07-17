# 本地数据抓取与 GitHub 投递链路专项 Review 及整改方案

> 报告日期：2026-07-14  
> 评估基线：`main @ 8428f977`  
> 范围：维护者本地采集、TNR 即时抓取、任务队列、失败恢复、GitHub clone/push、`local-data` 云端摄入、知识治理  
> 本报告只新增评审文档，不修改现有采集、数据或发布逻辑。

## 一、执行结论

项目的大方向是正确的：来源抓取放在维护者住宅网络，GitHub Actions 只做经过 manifest 约束的摄入与离线重建；registry、人工层、机器层的边界也已经建立。当前困难并不是“没有工具”，而是**采集执行面和 GitHub 投递面都只完成了主干 happy path，缺少长队列运行所必需的断点、隔离、降级、收据和统一操作契约**。

两个难点并非彼此独立：

1. `refresh.sh all` 在 FIDE 已生成发布包、但 GitHub push 失败时会立即退出，后续 Chess-Results 队列不会执行。GitHub 网络问题因此会直接表现成“赛事一直抓不完”。
2. Chess-Results 批任务遇到一个不兼容赛事便终止整批。成功赛事可以整场检查点，但失败赛事没有逐页检查点、失败原文或隔离状态，同一个“毒丸赛事”会在下一次继续卡住队列。
3. 命令行已支持直接传 TNR/URL，但本地面板没有粘贴框；抓取成功只落仓库外私有文件，也没有本地预览，因此维护者难以在几十秒内看到“识别到什么、已抓到哪里、哪些页面失败”。
4. `local-data` + manifest 精确摄入机制值得保留，但 GitHub 传输仍是单点：代理探测可能误判、失败后不轮换路线、没有兼容当前 manifest 的 API 投递兜底，也没有从 push 到 ingest/rebuild/deploy 的端到端收据。
5. 顶层 `AGENTS.md` 只有三行管线摘要，且写了已被代码明确退役的 `refresh.sh verify`。另外，`docs/ARCHITECTURE.md` 仍保留一段允许贡献者上传抓取内容的旧流程，与当前治理规则直接冲突。知识没有形成单一、可执行、可由 CI 检查的契约。

建议把整改目标定为：

> **采集可续跑、坏目标不阻塞、TNR 一贴即见进度；发布进入本地 outbox 后与采集完全解耦，Git/代理/API 任一路线可投递，同一 release run-id 可追踪到 main、rebuild 和 deploy。**

首轮不应靠提高日访问预算或盲目加并发来“提速”。先消除重复请求、整批中断和不可诊断失败，吞吐会自然提高；并发应在检查点与全局限速正确之后再小步启用。

## 二、现状基线与实测证据

### 2.1 当前队列

`docs/data/audit/domestic-event-queue.json` 当前记录：

| 指标 | 当前值 | 说明 |
|---|---:|---|
| 总目标 | 201 | 公开的目标/历史状态总量 |
| `capture-event` | 129 | 队列认为需要整场抓取 |
| `refresh-snapshot` | 1 | 需要补快照 |
| `monitor` | 71 | 历史公开层已有完整快照 |
| 新私有 capture-state 成功 | 3 | 2026-07-13 新架构下的本地私有成功 |
| 按 30 天跳过后当前待尝试 | 127 | `129 + 1 - 3` |

公开队列统计与私有 capture-state 是两套口径：队列仍显示 129 个 `capture-event`，运行时才临时跳过 3 个近期成功目标。这会让“还有多少待抓”在面板、JSON 和真实调度器之间不一致。

### 2.2 最近两次失败

本机私有运行状态显示：

| run-id | 结果 | 已取得成果 | 终止原因 |
|---|---|---|---|
| `20260713-222346-969867e4` | 失败 | 251 秒内完整抓取 3 场、33 个有效页面 | 第 4 场 `tnr1213323` 起始页未解析到表格 |
| `20260713-223159-b187e060` | 失败 | 已请求起始名单、排名和第 1 轮 | `tnr1110333` 第 1 轮未解析到对阵 |

第一轮运行的实际吞吐约为 3 场/251 秒，即约 43 场/小时；把当前剩余目标按每场 9 轮、每场 11 个页面粗算，在零格式异常时约需 3 小时。由此可见，**主要问题不是理论带宽不足，而是一个格式异常即可让剩余队列永久停在原地**。

两次错误都只保留了 Python traceback。失败页面未写入 `raw/`，因此无法回答：页面是空赛事、团队赛、淘汰赛、无逐轮表、重定向页面，还是解析器真的发生了布局回归。

### 2.3 当前自动化测试

本次实跑：

- `python3 -m unittest -v Scripts.tests.test_local_pipeline`：28/28 通过；
- `bash -n Scripts/local/refresh.sh`：通过；
- `python3 -m compileall -q Scripts`：通过；
- `git diff --check`：通过。

现有测试较好地覆盖了 registry 权威、manifest 边界、私有目录和最近成功跳过，但几乎没有覆盖：真实赛事格式适配、逐页续跑、批次部分失败、代理路由选择、GitHub API 数据投递、云端收据和强推竞态。

## 三、难点一 Review：数据抓取

### 3.1 已有能力与应保留部分

以下机制方向正确，不应推倒重来：

- 唯一安全入口是 `Scripts/local/refresh.sh`，并通过 `CHINA_CHESS_MAINTAINER_LOCAL=1` 限定维护者本机来源访问；
- Chess-Results 当前固定 `link-only`，原始 HTML 和解析结果写仓库外私有运行区；
- 来源级共享配额、最小间隔和熔断状态持久化在仓库外；
- 任务有跨进程锁、run-id、run.json、run.log 和固定私有目录；
- 每个**完整成功赛事**会立即写 capture-state，后续赛事失败不会抹掉已完成赛事；
- `event-queue` 命令行已接受纯数字 TNR、`tnr123` 和完整 URL；
- registry 姓名与等级分权威、`sanitize_person_name`、勘误和人工数据边界已经硬化。

问题在于检查点粒度仍是“整场”，错误粒度仍是“整批”，产品入口仍只覆盖预设队列。

### 3.2 根因清单

| 优先级 | 根因 | 代码证据 | 实际影响 |
|---|---|---|---|
| P0 | 一个赛事失败即终止整个批次 | `sync_chess_results_event.py:461-489` 的目标循环没有单目标异常隔离 | “毒丸赛事”反复占据队首，后续目标永远没有机会运行 |
| P0 | 页面只在整场完成后统一落盘 | `scrape_event()` 先把所有 body 留在内存，`main()` 成功返回后才调用 `write_snapshot_bundle` | 中后轮失败会丢掉之前成功页面；重试重复消耗时间和访问预算 |
| P0 | 失败响应不留诊断证据 | `fetch_page()` 在解析器报错后直接抛异常，body 无法进入 `raw/` | 无法离线复现；只能再次访问来源“猜”问题 |
| P0 | 单一赛事格式假设 | 起始名单固定 `art=0`；配对要求 `Bo/White/Black/Result`，并依赖固定列号 | 团队赛、淘汰赛、无逐轮公开、旧模板和空组别会被统一错报为布局变化 |
| P0 | 批处理没有失败队列或隔离区 | capture-state 只记录成功，不记录 attempt、error、nextRetryAt 或 terminal status | 同一确定性失败会无限重试，调度器无法绕行 |
| P0 | `all` 把 GitHub 投递失败当成采集终止条件 | `refresh.sh:419-424` push 失败后 `fail`，不会执行 `run_private_events` | GitHub 大陆网络问题直接降低抓取完成率 |
| P1 | 轮数发现脆弱 | `rounds_from()` 只识别 h2 中英文界面的少量模式，备用逻辑读取实际不存在的 `opponents` | 标题轻微变化会得到 0 轮并报错；未利用队列已有的轮数字段和页面链接 |
| P1 | 面板没有 TNR 粘贴输入 | `panel.py` 只有队列前 1/3/10 和逐行按钮 | “拿到一个链接马上抓”只能记命令行语法，不能在面板完成 |
| P1 | 成果不可见 | 私有 JSON/HTML 没有本地只读预览 | 成功后只能翻日志和文件，无法快速核对赛事名、人数、轮次和异常 |
| P1 | 状态口径分裂 | 公开队列的 `nextAction` 基于仓库历史数据，近期私有成功只在运行时跳过 | 面板总数不等于真实待抓数；难以估算 ETA |
| P1 | `PARSER_LAYOUT_CHANGED` 过度聚合 | 空赛事、无对阵、团队模板、解析回归都使用同一错误码 | 不能自动决定重试、降级、隔离或需要开发修复 |
| P1 | 解析器没有契约测试 | 28 个测试中没有 starting rank/standings/pairings 的合成格式矩阵 | 一处解析改动容易修好 A、破坏 B，且 CI 无法发现 |
| P2 | 全部网络工作串行 | 单任务锁 + 单目标循环 + 单页面循环 | 高延迟网络下每个 RTT 都阻塞；但这不是首轮最主要瓶颈 |

现有另一个解析器已经明确知道团队赛事可能使用 `art=15/16`（`sync_chess_results_starting_rank_aliases.py:70-72`），但赛事整取脚本没有复用这项能力。这说明问题不是来源不可知，而是 provider 能力分散、没有统一的赛事格式探测层。

### 3.3 目标采集架构

建议把“抓一场赛事”改为可持久化的页面任务图：

```text
粘贴 TNR/URL
  → 标准化、去重、登记 target
  → probe：识别 individual/team/round-robin/knockout/standings-only/unsupported
  → 生成页面任务：starting-rank、standings、round-1...N
  → 全局来源限速器
  → 每页下载成功即原子保存 raw + sha256 + HTTP 元数据白名单
  → 版本化 parser adapter
  → 每页解析结果检查点
  → 赛事聚合校验
  → complete / partial / unsupported / retry-wait / quarantined
```

每个目标至少维护以下私有状态：

```json
{
  "tournamentID": "1110333",
  "status": "partial",
  "format": "individual-swiss",
  "attempts": 2,
  "pagesExpected": 13,
  "pagesFetched": 3,
  "pagesParsed": 2,
  "failedPage": "round-1",
  "errorCode": "PAIRING_TABLE_UNSUPPORTED",
  "nextRetryAt": null,
  "parserVersion": "chess-results-v2",
  "updatedAt": "..."
}
```

关键语义：

- 网络失败：指数退避后重试缺失页面，不重新抓已通过哈希校验的页面；
- HTTP 成功但格式不支持：不做无意义网络重试，立即进入 `unsupported` 或 `needs-parser`；
- 某轮无对阵但排名有效：允许形成 `standings-only` 或 `partial`，不要把已取得的可信层级全部判废；
- 单目标失败：记录后继续下一个目标，批次最终返回 `partial-success`；
- 同一结构错误连续出现 2 次：隔离 7 天或直到 parserVersion 更新；
- parserVersion 更新：只对私有 raw 做离线重解析，零来源访问；
- 原始响应仍只留本机，不进入 git、GitHub Actions 或公开对象存储。

### 3.4 TNR 即贴即抓的产品入口

本地面板顶部新增“抓取指定赛事”：

1. 一个多行输入框，可粘贴纯 TNR、`tnr123456` 或完整 URL，一行一个；
2. 前端立即标准化、去重并展示 TNR chips；无效输入在提交前指出；
3. 点击“开始私有采集”后 1 秒内显示已入队；
4. probe 完成后展示赛事名、识别格式、预计轮数和页面数；
5. 每场独立显示 `3/11 页面`、当前轮次、已用时间、是否来自缓存；
6. 成功后提供“打开本地预览”和“打开运行目录”，不暗示已公开发布；
7. 失败时显示结构化原因、已保存页面数、下一步是“续跑”“等待网络”还是“需要解析器支持”；
8. 支持一次粘贴多场，但默认仍维持来源级 1 req/s 和小并发。

所谓“快速看到成果”应拆成两层：

- **数秒级反馈**：TNR 已识别、任务已入队；
- **首个页面反馈**：赛事标题、名单人数、预计页面数；
- **完整成果**：全部轮次完成或清晰标记 partial/unsupported；
- **公开成果**：Chess-Results 在当前 `link-only` 政策下不存在这一步。若未来需要公开，必须先有单独的许可/隐私决策，不能把私有预览偷偷接到 `local-data`。

### 3.5 性能整改顺序

性能优化应按以下顺序执行：

1. **零重复请求**：逐页缓存、续跑、解析器升级只离线重放；
2. **坏目标绕行**：错误隔离后批次继续；
3. **减少无效请求**：probe 先识别赛事类型和轮数；
4. **连接复用**：在不改变访问间隔的前提下复用 HTTP session；
5. **小并发隐藏 RTT**：事件级 2 workers，仍由同一个全局 token bucket 控制起始请求间隔；
6. **测量后再调**：若 2 workers 能明显降低 wall time 且无封禁/错误率上升，再考虑 3；默认上限不应超过 3。

不要首先提高 `daily_budget=1800` 或把 `delay` 降到 0。当前剩余队列在典型 9 轮模型下本来就接近日预算上限，重复抓取和确定性错误才是预算浪费源。

## 四、难点二 Review：GitHub clone、push 与云端摄入

### 4.1 已有正确架构

当前 FIDE/Lichess 发布链路具备高质量安全边界：

```text
本地 staging
  → 完整性/语义/人数/勘误/许可校验
  → 原子晋升
  → 精确路径 + operation + SHA-256 release manifest
  → 本地 commit
  → force-push 单写者 local-data
  → ingest workflow 用相同 validator 复核
  → 只应用 manifest 文件
  → main 离线 rebuild
  → deploy
```

`run_manager.py` 会拒绝人工层、原始 HTML、跨来源路径和 manifest 外文件；当前测试也验证了这些边界。该模型应作为整改的中心，而不是退回宽目录 push、CI 回抓或人工复制数据。

### 4.2 当前缺口

| 优先级 | 缺口 | 证据 | 影响 |
|---|---|---|---|
| P0 | GitHub 网络检查不在 `health` 中 | `health_check.py:111-123` 只探 Chess-Results/FIDE/Lichess | 正式抓完后才第一次知道 GitHub 是否可投递 |
| P0 | 代理探测可把 HTTP 错页误判成功 | `refresh.sh:173-178` 的 curl 没有 `--fail`，只看进程退出码 | 502、403 或本地网关页面可能使脚本坚持错误的直连路线 |
| P0 | push 失败不轮换路线 | 代理只探测一次，三次重试复用同一个 `GIT_PROXY` | 直连偶尔能开首页但 Git smart HTTP 不通时，所有重试等价 |
| P0 | 采集与投递耦合 | `all` 的 FIDE push 失败会阻止事件采集 | 难点 2 直接放大难点 1 |
| P0 | API 数据发布脚本与现行安全模型不兼容 | `publish_data_via_api.py` 允许 manual/community/incoming/docs/data，且不生成/校验当前 release manifest | 这是一个危险的历史兜底；在新 ingest 下要么失败，要么复用无关旧 manifest，不能继续作为操作入口 |
| P1 | 没有投递 outbox | `push` 只依赖当前 HEAD 上的一个 manifest | 多个待投递发布包不能排队；本地代码提交和数据发布容易互相污染 |
| P1 | push 成功没有云端收据 | 成功信息在 `git push` 后立即显示“已发布” | 实际 ingest、main commit、rebuild 或 deploy 仍可能失败，维护者看不到最终成果 |
| P1 | ingest 使用移动分支头 | workflow 由某次 push 触发，却重新 fetch `origin/local-data` | 连续两次 force-push 时，较早 workflow 可能摄入较新的 release，run-id 与触发事件错配 |
| P1 | clone/fetch 没有统一网络包装器 | 自动代理逻辑只存在于 `refresh.sh` 的 push 函数 | 新 agent 仍可能直接 `git clone/pull/fetch`，重复踩大陆连接问题 |
| P1 | 代码发布兜底没有进入主文档 | `publish_code_via_api.py` 已能基于远端 main 创建 PR 分支，但 README/AGENTS 未说明 | agent 不知道何时用、如何选 source-base、如何处理冲突 |
| P1 | 本地 main 与数据发布职责混杂 | `commit_prepared_release()` 直接在当前分支 commit，再推到远端 local-data | 摄入 main 产生不同 SHA，本地 main 容易与远端 main 分叉；后续代码提交范围难解释 |
| P1 | 认证、DNS、TLS、代理、远端拒绝统一成 `GIT_PUSH_FAILED` | push 没有结构化 stderr 分类 | 用户无法知道该开代理、重新登录、等待 GitHub，还是修 manifest |
| P2 | 远端 `local-data` 仍是旧式历史提交 | 当前 `origin/local-data @ f56b7157` 无新 manifest | 手工 dispatch 现行 ingest 会失败；首次新发布会覆盖，但应显式清理/迁移 |

### 4.3 目标投递架构：本地 outbox + 多传输适配器

把发布事务拆成“生成”和“投递”两个状态机：

```text
采集/构建
  → release bundle 写入本地 outbox/<run-id>/
      manifest.json
      files/...
      delivery.json
  → 采集任务结束，不等待 GitHub

独立 delivery worker
  → validate bundle
  → route 1: Git direct
  → route 2..N: macOS 系统代理 / GITHUB_PROXY / 常见本地代理
  → route fallback: GitHub Git Database API
  → force-update local-data
  → 记录 remote commit SHA
  → 等待 ingest/rebuild/deploy receipt
```

核心要求：

- outbox 是不可变发布包；push 失败不会要求重新抓取；
- Git 和 GitHub API 两种传输必须提交**同一个 manifest 和同一组哈希文件**；
- API 兜底必须复用 `run_manager.validate_manifest()`，禁止自行维护另一套 `ALLOWED`；
- `publish_data_via_api.py` 在重写前应明确退役并在代码中 fail closed；
- `health` 增加 GitHub direct/proxy/API/auth 四项检查，但绝不把 GitHub 代理环境传给 Chess-Results/FIDE 来源请求；
- clone、代码 push、数据 delivery 共享同一个 GitHub 路由探测库；
- 对 Git smart HTTP 应使用 `git ls-remote` 或等价 endpoint 探测，而不是只看 github.com 首页；
- 每次失败记录 route、phase、exit code 和脱敏后的 stderr 分类；不记录 token、代理认证或 Cookie。

### 4.4 数据和代码必须分两条路

| 变更类型 | 正确入口 | 禁止做法 |
|---|---|---|
| FIDE/Lichess 机器发布 | release manifest → outbox → `local-data` → ingest | 直接 push main、宽目录 `git add`、API 随意列路径 |
| Chess-Results 私有采集 | 本机私有 runs/capture-state/local preview | 进入 `local-data`、上传 HTML/解析结果、CI 回抓 |
| manual/community 人工修正 | 短命代码/数据 PR 分支，人工 review | 抓取脚本自动提交、混进机器 manifest |
| 代码修改 | 短命 PR 分支；Git 不通时使用经过文档化的 API publisher | 通过 `local-data` 偷运代码、在采集机 pull/rebase |
| 派生索引 | GitHub Actions 离线 rebuild | 本地抓完后把整个 `docs/data` 当源数据上传 |

建议为维护者提供两个明确命令，而不是让 agent 自行选择底层 Git：

```bash
# 数据发布：只消费 outbox，不重新抓取
bash Scripts/local/refresh.sh deliver

# 代码发布：先 dry-run 三方合并，再创建远端 PR 分支
python3 Scripts/local/publish_code_via_api.py ...
```

clone 的治理也应明确：采集机已有工作副本时永不为了“同步”而重新 clone/pull。新机器初始化才允许走统一的 `github bootstrap`，它按 direct → system proxy → explicit proxy → GitHub archive/API 的顺序处理，并把结果记录在本机状态中。

### 4.5 云端链路必须按触发 SHA 摄入

`ingest-local-data.yml` 应使用 GitHub push event 的不可变 commit SHA，而不是运行时再读取移动的 `origin/local-data`。建议：

1. workflow 记录 `github.sha`；
2. fetch/checkout 该确切 SHA；
3. `run_manager apply --source-ref <event-sha>`；
4. 校验 manifest 内 run-id 与本地投递记录一致；
5. 将 run-id、source SHA、main commit SHA 写入 job summary 或轻量 receipt；
6. rebuild/deploy 继续携带 run-id；
7. 本地面板通过 GitHub API 查询该 run-id 的最终状态。

最终状态不能再只有“push 成功”，而应是：

```text
prepared → queued-for-delivery → pushed → ingested-to-main
  → indexes-rebuilt → deployed
```

任一阶段失败都可单独重试，且永不重新访问来源。

## 五、知识治理 Review：为什么 agent 会反复走错路

### 5.1 顶层 AGENTS.md 的问题

当前 `AGENTS.md:10-14` 只给三条摘要，不能回答：

- Chess-Results 的成功结果为什么不能进入公开仓库；
- 哪些来源能走 `local-data`；
- push 失败后是否需要重抓；
- 代码修改如何发布；
- GitHub 代理只能用于 Git，不能泄漏到来源；
- `data/generated`、manual/community、registry 的写权限；
- 什么情况下绝不能 pull/rebase；
- agent 修改采集链路后必须运行哪些测试。

更严重的是 `AGENTS.md:14` 要求运行 `refresh.sh verify`，而 `refresh.sh:462-464` 和 `Scripts/local/README.md:60-62` 都明确把 `verify` 列为退役命令。新 agent 按顶层指令执行必然得到 `COMPLIANCE_POLICY_BLOCKED`。

### 5.2 其他文档漂移

`docs/ARCHITECTURE.md:101-119` 同时保留两套互相冲突的“社区贡献流水线”：前一套只允许 target-only，后一套仍写贡献者上传解析结果、HTML/PGN 和 `refresh.sh contrib`。后者与 `docs/GOVERNANCE.md`、`validate_incoming.py` 和当前命令白名单冲突，必须删除或标记为历史废案。

`Scripts/local/README.md` 是目前最接近真实实现的文档，但 agent 不一定会主动读它。应由 AGENTS 明确要求：任何涉及抓取、数据发布、GitHub 投递的任务，在行动前必须先读该文件，并把其关键禁令直接复制到顶层。

### 5.3 建议写入 AGENTS.md 的强制块

以下内容可直接作为整改时的顶层契约基础：

```markdown
## 本地采集与 GitHub 投递铁律

1. 所有 Chess-Results/FIDE/Lichess 来源访问只允许维护者本机住宅网络执行；
   GitHub Actions、云主机、社区贡献工具不得回抓。唯一入口是
   `bash Scripts/local/refresh.sh <safe-command>`。
2. Chess-Results 当前是 link-only：原始页面、解析排名/配对、PGN、姓名候选只写
   仓库外私有运行区，禁止进入 git、local-data、API 或公开对象存储。
3. FIDE/Lichess 机器发布必须经过 staging、验证和 release manifest；只能把 manifest
   精确列出的文件投递到单写者 `local-data`，由云端 ingest 到 main 后离线 rebuild。
4. 采集机永不 pull/rebase。GitHub 网络失败时只重投已生成的 release/outbox，禁止
   为了 push 失败重新抓取。GitHub 代理只用于 Git/GitHub API，来源请求必须直连。
5. 代码和人工数据不得通过 local-data 发布；走短命 PR 分支。普通 Git 不通时使用
   文档化的代码 API publisher，先 dry-run 冲突检查。
6. 禁止手改 data/generated；人工修正只进 data/manual 或 data/community。registry 是
   姓名和等级分唯一权威；姓名勘误写 name-corrections.csv，转会写 federation-overrides.csv。
7. `verify/crawl/pgn/events/contrib` 等旧 refresh 命令已退役，不得调用。来源健康检查使用
   `refresh.sh health`；私有赛事核验使用 `refresh.sh event-queue -- <tnr-or-url>`。
8. push 成功不等于发布成功；必须确认 ingest、rebuild、deploy 的 receipt。任一云端阶段
   失败只重试该阶段，不回抓来源。
9. 修改管线后至少运行：本地管线单测、compileall、refresh.sh bash -n、community/incoming
   校验和 git diff --check；新增格式必须配合成/脱敏 parser fixture。
```

此外，建议增加 CI 文档一致性测试：扫描 AGENTS/README/ARCHITECTURE 中出现的 `refresh.sh <command>`，要求命令属于当前安全白名单；扫描已退役命令和禁止上传的 payload 描述，发现冲突即失败。

## 六、分阶段整改计划

### 阶段 0：止血与统一口径（0.5—1 天）

| 任务 | 修改范围 | 验收 |
|---|---|---|
| 扩写 `AGENTS.md` | 顶层操作契约 | 明确数据/代码两条路、link-only、no-pull、push 失败不重抓 |
| 修正文档漂移 | `docs/ARCHITECTURE.md`、README、local README | 删除旧 contrib/upload 流程；不再出现 `refresh.sh verify` |
| 退役危险 API 数据脚本 | `publish_data_via_api.py` | 重写前直接 fail closed，并指向 manifest/outbox 路径 |
| 修复 `all` 的控制流 | `refresh.sh` | GitHub 投递失败只标记 delivery-pending，仍继续事件采集 |
| 补 GitHub health | `health_check.py` | 同时报告 direct、system proxy、API auth；来源代理环境保持为空 |

### 阶段 1：让队列真正跑完（2—3 天）

| 任务 | 修改范围 | 验收 |
|---|---|---|
| 单目标异常隔离 | `sync_chess_results_event.py` | 1 个坏 TNR 不阻止后续至少 20 个目标；批次返回部分成功摘要 |
| 失败/重试状态 | 私有 capture-state schema v2 | 记录 attempts、errorCode、failedPage、nextRetryAt、status |
| 逐页 raw 检查点 | event collector | 每个 HTTP 成功页面立即原子落盘；进程中断后只抓缺页 |
| 离线重解析 | event collector CLI | 指定 run/tnr 可完全不联网重跑 parser |
| 错误细分 | provider parser | 至少区分 network、blocked、empty-event、no-pairings、team-format、layout-regression |
| 队列口径统一 | builder + panel | 面板明确显示历史公开完成、新私有完成、待抓、隔离、需解析器支持 |

完成本阶段后再启动“清空剩余 127 个目标”，否则只是继续用访问预算重复撞同一错误。

### 阶段 2：TNR 即贴即抓与本地成果预览（1—2 天）

| 任务 | 修改范围 | 验收 |
|---|---|---|
| 多行 TNR/URL 输入 | `panel.py` | 支持粘贴、去重、校验和一次入队多场 |
| 逐场实时进度 | run state / panel API | 显示页面数、轮次、缓存命中、ETA 和失败处理建议 |
| 本地预览 | 新 local-only handler | 展示赛事标题、名单/排名/轮次摘要；只监听 127.0.0.1 |
| 快捷续跑 | panel action | partial 目标一键只补缺页，不重新抓完整赛事 |

### 阶段 3：赛事格式适配和测试矩阵（2—4 天）

| 任务 | 修改范围 | 验收 |
|---|---|---|
| provider 格式探测 | 新 Chess-Results adapter 层 | 支持 individual Swiss、round robin、team art=15/16、standings-only；不支持格式可准确分类 |
| 去固定列号 | pairing parser | 用重复 header 序号/语义列映射，固定列只作已验证 fallback |
| 可靠轮数发现 | page links + queue metadata + heading | 三路交叉验证，异常时允许 partial |
| 合成 fixtures | `Scripts/tests/fixtures` | 不提交来源原文；用虚构人名和最小 HTML 覆盖每种表格 |
| parser 合同测试 | tests | 每种格式成功/缺列/重复列/空表/编码异常均有用例 |

### 阶段 4：可靠 GitHub 投递（2—3 天）

| 任务 | 修改范围 | 验收 |
|---|---|---|
| release outbox | run manager / refresh | 可同时保存多个 pending release；采集任务不等待 GitHub |
| 路由轮换 | 共用 GitHub transport | direct/每个 proxy 逐一路由实测 Git smart HTTP；失败自动切换 |
| manifest API fallback | 重写 data API publisher | API 与 Git 路径使用同一 validator、同一文件清单和哈希 |
| 确切 SHA ingest | workflow | 连续 force-push 两个 release 时各自按 event SHA 摄入或明确去重 |
| 云端 receipt | workflow + panel | 本地 run-id 可见 pushed/main/rebuild/deploy 状态和失败链接 |
| 代码发布说明 | AGENTS/local README | clone/push 不通时 agent 能正确使用 code API publisher，不碰 local-data |

### 阶段 5：有数据后的性能调优（1—2 天）

- 收集至少 100 场的 request duration、pages/event、cache hit、format、failure 分类；
- 以 1 worker 为基线，A/B 测试 2 workers；
- 保持全局来源请求起始间隔不低于当前策略；
- 只有在错误率、封禁率不升高时才将 2 workers 设为默认；
- 任何性能修改必须能在配置中快速退回 1 worker。

总投入估算：8—15 个工程日。阶段 0—2 完成后，使用体验和完成率就会有明显变化；阶段 3—4 决定长期稳定性。

## 七、验收指标

### 7.1 抓取侧

| 指标 | 验收线 |
|---|---:|
| 粘贴 TNR 到显示“已入队” | p95 < 1 秒 |
| 粘贴 TNR 到首个页面摘要 | p95 < 30 秒，来源可达前提下 |
| 单个坏目标对批次的影响 | 后续目标继续，阻塞数 = 0 |
| 中断续跑重复请求 | 已成功页面重复请求 = 0 |
| 格式错误网络重试 | 默认 0 次；parser 更新后离线重放 |
| 队列终态率 | 剩余 127 个目标全部进入 complete/partial/unsupported/quarantined 之一，不允许长期无状态 |
| 失败可诊断率 | 100% 有 errorCode、failedPage、parserVersion 和私有 raw/hash |
| 状态口径 | 面板待抓数与调度器实际选择数完全一致 |

### 7.2 GitHub 投递侧

| 指标 | 验收线 |
|---|---:|
| push 失败后的数据安全 | 100% release 保留在 outbox，不重新抓取 |
| 可用路线自动发现 | direct/proxy/API 任一可用即能投递 |
| manifest 一致性 | Git 与 API 两种传输的 files/hash 完全一致 |
| 错误分类 | DNS/TLS/proxy/auth/remote-reject/workflow-failure 可区分 |
| 触发一致性 | ingest 的 source SHA = push event SHA |
| 端到端可见性 | 100% release run-id 可追到 main commit 和 deploy 结果 |
| 来源网络隔离 | 测试证明 GitHub proxy 不进入 Chess-Results/FIDE/Lichess 请求环境 |

### 7.3 数据正确性与合规

- registry 权威规则、姓名勘误钉死、联邦覆盖表和 `sanitize_person_name` 不得弱化；
- `data/manual`、`data/community`、`data/incoming` 永不进入机器 release manifest；
- Chess-Results link-only 内容永不进入 git/local-data/API；
- parser partial 状态不能伪装成“完整赛事”；
- 本地预览必须明确“私有采集、未公开发布”；
- GitHub Actions 继续保持零来源抓取。

## 八、测试方案

### 8.1 采集单元测试

- TNR 标准化：纯数字、tnr、完整 URL、重复、非法 host、过短/过长；
- individual/team/round-robin/standings-only 合成 HTML；
- 重复 `No./Pts./Gr` header 的语义列映射；
- 轮数来自 heading、链接、人工队列元数据时的一致性与冲突；
- 第 N 轮失败后，前 N-1 轮均已原子落盘；
- parser 报错不触发网络重试；网络超时只重试缺页；
- batch 中 A 成功、B 失败、C 成功，最终状态和退出码正确；
- parserVersion 更新后离线重放，不调用 `urlopen`。

### 8.2 GitHub 传输测试

- 直连返回 HTTP 502 时不得判为可用；
- 直连 push 失败后会尝试下一代理，不在同一路线空转三次；
- 认证失败不应误报网络失败；
- API fallback 拒绝 manual/community/incoming/raw HTML；
- API fallback 缺 manifest、hash 不符或 source 不支持时 fail closed；
- outbox 有两个 release 时可按顺序或显式 latest-wins 投递，不能静默丢包；
- workflow 连续接收两个 force-push 时使用各自 event SHA；
- deploy 失败时 receipt 停在 `indexes-rebuilt`/`deploy-failed`，重试不触发来源访问。

### 8.3 文档一致性测试

- AGENTS/README/docs 中出现的 `refresh.sh` 命令必须属于白名单；
- `verify/crawl/pgn/events/contrib` 不得被描述为可运行入口；
- 禁止出现“社区上传 Chess-Results HTML/PGN/解析结果”的现行流程；
- `local-data` 描述必须同时出现 manifest、exact paths/hash、ingest 和 no-pull；
- 数据发布和代码发布必须被明确区分。

## 九、风险与明确不做事项

1. **不把 Chess-Results 私有结果重新接回公开发布。** 抓取效率问题与再发布授权是两件事；整改只改善私有采集与预览。
2. **不恢复 GitHub Actions 来源抓取。** 数据中心 IP 限制和治理边界决定了 CI 只能离线。
3. **不以增加代理池或共享住宅 IP 额度为方案。** 当前问题是任务语义和恢复机制，不是绕过来源限制。
4. **不在没有逐页检查点前启用高并发。** 否则只会更快耗尽预算并放大不可复现错误。
5. **不让 parser 错误进入通用网络重试。** 结构错误需要离线分析或格式适配，重试相同页面没有价值。
6. **不重新引入 pull/rebase 作为采集机常规同步方式。** 代码更新与数据发布应分别走 PR/API 和 outbox/local-data。
7. **不让 API fallback 维护第二套宽松白名单。** 安全策略必须只有 `run_manager.validate_manifest()` 一份实现。
8. **不手改 generated 修进度。** 所有成功、失败和勘误必须落到可重建的状态或人工权威文件。

## 十、建议的 48 小时动作

按收益和依赖排序，最先完成以下六件事：

1. 扩写 `AGENTS.md`，删掉 `verify` 错误指令，清理 `docs/ARCHITECTURE.md` 的旧贡献流程；
2. 修改 `all`：push 失败进入 `delivery-pending`，继续执行事件队列；
3. 修改事件目标循环：单场失败记录后继续，不让 `tnr1213323` 或 `tnr1110333` 阻塞后续赛事；
4. HTTP 成功后立刻保存私有 raw，再解析；加入 `failedPage/errorCode/attempts`；
5. 面板新增多行 TNR/URL 粘贴框和逐场进度，先实现“可入队、可见、可续跑”；
6. 在重写前禁用 `publish_data_via_api.py`，同时给 `health` 增加真实 GitHub smart-HTTP/代理探测。

完成这六项后，再恢复队列批跑。预期结果不是“所有赛事都必须解析成功”，而是**所有目标都能被推进到一个诚实、稳定、可处理的终态，且任何 GitHub 网络故障都不会让已抓数据丢失或阻止下一场采集**。

## 十一、最终判断

项目并不需要第三套抓取器或另一条临时上传通道。它需要把已经正确的安全架构补成真正的运维产品：

- 抓取从“脚本调用”升级为可持久化任务系统；
- 错误从 traceback 升级为可调度状态；
- 成果从私有文件升级为本地可视预览；
- GitHub push 从单一路线升级为 outbox 驱动的多传输投递；
- “已 push”升级为 ingest/rebuild/deploy 的端到端收据；
- 隐含经验升级为 AGENTS.md + CI 可验证的团队契约。

做到这些后，剩余赛事数量不再是一个会反复归零的人工待办，而是一条可以持续消化、可准确估算、失败也会自动绕行的队列；大陆 GitHub 网络也从采集流程的阻塞条件，降级为可以稍后恢复的投递条件。
