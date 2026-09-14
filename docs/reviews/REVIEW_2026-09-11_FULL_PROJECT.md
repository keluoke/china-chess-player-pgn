# 全项目代码、工作区与数据维护管线审查

审查日期：2026-09-11；实施与上线验证延续至 2026-09-12。基线：远端 `47cee8f02d4813871cb427987ec3bf0e61dea516`；
Lichess 实施提交 `5c772bba50764919a9199bddc40b3cf7af7ac1c0`。

## 1. 结论

项目不是“完全无效的 AI 代码堆”，但存在明显的长期补丁堆积。核心数据正确性防线
有实际作用，事实表、registry 权威、精确 manifest、R2 内容寻址、快照一致性门禁
都不应当为了减代码而删除。当前主要成本来自多职责大文件、历史入口和文档没有
同步退役，以及为不同来源使用同一维护政策。

| 待评估说法 | 判断 | 证据与含义 |
|---|---|---|
| 双工作区划分不合理 | 不准确 | collector 保留采集状态/outbox、不 pull；code 稀疏检出 main。隔离必要，问题是安装/契约同步成本，而不是应合并工作区 |
| 存在无效/冗余代码 | 部分准确 | AST 发现实际重复函数，旧 PGN 工具链仍被文档当成当前入口，多处超长过程；不能仅按行数或写作风格判定 AI 来源 |
| 数据维护管线完全不合理 | 不准确 | 校验、原子发布、可追溯回执合理；Lichess 被强制本地维护和 ingest 推送后重放基线不合理，已整改 |
| 旧 by-player 反向反馈问题仍原样存在 | 不准确 | 当前 build_player_facts 产生事实表，canonical_player_facts 校验同快照/哈希；event-details 和 completeness 已使用事实输入 |
| Lichess 开放数据需要维护者每月手工处理 | 不准确 | 官方提供月度广播数据，CC BY-SA 4.0；现已建立 GitHub 定时流程 |
| 已归档来源广播即可称全台完整 | 不准确 | 公开范围与实际全台数不同；未匹配残差和未完局仍必须保留缺口 |

范围包括全部已跟踪自有代码/配置的结构扫描、入口/依赖/重复检测，关键采集→事实→
投影→发布→线上链路的人工审阅，以及 Python/前端/Worker 回归。共 159 个自有文件、
50,963 行，逐文件职责见 [覆盖清单](REVIEW_2026-09-11_CODE_INVENTORY.md)。
第三方压缩 viewer 作为依赖边界检查，未声称对压缩包逐行安全审计；未访问 FIDE 或
Chess-Results 来源。采集工作区大量改动属于历史运行时覆盖，未 reset、pull 或清理。

## 2. 分区与架构是否合理

| 分区 | 当前职责 | 判断 | 优化方向 |
|---|---|---|---|
| kimi collector | 本地住宅采集、私有原始响应、状态、staging/outbox | 合理 | 保留 append-only 采集历史，以已提交 main 的 runtime manifest 安装代码；不要把其 ahead/behind 当作需要 pull |
| kimi-code | 普通 main 开发、人工 CSV、workflow、站点代码 | 合理 | 实验与交付文档移出可部署目录；完整生成数据在隔离构建中使用 |
| Scripts/local | 面板、进程/锁、运行管理、Git/API 发布、R2、回执 | 边界过宽 | 拆成 capture control / release transaction / storage adapters；保留现有命令作为薄入口 |
| Scripts 根目录 | 采集、解析、身份、事实、投影、诊断共置 | 部分合理 | 按职责分包，禁止以“sync”命名掩盖写入派生层；先拆纯函数再迁路径 |
| data/manual、community | 人工证据/强制勘误/转会/命名 | 合理 | 可合并 schema 说明，不合并写权限；保持机器不能覆盖人工知识 |
| data/generated 与 docs/data | 机器事实与公共投影 | 部分合理 | registry/bulk 作为输入却放 docs/data，命名容易误导；先声明每个产物的 writer/readers，再渐进迁移 |
| Cloudflare Pages + R2 | 静态网页与 PGN 对象 | 合理 | 保留内容寻址和正文验证，统一 mutable 赛事接口与棋手接口的缓存语义 |
| Cloudflare shadow ingest | 未来迁移实验：HMAC、R2/D1/Queue、配额、分片 | 有隔离但维护成本高 | 未完成双写验收前冻结新增功能；不要再扩成第二生产重建入口 |
| GitHub Lichess 月度维护 | 开放库下载与 staging、精确 artifact 发布 | 新增且必要 | 仅开放该来源；复用既有事实构建和部署，不复制业务规则 |

