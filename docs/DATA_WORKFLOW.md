# 中国棋手全数据库工作机制

## 目标定义

“中国棋手全数据库”分三层建设：

1. 身份层：所有 FIDE federation 为 `CHN` 的棋手，唯一主键为 FIDE ID。
2. 国内临时身份层：李成智杯、棋协大师赛一级组/候补组等无 FIDE ID 棋手，先以赛事名单 sighting 建临时身份。
3. 中文检索层：在 FIDE 英文名之外，持续补充中文名、拼音名和常见别名。
4. 赛事与棋局层：将 Chess-Results、国内赛事官网、李成智杯、世界/亚洲青少年赛等赛事记录和 PGN 挂到同一个 canonical player ID 下。

第一优先级是身份层完整，因为它决定同名棋手不会错合并。FIDE ID 是最强身份；没有 FIDE ID 时，不能按姓名直接合并，只能先进入国内临时身份层，后续用证据链接到 FIDE ID。

## 数据源分工

| 数据层 | 主数据源 | 更新频率 | 写入位置 |
| --- | --- | --- | --- |
| 棋手身份 | FIDE rating list legacy XML, not-rated included | 每月 | `docs/data/registry/` |
| 国内临时身份 | 李成智杯、棋协大师赛、国内青少年赛事名单 | 按赛事 | `data/manual/domestic-player-sightings.csv` |
| 身份链接证据 | 后续 FIDE ID、同赛事跨年名单、出生年/省队/证书号 | 随审核 | `data/manual/player-identity-links.csv` |
| 中文名/拼音/别名 | 人工审核 CSV、既有种子、赛事名单 | 随 PR 更新 | `data/manual/player-aliases.csv` |
| 赛事索引 | Chess-Results、国内赛事官网、亚洲/世界青少年赛事页 | 每周或按赛事 | `docs/data/index/` |
| PGN | Chess-Results Game Database、赛事官网 PGN、手工导入 | 每周或按赛事 | `docs/data/pgn/` |
| 百万级 bulk PGN | Lichess official broadcast archive | 每月 | `docs/data/bulk/` |
| 按棋手 PGN 派生层 | 已入库赛事 PGN 和 bulk 青少年 PGN | 每次 PGN 更新后 | `docs/data/index/by-player/`, `docs/data/pgn/by-player/` |

FIDE 月度榜单用 legacy XML 版本，因为它包含未定级棋手；普通 rating list 只适合排行榜，不适合作为完整身份库。

## 无 FIDE ID 棋手处理

李成智杯、棋协大师赛一级组/候补组、低年龄组经常出现三种情况：

- 当年没有 FIDE ID。
- 名单只有中文名、省队/棋校和组别。
- 低年龄组无 FIDE ID，几年后升组或出国参赛后获得 FIDE ID。

这部分不能放进 FIDE registry，也不能只靠姓名合并。采用两阶段模型：

1. `sighting`：某个赛事名单里的某一行，保留原始证据。
2. `domestic player`：由一个或多个 sighting 组成的国内临时身份。
3. `identity link`：人工审核后，把 sighting/domestic player 链接到 FIDE ID。

手工录入位置：

```text
data/manual/domestic-player-sightings.csv
data/manual/player-identity-links.csv
```

生成的静态输出：

```text
docs/data/registry/domestic/manifest.json
docs/data/registry/domestic/players.json
docs/data/registry/domestic/sightings.json
docs/data/registry/domestic/identity-links.json
```

生成命令：

```bash
python3 Scripts/sync_domestic_players.py
```

ID 规则：

- FIDE 棋手：`fide-<FIDE_ID>`。
- 国内临时棋手：`domestic-<hash>`。
- 赛事出现记录：`sighting-<hash>`。

默认不跨赛事自动合并，只保留单条 sighting 生成的 `domestic-*`。即使姓名、出生年、省队/棋校相同，也先保守拆开；只有出现出生年、省队/棋校、证书号、后续 FIDE ID 等强证据并经过人工审核时，才通过 `player-identity-links.csv` 合并。

