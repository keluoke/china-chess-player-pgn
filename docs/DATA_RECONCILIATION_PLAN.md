# 数据源比对与补库方案

目标不是让网页用户临时点击按钮去抓数据，而是建立一条可审查、可重复、可回滚的数据生产线：先侦察数据源，生成仓库缺口报告；确认来源和质量后，再把合格 PGN 晋升到 `docs/data/pgn/`，最后重建棋手和年龄段查询索引。

## 产品原则

1. 网页端只读取仓库静态数据，不直接抓取第三方站点。
2. 所有补库动作先生成报告和 diff，人工确认后再合并。
3. 公开网页只发布确认可公开分发的 PGN。
4. 授权不清、商业库、私有抓包数据只保存在本地侦察资产库，不进入 GitHub。
5. 同一盘棋用标准化 header hash 和走法 hash 去重，保留来源证据链。

## 数据源分层

| 层级 | 来源 | 用途 | 默认处理 |
| --- | --- | --- | --- |
| A | Chess-Results | FIDE 赛事、国内公开赛事、TournamentID PGN | 自动侦察，合格后可晋升 |
| A | Lichess Broadcasts | 公开直播 PGN、青少年 bulk 年龄段索引 | 自动侦察，按授权说明发布 |
| B | 中国国际象棋协会官网、地方棋院官网 | 棋协大师赛、全国赛、李成智杯公告和附件 | 先登记来源，人工确认后晋升 |
| B | 国内赛事直播 H5 导出的 PGN | 李成智杯和国内大师赛高台直播 | 先本地入侦察库，确认授权后晋升 |
| C | TWIC、Chess.com | 补充国际公开赛事和线上公开赛事 | 先本地侦察，确认授权后选择性晋升 |
| D | ChessBase Mega Database、棋友私有资料 | 历史研究和身份核对 | 不公开发布，只做本地辅助证据 |

## 建议新增的报告层

新增一个只读报告目录：

```text
docs/data/audit/
  manifest.json
  source-coverage.json
  missing-pgn-events.json
  candidate-pgn-packages.json
  player-coverage.json
```

报告含义：

- `source-coverage.json`：按来源统计已侦察赛事数、已入库赛事数、待确认赛事数、失败赛事数。
- `missing-pgn-events.json`：网页上显示“未入库 PGN”的赛事清单，包含 FIDE ID、赛事名、日期、来源、TournamentID、年龄段。
- `candidate-pgn-packages.json`：本地侦察兵找到但还没晋升的候选 PGN 包，附带来源、棋局数、去重结果、质量状态。
- `player-coverage.json`：按棋手统计已缓存棋局、缺口赛事、U8/U10/U12/U14/U16/U18 阶段覆盖情况。

网页后续可以显示这个报告，但不提供直接抓取按钮。用户看到的是“缺口在哪里、证据是什么、下一批准备入库什么”。

## 工作流设计

### 1. 侦察

定期或手动运行本地侦察兵，不直接改 `docs/data/pgn/`：

```bash
python3 Scripts/pgn_scout.py seed-chess-results-targets
python3 Scripts/pgn_scout.py fetch-chess-results --max-requests 200
python3 Scripts/pgn_scout.py report --write reports/pgn-scout-report.md
```

国内来源先进入来源目录：

```text
data/manual/domestic-source-catalog.csv
```

建议字段：

```text
source_id,event_name,event_year,event_type,age_group,official_url,attachment_url,live_url,status,redistributable,evidence_note
```

### 2. 比对

新增脚本建议名：

```bash
python3 Scripts/reconcile_pgn_sources.py --write-audit
```

它做四件事：

1. 读取 `docs/data/index/events.json`、`docs/data/index/by-player/` 和 `docs/data/pgn/`。
2. 读取本地侦察兵 SQLite、Chess-Results 目标表、国内来源目录。
3. 对比每个赛事是否已有 PGN、是否只有成绩无棋谱、是否有候选 PGN。
4. 输出 `docs/data/audit/*.json` 和 `reports/pgn-reconciliation-YYYY-MM-DD.md`。

### 3. 审核

审核标准：

- 能解析出合法 `[Event "..."]` header。
- 至少能绑定到明确赛事和明确棋手身份。
- 没有 HTML 错误页、重复内容、乱码 header。
- 来源在 `data/manual/public-pgn-sources.csv` 中允许公开分发。
- 国内青少年棋手无 FIDE ID 时，先进入 domestic provisional ID，再通过证据链链接到 FIDE ID。

### 4. 晋升

只有审核通过后才执行：

```bash
python3 Scripts/promote_public_pgn.py --promote-scout --source <approved-source>
python3 Scripts/sync_static_pgn.py --from-local-cache
python3 Scripts/build_static_player_pgn.py
python3 Scripts/reconcile_pgn_sources.py --write-audit
```

每次 PR 必须包含：

- 新增或变化的 `docs/data/pgn/`
- 更新后的 `docs/data/index/`
- 更新后的 `docs/data/index/by-player/`
- 更新后的 `docs/data/audit/`
- 一份 `reports/pgn-reconciliation-YYYY-MM-DD.md`

## 国内赛事补库重点

优先建来源目录，不急着抓 PGN：

1. 李成智杯：按年份、组别、性别、年龄段登记成绩公告、直播链接、附件链接。
2. 中国棋协大师赛：按一级组、候补组、地方站、年份登记赛事页面和 PGN 附件。
3. 全国国际象棋锦标赛、甲级联赛、全国团体赛：优先查 Chess-Results TournamentID 和协会官网附件。
4. 世界/亚洲青少年赛：优先查 Chess-Results、FIDE/主办方官网、Lichess Broadcast。

## 前端呈现建议

当前先去掉“请求入库”和“运行更新”按钮。下一步前端只显示三类状态：

- `PGN 已缓存`：可下载、可在线播放。
- `待数据源比对`：已有赛事线索，但仓库未确认 PGN。
- `待补源`：只有赛事记录，没有明确 PGN 来源。

后续可增加一个“数据覆盖报告”页面，展示：

- 数据源覆盖率
- 本周新增候选 PGN
- 待审核 PGN 包
- 各年龄段缺口
- 各棋手缺口赛事

这个页面只读，不触发抓取。真正更新仍走本地脚本或 GitHub Actions 生成 PR。

## 近期实施顺序

1. 移除网页上的直接补库按钮。
2. 新增 `Scripts/reconcile_pgn_sources.py`，先只生成报告，不写 PGN。
3. 新增 `data/manual/domestic-source-catalog.csv`，先登记李成智杯和棋协大师赛来源。
4. 生成第一版 `docs/data/audit/*.json`，让网页能显示仓库覆盖率和缺口。
5. 选 3-5 个赛事做端到端试运行：侦察、比对、审核、晋升、重建索引。
6. 稳定后再把侦察和比对接入 GitHub Actions，但仍要求通过 PR 查看 diff 后合并。
