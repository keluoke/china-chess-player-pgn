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

网络采集不再直接补录 sightings 或别名。维护者可运行
`Scripts/local/refresh.sh candidates` 在仓库外生成候选，人工核对后再分别写入
`name-corrections.csv`、`player-aliases.csv` 或身份链接机制。网络失败不会删除任何
已审核人工数据。

仓库外 `identity-workbench/` 中的 `identity-name-groups.json` 按同名观察生成审核分组，`identity-candidates.json`、`fide-link-candidates.json` 与 `chinese-name-candidates.json` 按同赛事、跨赛事、俱乐部一致、晋级连续性和全库唯一性加权排序。高置信候选可形成前端展示聚合，但禁止自动写入 `player-identity-links.csv`；用户可提交证据或异议。同名簇达到 3 条时继续生成经过硬冲突剪枝的两两候选，不再以 `parent-only` 为由完全停止建议。维护者流程见 `MAINTAINER_IDENTITY_REVIEW_GUIDE.md`。

FIDE 候选同时比较国内观察的中文名、拼音、拉丁显示名和别名；注册表只有拉丁名
时，不得因国内记录优先使用中文名而漏掉候选。同名簇达到 3 条以上也不得整体
跳过，而应逐条检查唯一 FIDE 命中、性别、出生年、同赛事和参赛单位证据。
姓名或拼音唯一匹配本身只进入审核队列，不自动确认。人工确认写入
`player-identity-links.csv` 后，前端把国内赛事历史投影到既有 FIDE 卡片，不再
生成第二张同 FIDE ID 卡片，也不把国内姓名或赛事字段写回 registry。三条以上
同名记录只有各自具备同赛事 FIDE 或特色参赛单位等成员级证据时才能自动展示
归组，禁止用全局同名证据把整簇吸附到同一个 FIDE ID。

## 需求驱动的赛事整取

`Scripts/build_domestic_event_queue.py` 把既有 starting-rank 目标、大师赛五组目录、`domestic-source-catalog.csv` 和人工确认的 `data-demand-gaps.csv` 合成维护者队列。排序固定为李成智杯 > 棋协大师赛 > 省级青少年赛，并叠加查询需求热度和缺失源页快照的优先分。

本地住宅网络运行 `Scripts/local/refresh.sh event-queue`，默认整取队首 3 项
赛事，单次上限 10 项。starting rank、standings、逐轮页面及解析结果全部
写入维护者应用数据目录下的私有运行区（0700/0600），仅在私有元数据中保留
URL、字节数和 SHA-256；不写入 `data/generated/`。成功事件逐个检查点落盘，
30 天内已采集目标默认跳过。

网页未命中只能生成目标线索 Issue（URL、tnr/FIDE ID、原因和优先级），
不能包含 HTML、PGN、解析行、Cookie 或响应头。隐私请求必须通过私密渠道处理。

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
Scripts/local/refresh.sh event-queue -- 1429695
```

也可以传入 `tnr1429695` 或完整 URL。命令仅在私有运行区保存起始名单、
最终排名和逐轮对阵，不调用 PGN、棋手索引或公开赛事详情重建流程。
GitHub Actions 不读取该私有运行区，也不访问 Chess-Results。

纯国内赛事可把每轮比分、累计分和轮后名次写入 `data/manual/domestic-event-round-results.csv`。棋局仍以 PGN 为事实表，轮次成绩表用于 standings 快照；两者通过 `canonical_event_id + section_id + round + player_ref` 关联。

赛事页应明确覆盖口径：

- 国内完整赛事：显示全部分组、每轮成绩、最终排名、晋级线和完整名单；
- 国外部分收录：显示“仅中国棋手”覆盖徽标，分别展示赛事总参赛人数、中国棋手数、有 PGN 的中国棋手数，不把局部名单称作完整 standings。

## 全量与增量更新策略

| 数据层 | 增量频率 | 全量校验 | 说明 |
|---|---:|---:|---|
| FIDE 注册表与等级分 | 每月 FIDE 新榜后 | 每月全量 | 注册表是权威，不由派生层回写 |
| Chess-Results 目标采集 | 赛期按需 | 通过完整性门禁后发布 | raw 仅维护者本地；30 天检查点；清洗数据经比对合并进仓库 |
| 大师赛五组 tnr 目标 | 开赛前人工登记 | 每站赛后复核 | 公开层只保留人工元数据和来源链接 |
| 国内棋手 sightings | 有已审核证据时 | 每届赛后人工复核 | 机器候选不自动写人工层 |
| PGN | Lichess 新分片发布后 | 每月去重重建 | Lichess 保留 CC BY-SA 4.0 署名；Chess-Results 全赛事 PGN 经门禁后归档发布 |
| 中文赛事名 | 每周审核候选队列 | 每季度覆盖率审计 | 必须保留证据 URL |
| identity links | 有新证据即更新 | 每月低置信队列复核 | 低置信不能自动合并 |
| 静态索引、看板、canonical events | 每次数据变更后 | 每次发布 | 纯计算，可在 CI 重建 |

Chess-Results 增量状态保存在仓库外的 `capture-state.json`，只记录最近成功时间和数量统计。
FIDE/Lichess 公开发布必须经过 staging、数量/结构检查和精确 manifest；任何人工
修正继续只写 `data/manual/` 与 `data/community/`。
