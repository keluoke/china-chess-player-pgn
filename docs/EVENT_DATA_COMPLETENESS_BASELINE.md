# 赛事数据完备性整改设计基线

状态：**分阶段实施中**。本文档固化三方 code review 后的整改方案，是
`AGENTS.md` 铁律二的实施细则。完整性报告、公开直播范围 PGN 门禁及 Lichess
广播交叉归档已实施；云端字段级三方合并等其余缺口仍按 P0 处理。

## P0 缺陷（当前实现）

| # | 缺陷 | 位置 | 后果 |
|---|------|------|------|
| P0-1 | "云端比对合并"实际发生在本机旧工作区；采集机禁止 pull，云端 ingest 只按 manifest 覆盖文件，无三方合并、无 `baseCommit` 校验 | `Scripts/sync_chess_results_event.py` 本地 merge；`Scripts/local/run_manager.py` apply | 本地旧副本可能覆盖云端较新赛事数据 |
| P0-2 | PGN 不在"完整赛事"门禁内：名单/排名/轮次齐即标 `complete`，PGN 独立后处理，下载错误仍返回成功，已有文件不校验与对阵的匹配 | 采集完成判定与 `Scripts/fetch_event_pgn.py` 错误统计 | "complete" 不等于全量；PGN 缺失/错配静默通过 |
| P0-3 | 面板仍是"私有抓取/发布"两条泳道口径，发布中心只列 FIDE/Lichess | `Scripts/local/panel.py` | 抓完却找不到可投递包，误操作 |
| P0-4 | 顶层列表"非空整体覆盖"式合并：无逐棋手/轮/台/局稳定键、无删除语义、无冲突回执；`partial` 可进发布路径 | 采集器 merge | 数据丢失或伪完整 |
| P0-5 | 团队赛标 unsupported，不满足"赛事所有数据" | 采集器团队赛分支 | 覆盖缺口 |
| P0-6 | 前台仍公开显示 Chess-Results 名称、信源原名和外链 | `docs/app.js` 赛事页 | 违反去来源化契约 |

## 1. 数据契约（固化）

- 原始 HTML 永远只在维护者本机私有区。
- 可发布对象仅为清洗后的：赛事元数据、名单、最终排名、逐轮对阵/结果、完整 PGN。
- Chess-Results 公共对象和前台不出现 `source`、`sourceRefs`、外链、信源原名；
  对外只表达"赛事数据已清洗 / 完整度 / 更新时间"。Lichess 保留 CC BY-SA 4.0
  许可证与署名义务。
- registry 是姓名、FIDE ID、等级分唯一权威；赛事对象仅保存赛事事实，其中的
  身份字段永不反向写入棋手主档。

## 2. 完整赛事门禁（completeness report）

每场赛事（每 TNR 每次采集）必须生成 completeness report：

```json
{
  "tournamentID": "…",
  "roster":     {"expected": N, "captured": N, "missing": []},
  "standings":  {"expected": N, "captured": N},
  "rounds":     {"expected": R, "captured": R, "missingRounds": []},
  "results":    {"boards": B, "resolved": B, "anomalies": []},
  "pgn":        {"games": G, "matchedPairings": M, "matchRate": 0.0,
                 "unmatchedGames": [], "pairingsWithoutGame": []},
  "identity":   {"unresolved": []},
  "verdict":    "complete | partial | quarantined",
  "reasons":    []
}
```

- 只有全部达标才可标 `complete` 并进入发布。
- `partial`、团队赛未解析、PGN 缺失/错配（匹配率低于阈值且无"来源未公开棋谱"
  证据）、身份未决：一律隔离，不发布，不得伪装为完整赛事。
- "来源未公开逐轮/棋谱"是合法的完整性豁免，但必须在 report 中带证据记录，
  verdict 记为 `complete-with-source-gaps`，前台展示对应缺失原因。
- PGN 下载/解析错误必须使 PGN 项失败并阻断 `complete`，不允许统计后返回成功；
  已有 PGN 文件在门禁时必须重新校验与当前对阵的匹配率。

### 2.1 PGN 完整度的两种不可混淆口径

- `full-board-complete` / `matched-full`：覆盖全部已下且非轮空对局；只有此状态
  可称“全台棋谱完整”。
- `source-published-complete` / `matched-advertised-complete`：覆盖来源实际公开
  的全部直播棋谱，允许只覆盖前 N 台；适用于李成智杯、棋协大师赛等客观上仅
  提供直播台棋谱的赛事。该状态是“公开范围完整”，不是“全台完整”。
- `not-published`：来源没有公开棋谱链接，不是抓取失败，不进入补抓队列。
- `source-published-partial` / `source-published-missing`：来源已公开但存在少局、
  错配、下载空响应或未归档，必须保留为缺口；不得根据“通常只直播前十台”推断
  完整。

门禁分母以每轮来源实际宣称/返回的可用棋谱为准，排除轮空和明确判负；每局必须
与 `round + board + 白黑 playerNo` 唯一匹配。直播台数可以因轮次变化，不能把
固定“前十台”写死成分母。

### 2.2 亚少赛、世少赛的 Lichess 交叉归档

Lichess Broadcast 月度库作为 Chess-Results 对阵事实层的补充棋谱源，流程为：

1. 仅从严格主赛清单选择标准慢棋的亚少赛/世少赛组别，排除 rapid、blitz、
   schools、junior、cup、training、eastern 等相邻赛事。
2. 先校验赛事系列、项目、年份/日期窗口及年龄/性别组，再以轮次和双方身份匹配；
   FIDE ID 优先，规范化姓名只作回退。