`player-identity-links.csv` 示例语义：

```text
from_type=sighting, from_id=sighting-xxx, to_type=fide, to_id=8657238
from_type=sighting, from_id=sighting-aaa, to_type=domestic, to_id=domestic-bbb
from_type=domestic, from_id=domestic-bbb, to_type=fide, to_id=8657238
```

这样低年龄组历史成绩可以先挂在 `domestic-*` 下；一旦确认后来的 FIDE ID，所有历史成绩通过 identity link 归并到 `fide-*`，不需要改历史原始记录。

## 本地原始 PGN 侦察兵

所有来源不明、体量较大、授权需要复核或质量未确认的 PGN，先进入本地侦察兵资产库：

```bash
python3 Scripts/pgn_scout.py init
```

默认路径：

```text
~/Library/Application Support/ChinaChessPlayerPGN/RawPGNScout/
```

侦察兵负责：

- Chess-Results TournamentID/FIDE ID 撞库。
- Lichess broadcast 和开放数据库 PGN/ZST 下载。
- Chess.com Public API 月度 PGN 和国家用户列表侦察。
- TWIC issue ZIP 下载、解压和中国相关棋局过滤。
- ChessBase Mega Database 导出的本地 PGN 导入。
- 协会官网、Wayback、国内直播 H5 抓包后的 PGN/ZIP 导入。

侦察兵不直接发布数据到 GitHub Pages。只有明确可公开分发、能挂到 FIDE ID 和赛事 ID、且通过 PGN header 校验的文件，才进入 `docs/data/pgn/`。

## 仓库存储

棋手身份库不拆成每人一个文件，避免几千个小 JSON 文件难以维护；采用总表加分片：

```text
docs/data/registry/manifest.json
docs/data/registry/players.json
docs/data/registry/shards/fide-prefix-860.json
docs/data/registry/shards/fide-prefix-861.json
```

`players.json` 是搜索和列表用的轻量表；`shards/` 是保留更多字段的分片明细。网页端首屏会尝试加载 `players.json`，如果文件不存在则自动退回现有青少年榜单和赛事索引。

赛事和 PGN 继续使用现有结构：

```text
docs/data/index/players/fide-<fideID>.json
docs/data/pgn/<source>/tnr<tournamentID>/fide-<fideID>-<tournamentID>.pgn
```

前端优先读取按棋手聚合的派生层：

```text
docs/data/index/by-player/manifest.json
docs/data/index/by-player/players.json
docs/data/index/by-player/fide-<fideID>.json
docs/data/pgn/by-player/fide-<fideID>/all.pgn
docs/data/pgn/by-player/fide-<fideID>/U8.pgn
docs/data/pgn/by-player/fide-<fideID>/U10.pgn
```

这层由 `Scripts/build_static_player_pgn.py` 从已确认可公开分发的 PGN 派生，不直接联网。macOS 版和网页版搜索棋手后都先读 `by-player`；只有缺失时才回退到赛事 PGN 或 bulk 青少年包。

## 更新流程

月度 FIDE 棋手库更新：

```bash
python3 Scripts/sync_chinese_players.py
```

只做小样本验证：

```bash
python3 Scripts/sync_chinese_players.py --max-players 100
```

从已经下载的官方包导入：

```bash
python3 Scripts/sync_chinese_players.py --input ~/Downloads/players_list_xml_legacy.zip
```

更新 PGN 静态归档：

```bash
python3 Scripts/sync_static_pgn.py --from-local-cache
python3 Scripts/sync_static_pgn.py --fetch-missing --max-downloads 50
python3 Scripts/promote_public_pgn.py --scan-chess-results --max-players 25
python3 Scripts/promote_public_pgn.py --promote-scout --source lichess
python3 Scripts/sync_lichess_broadcast_bulk.py --metadata-only --mirror --index-youth
python3 Scripts/build_static_player_pgn.py
```

GitHub 页面上有两个可手动运行的 workflow：

