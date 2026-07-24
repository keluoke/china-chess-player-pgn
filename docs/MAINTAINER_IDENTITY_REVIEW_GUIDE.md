# 棋手身份与中文名维护者审核指南

> 适用范围：A 国内身份连接 FIDE、B 中文名候选、C 参赛事实与 PGN 覆盖、D 用户证据与统一审核队列。
> 核心原则：机器可以给出高置信建议并在前端默认聚合，但不能替维护者修改 registry；用户提交的是证据或异议，不是数据写权限。

## 1. 四条链的职责

### A. 国内赛事身份 → FIDE 档案

`sync_domestic_players.py` 对同名国内实体和唯一 FIDE 档案计算候选，并叠加：

- 同一赛事名单中同一记录已解析到该 FIDE ID；
- FIDE 棋手赛事足迹中的姓名一致；
- 具有区分度的俱乐部一致；
- 达到 65% 晋级线且存在升级轨迹；
- 时间、性别、出生年不存在硬冲突。

满足同赛事、唯一来源姓名或特色俱乐部任一强证据时，候选可进入前端展示聚合。此时 FIDE 卡片成为主卡，国内记录隐藏为子记录；原始 domestic ID、赛事观察和审核状态均不改变。用户可以提交“补充身份证据”或“对此身份有异议”。

接受候选后才写 `data/manual/player-identity-links.csv`：

```csv
from_type,from_id,to_type,to_id,confidence,evidence,source_url,reviewed_by,reviewed_at,notes
domestic,domestic-3718d907d01f,fide,8640491,reviewed-high,same-event-roster+distinctive-club,,maintainer,2026-07-19,金鸿涛：苏州站公开组与 FIDE 赛事足迹一致
```

不要直接修改 `docs/data/registry/players.json` 或任何 `data/generated/` 文件。

### B. FIDE 档案 → 中文名

中文名候选来自已抓取赛事中的专用姓名字段，先经过 `sanitize_person_name`。规则如下：

- registry 已有中文名：不生成普通补名候选；如发现错误，走 `name-corrections.csv`；
- 同一 FIDE ID 在至少 2 个赛事中出现同一中文名且没有冲突：高置信，搜索、
  排行榜、赛事名单和棋手详情默认显示，并标注“中文名高置信暂定”；
- 只出现 1 个赛事：中置信，不参与搜索、排行榜或名单的默认名称，仅在棋手详情
  中显示“可能中文名（单场观测，待核验）”；
- 出现多个不同中文名：冲突队列最高优先，不进入任何公开展示投影。

前端统一通过 `docs/presentation-names.js` 解析姓名，优先级固定为
`registry.chineseName → 高置信展示候选 → registry 英文名`。中置信只提供详情
提示；删除展示候选后自动回退英文名。展示候选不会写入或覆盖 registry 的
`chineseName`。审核接受后，把中文名、拼音和别名写入
`data/manual/player-aliases.csv`；确认是历史错标时写
`data/community/name-corrections.csv`。

```csv
fide_id,chinese_name,pinyin_name,aliases,source,confidence,notes
8608369,戴文智,dai wenzhi,"戴文智|Dai Wenzhi|Dai, Wenzhi|Wenzhi Dai",reviewed-event-evidence,reviewed,多个赛事的同一 FIDE ID 重复出现中文名
```

### C. 参赛事实与 PGN 覆盖分离

棋手“参加过赛事”不再由是否有 PGN 决定。`build_player_participation.py` 从赛事赛果事实生成按 FIDE ID 分桶的参赛历史：

- `resultStatus=recorded`：已有参赛/赛果事实；
- `resultStatus=scheduled`：未来赛事的报名或名单记录，不能称为最终赛果；
- `pgnStatus=available`：已有可播放棋谱；
- `pgnStatus=not-archived`：赛事记录存在，但本库没有该棋手 PGN。

前端把参赛历史、PGN 档案和 A 链聚合的国内足迹去重合并。因此“暂无棋谱”不能再被展示成“没有参加过赛事”。缺 PGN 只有在赛事明确公开过 PGN、维护者已有文件，或用户给出具体线索时，才进入本地事件队列；禁止为补身份无目的重抓赛事。

### D. 统一审核队列与贡献闭环

完整候选证据只写到仓库外：

```text
~/Library/Application Support/ChinaChessPlayerPGN/identity-workbench/
  review-queue.json
  identity-candidates.json
  fide-link-candidates.json
  chinese-name-candidates.json
  identity-conflict-edges.json
  identity-review.json
```

公开仓库只保存数量摘要和展示所需的最小投影，不公开未成年人的原始俱乐部或完整候选足迹。

统一队列按以下顺序排列：

1. 现有 727 个高优先级 domestic 身份候选，尚不能默认聚合的先审；
2. 已默认展示聚合、等待维护者落表的高优先级 domestic 候选；
3. 国内身份与 FIDE 的同赛事直接证据；
4. 国内身份与 FIDE 的特色俱乐部一致；同级内优先等级分高、赛事足迹多的高价值棋手；
5. 中文名冲突；
6. 高置信中文名；
7. 中低置信长尾。

用户贡献页只收证据、目标链接和异议。普通证据可形成待审核 Issue；身份异议与隐私请求留在用户本机并通过私密渠道交给维护者。只有明确需要补抓具体赛事时，维护者才运行 `refresh.sh event-queue -- <tnr>`。

## 2. 每轮审核的标准流程

### 2.1 离线重建候选

```bash
bash Scripts/local/refresh.sh reindex
python3 Scripts/local/identity_review.py --limit 30
```

