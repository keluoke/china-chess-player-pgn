# 国内棋手与赛事数据机制

## 身份边界

- `docs/data/registry/players.json` 是 FIDE 棋手姓名、等级分与身份的唯一权威。
- `data/manual/domestic-player-sightings.csv` 保存国内赛事中观察到的人名证据；没有 FIDE ID 也必须入库。
- `data/manual/player-identity-links.csv` 是唯一允许合并 sightings、或把国内实体连接到 FIDE ID 的机制。姓名、年龄组和俱乐部相同都不能自动合并。
- `Scripts/sync_domestic_players.py` 输出置信分、同名冲突数、跨赛事出现数、年龄段连续性与 `identity-review.json`。置信分只用于排列人工审核优先级。

前端搜索使用 FIDE 注册表与国内实体的并集。无 FIDE 结果显示 `[无FIDE]`，每条结果固定展示 FIDE ID、等级分、出生年和 title；库内同名实体达到 3 个时显示警告。

国内数据有三个不同口径，不能混称为“唯一棋手人数”：

- `sightings`：赛事名单观察数；
- `domesticPlayers`：未审核合并前的保守临时实体数；
- `uniqueNameCount`：姓名池去重数，仅用于覆盖度展示，同名者仍可能是不同人。

全量补录已整理来源时运行 `python3 Scripts/sync_chess_results_starting_rank_aliases.py`；粘贴单站 tnr 时由 `sync_chess_results_event.py` 使用 `--only-explicit`，只访问该赛事。自动 sightings 只追加、不因网络失败删除历史证据。完整证据按哈希分片，首页只加载轻量搜索索引。

`identity-name-groups.json` 按同名观察生成审核分组，`identity-candidates.json` 与 `fide-link-candidates.json` 按跨赛事、俱乐部一致、年龄连续和全库唯一性加权排序；它们都只是审核队列，禁止自动写入 `player-identity-links.csv`。同名簇达到 3 条时标记为 `parent-only`，机器不生成合并提名。

## 需求驱动的赛事整取

`Scripts/build_domestic_event_queue.py` 把既有 starting-rank 目标、大师赛五组目录、`domestic-source-catalog.csv` 和人工确认的 `data-demand-gaps.csv` 合成维护者队列。排序固定为李成智杯 > 棋协大师赛 > 省级青少年赛，并叠加查询需求热度和缺失源页快照的优先分。

本地住宅网络运行 `Scripts/local/refresh.sh event-queue`，默认整取队首 3 项赛事；也可用 `python3 Scripts/sync_chess_results_event.py --from-queue 5` 指定数量。每次抓取把 starting rank、standings 和逐轮页面压缩保存到 `data/generated/chess-results-event-snapshots/`，同时在赛事产物写入 URL、字节数和 SHA-256。

网页搜索未命中时只先写浏览器本机队列，用户明确生成并发送贡献包后，维护者运行 `python3 Scripts/import_web_contribution.py <贡献包.json>` 才会增加 `data-demand-gaps.csv` 的需求计数。隐私请求和身份线索被导入器硬性拒绝写入公开仓库，必须私下处理。

## 棋协大师赛

`data/community/master-tournament-groups.csv` 逐站、逐组登记 Chess-Results tnr。合法组别为 `OPEN`、`MEN_CANDIDATE`、`WOMEN_CANDIDATE`、`MEN_LEVEL_1`、`WOMEN_LEVEL_1`。每行同时保存轮次和晋级比例，默认比例为 `0.65`；因此 9 轮需要至少 6 分，少于 9 轮时直接按 `得分 / 实际轮次 >= 0.65` 计算，不使用固定 6 分。

`Scripts/build_domestic_progressions.py` 生成成长时间线和晋级审核队列。低级别无 FIDE 到 OPEN 有 FIDE 的路径只有在人工 identity link 已确认是同一人时才能贯通。

## 赛事持久 ID 与中文名

`data/community/tournament-name-mappings.csv` 中：

- `canonical_event_id` 是数据仓库长期不变的赛事 ID，例如 `lichengzhi-cup-2025`；
- tnr 是一个 section 的 `sourceRefs[]`，可以替换或追加；
- `chinese_name` 必须有 evidence URL，不从英文标题机器臆译。

`Scripts/build_event_catalog.py` 同时输出 section 级 `events.json`、聚合级 `canonical-events.json`，以及按日期和中国棋手数排序的 `event-name-mapping-candidates.json`。维护者优先核验候选队列，再把中文名写回社区映射表。

李成智杯的 PGN 同时保存 `naturalStage`（按出生年计算）与 `eventStage`（实际报名组）。棋手页年龄包按 `naturalStage` 聚合，赛事页按 `eventStage`/section 聚合，因此跨级参赛不会改变棋手的自然年龄归档。

## 轮次成绩与国内外覆盖

维护者拿到一个 Chess-Results 链接后，在本地住宅网络运行：

```bash
python3 Scripts/sync_chess_results_event.py 'https://chess-results.com/tnr1429695.aspx?lan=1'
```

也可以直接粘贴 `tnr1429695` 或纯数字 ID。命令会抓取起始名单、棋手中文名、最终排名和所有轮次对阵，并调用既有 PGN/棋手索引重建流程；原始抓取结果写入 `data/generated/chess-results-event-details/`。抓取必须在本地完成，GitHub Actions 只根据已提交数据离线生成赛事详情页，绝不回抓 Chess-Results。

纯国内赛事可把每轮比分、累计分和轮后名次写入 `data/manual/domestic-event-round-results.csv`。棋局仍以 PGN 为事实表，轮次成绩表用于 standings 快照；两者通过 `canonical_event_id + section_id + round + player_ref` 关联。

赛事页应明确覆盖口径：

- 国内完整赛事：显示全部分组、每轮成绩、最终排名、晋级线和完整名单；
- 国外部分收录：显示“仅中国棋手”覆盖徽标，分别展示赛事总参赛人数、中国棋手数、有 PGN 的中国棋手数，不把局部名单称作完整 standings。

## 全量与增量更新策略

| 数据层 | 增量频率 | 全量校验 | 说明 |
|---|---:|---:|---|
| FIDE 注册表与等级分 | 每月 FIDE 新榜后 | 每月全量 | 注册表是权威，不由派生层回写 |
| Chess-Results 新赛事/新轮次 | 赛期每日；平时每周 | 每季度 | 本地住宅 IP 抓取，CI 不回抓 |
| 大师赛五组 tnr 与 standings | 开赛前登记；赛期每日 | 每站赛后一次 | 赛后锁定实际轮次与最终成绩 |
| 李成智杯低龄组 sightings | 赛期每日 | 每届赛后一次 | 优先保留无 FIDE 名单与年龄组证据 |
| PGN | 新赛事发布后每日增量 | 每季度去重重建 | 以棋局哈希去重，不按姓名合并 |
| 中文赛事名 | 每周审核候选队列 | 每季度覆盖率审计 | 必须保留证据 URL |
| identity links | 有新证据即更新 | 每月低置信队列复核 | 低置信不能自动合并 |
| 静态索引、看板、canonical events | 每次数据变更后 | 每次发布 | 纯计算，可在 CI 重建 |

增量抓取保存游标（最近事件日期、已见 tnr、最近完整轮次和内容哈希），只追加新证据或更新仍在进行的赛事。全量重刷只重建 `data/generated/`，随后与上次 manifest 做数量、身份和哈希差异检查；任何人工修正继续只写 `data/manual/` 与 `data/community/`。
