# 棋谱质量与公开指标契约

## 质量与覆盖

`Scripts/game_quality.py` 是所有出口的主线质量判定入口。事实层保存版本、解析器版本、合法步数、是否可复盘、是否结束、结果状态与统计资格。

- 原始赛事归档、原始 Result 和原档哈希保留。归档完整不代表主线可复盘。
- 默认棋手/赛事下载包只含合法且非空主线；未结束棋局可复盘，但不参与胜负统计。
- `playableComplete` 必须以合法棋谱实际覆盖所有应有对局；广播范围未核或配对身份未解时不得成立。
- `replayCoverage` 是主站、赛事页和专题共同消费的版本化状态：full / live / partial / missing / none / unknown。live 只指公开直播范围，不表示全台。
- 比分争议不禁止复盘。`resultStatus=disputed` 必须配 `resultStatsEligible=false`；PGN 附加 ResultStatus、ResultIssueID、ResultStatsEligible 标签，原始 Result 不改。

## 指标字典与兼容性

API v1 旧字段保留含义，新增字段用于消除歧义：

| 字段 | 含义 |
| --- | --- |
| 公共 totals.games / playerGameLinks | 棋手与归档棋局的关联次数；同一局两名棋手可计两次 |
| archivedUniqueGames | 事实层按指纹去重的归档记录数，包含非法/空主线及未进入公共赛事目录的记录 |
| legalArchivedUniqueGames | 归档事实中的合法非空主线数 |
| playableUniqueGames | 公共赛事目录实际可下载、可复盘的独立棋局数，已排除隔离赛事/测试记录；首页使用此值 |
| 棋手 gameCount / archivedGameCount | 该棋手归档记录数，保留旧口径 |
| playableGameCount / excludedGameCount | 该棋手合法非空主线数 / 被默认包排除记录数 |
| packages[].gameCount | 该下载包实际包含局数，必须与正文一致 |
| participationEventCount | 结构化赛事参赛事实中的不同赛事数 |
| pgnEventCount | 棋手 PGN 归档按原有 event_summaries 规则分组的赛事数，包含待修复记录 |
| API v1 eventCount | 保留原有结构化参赛赛事数 |
| bootstrap eventCount | 保留原有 PGN 分组赛事数；新客户端应使用明确命名字段 |

公开计数不能把局数、棋手关联数、赛事分组数混用。零值有意义，不得当作缺失值。

## 人工核定

离线构建输出 `data/generated/game-result-review.json`。自然键为赛事、轮次、台次、双方 playerNo，生成稳定 issueID；bindingSha256 绑定当期 PGN 哈希、结果和方向。白黑交换先统一方向，姓名匹配不唯一则不自动断言冲突。

人工裁决写 `data/community/game-result-decisions.csv`，仅接受 accept-table / accept-pgn。必须填写 issue_id、binding_sha256、decision、evidence_path、evidence_sha256、reviewer、reviewed_at。证据置于 `data/manual/result-evidence/`，使用可审阅的脱敏记录；不要提交原始 HTML。证据缺失、正文哈希不符、绑定过期、重复或未匹配裁决都会中止构建。

没有足够证据时保持 pending。构建器不抓来源，不从多数票或引擎棋力推断比分。核定后 `effectiveResult` / ReviewedResult 表达有效比分，历史原文仍可追溯。未来胜负统计只能使用 `resultStatsEligible` 为 true 的棋局并优先读取 effectiveResult。

## 上线验证

`python3 Scripts/verify_product_canary.py --expect-snapshot <snapshot-id> --output /tmp/product-canary.json`

使用正常生产 URL 检查快照、四名棋手指标、居文君默认包和同源回退的正文哈希/MIME/合法性、两个历史问题赛事的目录包、错误版本请求 409、新旧域名 R2 GET/HEAD CORS。`--cors-only` 只做跨域验证。HTTP 验证不替代浏览器交互测试。