`reindex` 不访问来源、不提交、不推送。它会重建赛事明细、身份候选、参赛历史和审核摘要。

按类型看队列或展开一张完整证据卡：

```bash
python3 Scripts/local/identity_review.py --type domestic-fide-link --limit 30
python3 Scripts/local/identity_review.py --type chinese-name --limit 30
python3 Scripts/local/identity_review.py --type domestic-domestic-link --limit 30
python3 Scripts/local/identity_review.py --show fide-link-domestic-3718d907d01f-8640491
```

### 2.2 审核 A：domestic → FIDE

按以下顺序判断：

1. 核对同一赛事、同一 `playerNo` 是否解析到候选 FIDE；
2. 核对特色俱乐部是否一致，忽略“中国、江苏省、A2”等低区分度值；
3. 核对晋级轨迹是否是低一级组达到 65% 后进入高一级组；
4. 排除同一赛事占两个席位、性别冲突、出生年冲突和时间重叠；
5. 接受后追加一条 `domestic → fide` link；不确定则保留展示候选，不落表；明确错误则导入异议 tombstone。

金鸿涛案例应被判为高置信：`domestic-3718d907d01f` 与 FIDE `8640491` 同名，苏州站公开组记录已在公开赛事明细中解析到该 FIDE 档案，且南京博智弈俱乐部足迹一致。即使尚未写人工 link，前端也应默认显示在 FIDE 主卡下。

### 2.3 审核 B：中文名

1. 确认 `candidateNames` 只有一个值；
2. 查看 `eventIDs` 是否为真实赛事而不是只有 `name-map` 占位证据；
3. 至少两个赛事重复出现，可接受为 `player-aliases.csv`；
4. 同名存在多个 FIDE ID 时，必须结合赛事、出生年或官方资料核对；
5. 多个中文名冲突时不得凭拼音猜测。

戴文智案例：FIDE `8608369` 当前 registry 没有中文名，但多个赛事记录重复出现“戴文智”，应先以前端暂定中文名聚合展示；维护者审核后写入 `player-aliases.csv`，而不是手改 registry JSON。

### 2.4 审核 domestic ↔ domestic

俱乐部一致、晋级轨迹连续、时间不冲突时置信度已经很高，可默认展示为同一身份。永久链接仍需把被并入实体的每个 sighting 指向选定 canonical domestic ID：

```csv
sighting,sighting-被并入记录,domestic,domestic-选定主实体,reviewed-high,club+promotion-continuity,,maintainer,2026-07-19,
```

如果证据只达到高分、但赛事日期只有年份而无法证明先后，则优先审核但不默认聚合。明确不是同一人时不要“降分后放回队列”，而要写负链接，防止以后重新成组。

### 2.5 处理身份异议

收到贡献页下载的 JSON 后先人工查看，再导入：

```bash
python3 Scripts/local/import_identity_dispute.py /path/to/identity-dispute.json
bash Scripts/local/refresh.sh reindex
```

导入器同时支持 `domestic-* ↔ domestic-*` 和 `fide-* ↔ domestic-*`。导入后应确认对应展示组已经拆分。若异议最终不成立，维护者需显式修改 `data/manual/presentation-disputes.csv` 的状态并留下审核人、日期和说明；不要直接删除历史行。

## 3. 何时补抓赛事

只有以下情况才进入维护者本地事件队列：

- 候选卡指向一个明确 tnr，但当前缺少用于裁决的名单、排名或轮次；
- 页面明确提供部分台次或全台 PGN，而本地 completeness report 显示抓取/归档失败；
- 用户提供具体赛事或 PGN 文件线索；
- 已有数据互相冲突，需要回看同一赛事的原始私有证据。

```bash
bash Scripts/local/refresh.sh event-queue -- <tnr>
```

如果赛事本来就没有公开 PGN，`pgnStatus=not-archived` 是正常状态，不创建无意义补抓任务。盐城站棋协大师赛、盐城快棋赛等已知全台 PGN 赛事若出现缺口，应作为高优先级具体补件处理。

## 4. 发布前检查

```bash
python3 -m unittest Scripts.tests.test_identity_evidence_chains
python3 -m unittest Scripts.tests.test_identity_dispute_import
python3 -m unittest Scripts.tests.test_completeness_and_identity
python3 Scripts/validate_registry_authority.py
python3 Scripts/validate_public_privacy.py
python3 Scripts/validate_snapshot_consistency.py
git diff --check
```

检查结果必须同时满足：

- registry 权威字段未被派生候选覆盖；
- 前端仅出现 `suggestedChineseName`，没有把暂定名伪装成权威 `chineseName`；
- 公开身份投影不含俱乐部、学校、来源 URL 或完整证据链；
- 参赛数不再等同于 PGN 赛事数；
- 异议 tombstone 能在下一次投影中拆组；
- 所有公开派生清单属于同一 snapshot。

## 5. 当前基线（2026-07-19 离线测算）

- domestic ↔ domestic 候选：18,602，原有高优先级 727；
- domestic → FIDE 候选：1,497；引入同赛事明细后，高置信展示候选 1,465；
- registry 缺中文名但存在中文赛事证据的 FIDE 棋手：1,345；其中重复一致高置信 684，冲突 24；
- 当前可形成的展示聚合组：1,366，覆盖 2,337 个 FIDE/domestic 实体。

这些数字是候选/展示口径，不是已人工确认永久合并数。每次数据刷新后以仓库外 `review-queue.json` 和 `identity-workbench-summary.json` 为准。