- `Update Chinese player registry`：刷新 FIDE CHN 棋手身份库。
- `Update domestic player registry`：根据手工赛事名单和身份链接刷新国内临时身份层。
- `Update static PGN archive`：刷新已登记赛事的 PGN。
- `Promote public PGN`：按 FIDE ID 扫 Chess-Results 全局 PGN 搜索，并把新增合格 PGN 晋升到静态归档。
- `Update Lichess broadcast bulk archive`：刷新百万级 Lichess broadcast 压缩分片，并重建 U8-U18 青少年 PGN 包。

涉及 PGN 的 workflow 会在提交前运行 `Scripts/build_static_player_pgn.py`，把赛事 PGN 和 bulk 青少年 PGN 重新聚合成 `by-player` 查询层。

## 百万级 bulk 与青少年筛选

百万级数据不拆成每盘一个文件，采用压缩分片：

```text
docs/data/bulk/manifest.json
docs/data/bulk/lichess-broadcast/shards/lichess_db_broadcast_YYYY-MM.pgn.zst
docs/data/bulk/youth/manifest.json
docs/data/bulk/youth/pgn/U8/lichess-broadcast-youth.pgn
```

Lichess broadcast archive 当前包含 77 个官方直播 PGN 分片、1,109,301 盘。原始 `.pgn.zst` 用作百万级静态资产；网页端首屏只加载 manifest，不展开百万盘棋。青少年筛选由脚本流式扫描分片生成：用赛事年份减棋手出生年份，按李成智杯 U8/U10/U12/U14/U16/U18 年龄段归类。这样每个年龄段都有一个可直接下载的 PGN 包。

`by-player` 派生层再把青少年分段包按 FIDE ID 聚合，生成每名棋手的 `all.pgn` 和 U8-U18 阶段包。这样 UI 查询棋手时不需要扫描百万级分片，也不需要在浏览器里临时从整段年龄组 PGN 里抽取。

Lichess broadcast 数据按 CC BY-SA 4.0 发布，仓库保留 `docs/data/bulk/NOTICE.md` 作为授权说明。TWIC、Chess.com 和国内官网仍按 `data/manual/public-pgn-sources.csv` 审核后再晋升。

## 中文名补全流程

中文名不能只靠拼音自动猜。标准流程是：

1. 在 `data/manual/player-aliases.csv` 添加或修改一行。
2. 必须填写 `fide_id`，中文名、拼音和别名都只是这个 ID 的 alias。
3. `confidence` 使用 `reviewed`、`needs-review` 或 `derived`。
4. 不确定时保留空中文名，只填拼音和英文别名，避免错配。
5. 合并后重新运行 `python3 Scripts/sync_chinese_players.py --input <FIDE包>`。

同名处理原则：

- 同中文名不同 FIDE ID 允许共存。
- 搜索结果必须显示 FIDE ID、英文名、出生年和等级分供用户选择。
- 任何脚本不得按中文名或拼音自动合并棋手。

## 质量检查

每次数据更新至少检查：

```bash
python3 -m py_compile Scripts/sync_chinese_players.py Scripts/sync_static_pgn.py
python3 -m json.tool docs/data/registry/manifest.json >/tmp/registry-manifest.json
python3 -m json.tool docs/data/index/manifest.json >/tmp/pgn-manifest.json
swift build
git diff --check
```

PGN 文件必须能解析出 `[Event "..."]` header；源站返回的 HTML、空文件和错误页不得进入 `docs/data/pgn/`。

## 里程碑

1. M1：FIDE CHN 全量身份库可月度同步，网页搜索能覆盖全量 FIDE ID 和英文名。
2. M2：人工中文别名覆盖国内顶尖棋手、青少年榜单棋手、李成智杯前三名。
3. M3：赛事索引覆盖近十年中国甲级联赛、全国冠军赛、棋协大师赛、李成智杯、亚洲/世界青少年赛。
4. M4：PGN 归档覆盖所有可公开下载赛事，并能按棋手、年龄段、赛事类型导出。
5. M5：每次数据更新由 GitHub Actions 生成可审查 diff，主分支始终可被 GitHub Pages 直接读取。
