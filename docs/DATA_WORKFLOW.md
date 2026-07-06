# 中国棋手全数据库工作机制

## 目标定义

"中国棋手全数据库"分三层建设：

1. 身份层：所有 FIDE federation 为 `CHN` 的棋手，唯一主键为 FIDE ID。
2. 国内临时身份层：李成智杯、棋协大师赛一级组/候补组等无 FIDE ID 棋手，先以赛事名单 sighting 建临时身份。
3. 中文检索层：在 FIDE 英文名之外，持续补充中文名、拼音名和常见别名。
4. 赛事与棋局层：将 Chess-Results、国内赛事官网、李成智杯、世界/亚洲青少年赛等赛事记录和 PGN 挂到同一个 canonical player ID 下。

## 数据源分工

| 数据层 | 主数据源 | 更新频率 | 写入位置 |
| --- | --- | --- | --- |
| 棋手身份 | FIDE rating list legacy XML | 每月 | `docs/data/registry/` |
| 国内临时身份 | 李成智杯、棋协大师赛、国内青少年赛事名单 | 按赛事 | `data/manual/domestic-player-sightings.csv` |
| 身份链接证据 | 后续 FIDE ID、同赛事跨年名单、出生年/省队/证书号 | 随审核 | `data/manual/player-identity-links.csv` |
| 中文名/拼音/别名 | 人工审核 CSV、既有种子、赛事名单 | 随 PR 更新 | `data/manual/player-aliases.csv` |
| 赛事索引 | Chess-Results 爬虫 | 每周 | `docs/data/index/` |
| PGN | Chess-Results Game Database、赛事官网 PGN | 每周 | `docs/data/pgn/` |
| 百万级 bulk PGN | Lichess official broadcast archive | 每月 | `docs/data/bulk/` |
| 按棋手 PGN 派生层 | 已入库赛事 PGN 和 bulk 青少年 PGN | 每次 PGN 更新后 | `docs/data/index/by-player/`, `docs/data/pgn/by-player/` |

## 无 FIDE ID 棋手处理

采用两阶段模型：

1. `sighting`：某个赛事名单里的某一行，保留原始证据。
2. `domestic player`：由一个或多个 sighting 组成的国内临时身份。
3. `identity link`：人工审核后，把 sighting/domestic player 链接到 FIDE ID。

手工录入位置：

```text
data/manual/domestic-player-sightings.csv
data/manual/player-identity-links.csv
```

ID 规则：
- FIDE 棋手：`fide-<FIDE_ID>`
- 国内临时棋手：`domestic-<hash>`
- 赛事出现记录：`sighting-<hash>`

## GitHub Actions 更新流程

| Workflow | 触发 | 行为 |
|----------|------|------|
| `crawl-player-events.yml` | 手动 + 每周一 03:30 UTC | 爬取 CHN 棋手赛事记录 |
| `update-pgn.yml` | 手动 | 抓取缺失 PGN |
| `promote-public-pgn.yml` | 手动 | 晋升 PGN 到静态层 |
| `update-player-registry.yml` | 手动 + 每月 | 同步 FIDE 注册表 |
| `update-domestic-registry.yml` | 自动 | 生成国内身份层 |
| `update-name-aliases.yml` | 自动 | 更新别名索引 |
| `update-mimic-profiles.yml` | 手动 + 每周 | 刷新模拟 profile |
| `update-lichess-broadcast-bulk.yml` | 按月 | 镜像 Lichess broadcast |
| `reconcile-pgn-sources.yml` | 自动 | 验证 PGN 完整性 |

## 中文名补全流程

1. 在 `data/manual/player-aliases.csv` 添加或修改一行
2. 必须填写 `fide_id`，中文名/拼音/别名只是 alias
3. 不确定时保留空中文名，只填拼音和英文别名
4. 合并后运行 `python3 Scripts/sync_chinese_players.py`

棋协大师赛 Starting rank 表补全：

```bash
python3 Scripts/sync_chess_results_starting_rank_aliases.py
python3 Scripts/sync_chinese_players.py
python3 Scripts/sync_domestic_players.py
```

## 质量检查

```bash
python3 -m py_compile Scripts/sync_chinese_players.py Scripts/sync_static_pgn.py
python3 -m json.tool docs/data/registry/manifest.json >/dev/null
python3 -m json.tool docs/data/index/manifest.json >/dev/null
git diff --check
```

PGN 文件必须能解析出 `[Event "..."]` header。