目标数据图：

```text
FIDE / Chess-Results → 本机 refresh → staging + manifest + outbox → local-data ingest ┐
Lichess 月库 → GitHub 每月5日 → staging + R2 校验 + 精确 artifact → main 快进         ├→ main 输入 SHA
manual / community → main 人工审阅提交                                               ┘
main 输入 → registry + 不可变赛事/PGN事实 → player-event/game-facts
          → 完整性门禁 → 身份/赛事/棋手投影 → 搜索/API/统计
          → 全量棋手 PGN R2 认证 → 一个 snapshot → deploy → 线上正文验证
```

## 3. 发现与优先级

### P1-01：Lichess 没有云端维护入口，已造成真实月份缺失（本次修复）

审查前 `.github/workflows/` 没有广播定时任务；`AGENTS.md` 与本地 README 把三个
来源全部限制在住宅网络。线上/本地已发布广播 manifest 为 78 片，最新 2026-06，
1,146,297 局。官方目录已公布 2026-07（40,038 局）与 2026-08（48,940 局），
缺失 2 片、88,978 局。完整原档的缺失与中国棋手投影缺失不能用同一个局数表达。

更具体的旧闭环缺口：`Scripts/local/refresh.sh:964` 的 run_lichess 下载到 staging，
随后只晋升清单、youth 和 lichess-events，既不晋升 raw 月片，也不在此事务上传
这些新月片到 R2。只看 mirrored=true 不能证明公开分发对象已存在。

整改：新增每月 5 日定时任务、严格来源能力开关、R2 旧片正文验证和缺片条件写入、
完整帧/局数验证、精确 artifact 与独立 publish 作业。实际切片更新和上线证据见
本文最后的交付记录。

### P1-02：ingest 校验之后自动 rebase 可绕开三方冲突门禁（本次修复）

`.github/workflows/ingest-local-data.yml` 先 apply 检查 baseline/current/candidate，
但 Commit as bot 原先没有关闭 `Scripts/ci_commit_push.sh` 默认的 rebase 重试。
只要 main 在 apply 与 push 之间改变，同一路径就可能在重新基线时选用发布候选，
而没有重做原来的三方校验。新增第二个云端输入生产者后，这个窗口更需要封闭。

整改：ingest 显式 `CI_COMMIT_REBASE_ON_CONFLICT=false`，快进失败即停止。月度
发布同样不 rebase，并在落盘前复核远端 main。已提交 artifact 的重试只有在其
所有路径与远端正文完全一致时才恢复下游。

### P1-03：中国棋手广播投影的覆盖范围比 registry 窄（已完成并上线，见实施记录）

`Scripts/sync_lichess_broadcast_bulk.py:921` 的 youth_matches 要求当前 federation
等于 CHN 且存在 birth_year。即使 PGN 有精确 FIDE ID，缺出生年的 registry 成员
或已由覆盖表收录的转出棋手仍被排除。构造同一 FIDE ID 的控制样本：CHN/1994
返回 adult；CHN/空出生年和 SGP/1994 都返回空列表。当前 main 注册表共有 11,646 人，其中 375 人缺出生年。进一步离线扫描截至
2026-06 的 78 个本地月片，发现其中 10 人、128 条带精确 FIDE ID 的广播记录，
会被这个过滤条件排除。这里统计的是广播记录，不是已跨来源去重的全站缺失局数；
是否被其他来源补入仍需联合事实表核对。当前 registry 没有非 CHN 成员，转出场景
是已复现的条件风险，不声称当前发生了转出棋手遗漏。

整改：先按 registry 收录集合建立全量 by-player 事实；年龄组只是投影，新增
unknown-age 分类而不是丢棋局。转出棋手遵从 federation-overrides 的收录政策，
不可直接修改注册表。验收须对“有 FIDE ID、无出生年、已转出、姓名歧义”做对照。

### P1-04：旧工具与操作文档仍形成第二条发布路径（已完成并上线，见实施记录）

`docs/PGN_SCOUT.md:3` 仍推荐 pgn_scout → promote_public_pgn → sync_static_pgn
作为当前采集与索引路径，其第 151 行也直接指导晋升/重建。refresh 已退役对应命令，
正式 snapshot 构建也刻意不运行 sync_static_pgn，但这些大工具仍能单独被使用。
`sync_static_pgn.py:193` 仍读旧棋手索引，这只能保留为明确的历史迁移功能。

整改：把可执行操作文档收敛到现行入口；保留有用离线库函数，旧 CLI 改为显式
migration-only，增加调用阻断测试。先证明 imports/手动恢复没有依赖，再删除。
不能把“无 workflow 引用”当成死代码的充分条件。