3. 只有唯一 TNR/台次候选才可归档；歧义局拒绝，广播容器总局数、已匹配局数和
   未匹配残差写入 manifest。
4. 赛事投影位于 `docs/data/bulk/lichess-events/`，保留 Lichess Broadcast
   名称、URL、CC BY-SA 4.0 许可证及署名。月度 `.pgn.zst` 原档只驻留本地/R2，
   不进入 Git manifest；校验器接受合法 Zstandard skippable frame。

Chess-Results 与 Lichess 的并集决定 `advertisedPGN` 分母；任一广播容器存在未
唯一匹配的局时，只能按实际事件覆盖标记 complete/partial，不能因有 PGN 文件
就整体宣称完整。

## 3. 云端三方合并（迁移合并点）

本机不做跨版本合并（采集机永不 pull，本机工作区不可假设等于云端 main）。

发布包（候选）新增携带：

- 赛事 ID 与每类对象的自然键；
- 候选基线：本机上次已知 main 的版本/每文件 SHA-256（`baseCommit` + 对象级哈希）；
- 本地清洗版本（parserVersion + 清洗规则版本）。

云端 ingest 以**当前 main** 为基线做字段级三方合并：

```
本机清洗候选 + 当前 main + 候选基线
→ 一致跳过 / 本地优先覆盖 / 保留更完整字段 / 隔离身份冲突
→ 合并回执
```

- 自然键：排名 = `playerNo`；配对 = `round + board + 白方 playerNo + 黑方
  playerNo`；棋局 = 规范化 PGN 指纹（去注释/时钟/结果空格差异后的哈希）。
- 删除语义：候选中显式 `absent` 的键 + 基线中存在 → 删除；候选中仅缺失
  （未声明）→ 保留 main 现值，不隐式删除。
- 身份冲突（同 playerNo 不同 FIDE ID / registry 矛盾）→ 隔离并出回执，不合并。
- 合并回执必须记录：新增、更新、跳过、覆盖、隔离的每个自然键及旧/新哈希；
  回执写入 job summary 并回传本机 outbox 状态（receipts 链路）。
- registry 权威校验在合并后对产物再次断言（现 `validate_registry_authority`）。

## 4. 维护者工作台（单条状态链）

废弃"私有抓取 / 发布"两条泳道，改为每 TNR 单条状态链：

```
本地抓取 → 清洗校验 → 完整性门禁 → 云端差异/冲突
→ manifest/outbox → 投递 → ingest → rebuild → deploy → online-verified
```

- 每个 TNR / run-id 展示：PGN 数、对阵覆盖率、差异数、冲突数、当前阻塞原因、
  下一步动作。
- 隔离 / 等待重试状态禁止"一键重抓"直通；必须先展示阻塞原因并要求对应处置
  （解析器更新、队列元数据补充、身份勘误等）。
- 发布中心必须列出 Chess-Results 发布包，与 FIDE/Lichess 同一回执链
  （pending → … → online-verified）。

## 5. 回归矩阵（验收必备）

格式覆盖：个人瑞士制、团队赛（逐台）、淘汰赛、无逐轮公开（standings-only
豁免）、分页名单（zeilen 截断）、缺失 PGN、错配 PGN、同轮重复台次。

合并覆盖：一致跳过、本地更新覆盖、云端并发更新（候选基线落后于 main）、
partial 候选不得覆盖 main 上的完整赛事、显式删除、隐式缺失不删除、身份冲突
隔离。

端到端：本机候选包 → 云端 ingest 三方合并 → 合并回执断言（新增/更新/跳过/
覆盖/隔离计数与哈希），作为 CI 必跑用例。

## 6. 文档与产品口径

- `docs/DEPLOY_UPDATE_GUIDE.md`、`docs/DATA_WORKFLOW.md`、
  `docs/DOMESTIC_PLAYERS_AND_EVENTS.md`、面板文案与本基线同步；任何
  "Chess-Results 不发布"的旧口径删除。
- 对外只表达"赛事数据已清洗 / 完整度 / 更新时间"；不展示 Chess-Results
  来源标识、原名或外链（P0-6 的 `docs/app.js` 改造在实施阶段完成）。

## 实施顺序

1. **P0-1/P0-4（安全隔离阶段已实施）**：发布包逐路径携带
   `baseBlobOid` / `baseSha256`；云端及 API fallback 在任何写入前验证
   baseline/current/candidate，真实并发冲突以 `RELEASE_BASE_CONFLICT` 整包隔离。
   后续若要自动解决冲突，仍须实现自然键字段级合并及逐对象回执；在此之前禁止
   用候选覆盖 current。
2. **P0-2（核心门禁已实施）**：completeness report 已区分 results、来源公开
   范围和全台覆盖；缺 playerNo 的非轮空对阵显式降为 partial/P0，奇数名单覆盖率
   不误作硬门禁；Lichess 未匹配残差否决 complete 并进入离线复核。仍需继续补齐
   面板展示和所有旧数据迁移。
3. **P0-3 / 工作台**：面板单条状态链 + Chess-Results 发布包进发布中心。
4. **P0-6**：公共对象与前台去来源化（生成层剥离 `source`/`sourceRefs`/外链，
   app.js 改口径）。
5. **P0-5**：团队赛解析器（逐台对阵），进入回归矩阵。
6. 回归矩阵全绿 + 端到端合并回执测试通过后，本文档状态改为"已实施"。

每步落地时必须同步：`AGENTS.md`（如有契约措辞变化）、相关 docs、面板文案、
`Scripts/tests/*`（含 fixture），并全量通过铁律九的测试清单。