### P1-05：重复 Event 标签被拆成虚假的独立棋局（本次实跑发现并修复）

首轮 GitHub 月度任务 `34618407440` 在 2026-02 严格局数检查失败：旧解析器数出
19,753 局，官方目录为 19,752 局。离线检查原档发现同一局有连续两条相同 Event
标签，旧解析器按每条 Event 切分，多生成一个只有标题的假棋局。并非来源少局，
不能通过改目录计数或忽略差额解决。

整改提交 `3cefa140a20d3ba06c3f65e839dbb3e3b6b8f815` 修正广播流式解析与下游
静态 PGN 拆分边界；回归覆盖重复标签、跨读取块与 CRLF。78 个已有月片离线
重新计数全部与原 manifest 一致，原档字节与哈希保持不变。

### P2-01：大文件和真实复制函数抬高维护成本（已完成并上线，见实施记录）

主要热点：sync_domestic_players 2,029 行；sync_chess_results_event 1,747 行；
run_manager 1,376 行；panel 1,320 行；refresh.sh 1,220 行；app.js 2,621 行。
更能说明问题的是 upload_bulk_to_r2.main 长 402 行，event_report 长 347 行，
sync_chess_results_event.main 长 374 行，一次改动容易碰到多个状态机。

AST 精确重复（不含注释和位置）：pgn_scout.py:385 与 reconcile_pgn_sources.py:694
的 download_chess_results_pgn；HTML 处理器 handle_starttag 在 pgn_scout、
promote_public_pgn、reconcile_pgn_sources 三处完全重复。重复是可验证事实；
代码是否由 AI 生成无法仅从文件内容判定。

整改：优先抽离下载/解析纯函数、R2 对象验证和事务状态转换；不要引入新通用框架。
`build_player_facts.py:517` 的 bulk 映射失败回退还在每条索引记录内线性扫描全部
PGN，最坏为 O(索引条数 × 棋局数)，建议预建精确与宽松键索引并保留歧义集合。
一次只迁一个能力，旧入口保留适配器，利用现有 parser fixture 与发布故障用例
证明行为等价。不能为了缩短函数删掉有实际事故依据的校验。

### P2-02：工作区实验与公共目录混杂，测试受未跟踪文件影响（已完成，见实施记录）

kimi-code 留有未跟踪的 estimated-rating 实验脚本/测试、历史交付文档及
`docs/data-pipeline-assessment-report.html`。本次原工作树全量 unittest 的唯一
失败来自这个既有未跟踪 HTML：公共文案测试扫描 docs/*.html，遇到“抓取”文本。
它未进入 Git，不能把这个本地失败归咎于本次提交，也不能擅自删掉用户材料。

在保持 Git 元数据和可执行权限的已跟踪文件隔离副本中，414 项测试通过（2 跳过）。
整改：实验移到显式 experiments/ 或仓库外；报告放 docs/reviews/ 的 Markdown，
生成站点时对 HTML 也有公共清单约束；测试的公共面扫描应与发布清单同源。

### P2-03：审计/影子/观察层仍有演进债务（已完成并上线，见实施记录）

- `build_person_observations.py:176` 跳过已有 FIDE ID 的记录，统一证据时间线尚不完整；
  应逐步使用稳定 observation key 覆盖所有人员，不借此自动跨赛事并人。
- `build_release_snapshot.py:118` 的 optional_script 允许缺失构建器被跳过；目前下游
  一致性门禁能挡住多种缺失，但生产必要步骤应在 DAG 上明确 required，避免依赖
  间接失败。input_facts 也应明确区分原始输入和本次构建产物。
- `functions/api/event-pgn.js:10` 仍用可变赛事路径和一天缓存；棋手包已有内容寻址。
  赛事对象修订时这两个接口的更新延迟不同，建议统一为 receipt 解析后的对象 URL。
- Cloudflare shadow 已存在 client/Worker 双份路径/配额/manifest 规则。保留跨语言
  指纹一致性测试，使用一份版本化协议样例，不同时扩展两套生产事实重建逻辑。

## 4. 优化实施顺序与验收

| 顺序 | 工作包 | 预期改动 | 必须满足的验收 | 预计工作量 |
|---|---|---|---|---|
| 0 | 本次 Lichess 与并发修复 | 定时维护、历史缺片、R2 回读、契约、ingest 快进保护 | 实际 GitHub 运行 + 新切片哈希 + main/rebuild/deploy/线上一致 | 本次完成闭环 |
| 1 | 广播事实全收录 | registry 集合驱动、unknown-age、转出棋手 | 不丢已有棋局，新增有证据的遗漏，身份字段不变 | 2–3 个工作日 |
| 2 | 旧入口与文档收敛 | scout/promote/sync_static 标迁移态；运行手册唯一入口 | 全文档命令检查 + CLI 阻断 + 零新来源访问回归 | 1–2 个工作日 |
| 3 | 管线模块化 | parser、capture state、release、storage adapter 拆分 | 同一 fixture 与相同输入生成同一语义事实/manifest | 4–6 个工作日 |
| 4 | DAG 与缓存 | 显式 required 输入/输出，月片+registry/规则版本缓存 | 冷/热构建 facts/hash 等价，输入变化不漏更新 | 3–5 个工作日 |
| 5 | 前端与目录卫生 | app.js 拆路由/载入/视图、HTML 发布清单、实验分区 | 搜索/深链/棋谱代理行为不变，评审资料不进站点 | 2–4 个工作日 |

工作量是实施估算，不是承诺日期。避免一次全仓重写；先处理有数据损失或并发覆盖
风险的缺陷，再降低维护成本。没有证据的“看起来是 AI 写的”不应成为删除依据。

## 5. 验证与交付记录

本地必需校验：226 项管线/parser/文档/月度流程测试通过；compileall、refresh.sh
语法和 git diff --check 通过。隔离已跟踪树 414 项通过（2 跳过）；前端姓名 5 项、
搜索核心、PGN 代理用例以及 Worker 9 项通过。失败分支日志中的 synthetic error
是故障注入用例的预期输出，不能据此把测试判成失败。

采集私有 outbox 实查：8 包 online-verified，5 包 abandoned，没有 pending 包；
不能因历史失败包仍有目录就判定投递持续报错。

契约文档已纳入 collector runtime 的精确安装清单，AGENTS、本地 README 与月度
维护手册从同一已提交 main 安装，避免两工作区继续执行不同铁律。

已验证的月度任务：`34656272076`（success）；发布提交：
`1c8cef861ed21c2963084177f6b6aa8a78f72f41`。共 89 个精确 manifest 路径；
完整库为 80 片、1,235,275 局，比原 78 片新增 88,978 局。

| 月片 | 局数 | 压缩字节数 | SHA-256 |
|---|---:|---:|---|
| 2026-07 | 40,038 | 24,535,400 | `714d0eb99f99fca8d791142038b6c59b5ca6a51b3339bd3891a92f4bdffcbf0c` |
| 2026-08 | 48,940 | 31,010,232 | `e227c35c3207ebade754849c1825982e2717ae82c013013d896778e27be77724` |

交付闭环（2026-09-12 核验）：

- [代码 CI 34656261715](https://github.com/keluoke/china-chess-player-pgn/actions/runs/34656261715)：success。
- [月度维护 34656272076](https://github.com/keluoke/china-chess-player-pgn/actions/runs/34656272076)：success。
- [完整重建 34656918997](https://github.com/keluoke/china-chess-player-pgn/actions/runs/34656918997)：success，输入为上述发布提交。
- [部署 34658999440](https://github.com/keluoke/china-chess-player-pgn/actions/runs/34658999440)：success，部署提交 `4640e96ef43bc55ef34f0a2ec4fe5083f773ad0d`。
- 普通生产 URL 的 snapshot、两个 bulk manifest、月度 receipt、public-events、
  player-pgn-r2-receipt 六份 JSON 均核对 MIME 和完整正文，与部署提交逐字节一致。
  线上 snapshot 为 `20260911T230947Z-bf455b6e`，inputCommit 为
  `1c8cef861ed21c2963084177f6b6aa8a78f72f41`。
- 80 个公开 R2 月片全部 HEAD 成功且长度匹配；7、8 月原档经公开 URL 下载后
  SHA-256 与上表一致。云端任务另对全部 80 片执行完整帧、局数和正文哈希校验。
  这是月库归档完整性的证明，不把未匹配的赛事广播残差宣称为全台棋谱完整。

每月 5 日北京时间 11:17 的 GitHub schedule 已启用；未来首次定时触发尚未发生，
本次以同一 workflow 的手动触发验证完整执行链。

官方来源：[Lichess Broadcast 月度目录与 CC BY-SA 4.0 声明](https://database.lichess.org/#broadcasts)。

## 2026-09-13 优化续作

上述五项的具体改动与验收证据见 [实施记录](IMPLEMENTATION_2026-09-13_REVIEW_OPTIMIZATIONS.md)。原问题描述保留为评审时点证据。
