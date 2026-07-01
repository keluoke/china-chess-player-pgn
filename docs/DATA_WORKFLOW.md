# 中国棋手全数据库工作机制

## 目标定义

“中国棋手全数据库”分三层建设：

1. 身份层：所有 FIDE federation 为 `CHN` 的棋手，唯一主键为 FIDE ID。
2. 中文检索层：在 FIDE 英文名之外，持续补充中文名、拼音名和常见别名。
3. 赛事与棋局层：将 Chess-Results、国内赛事官网、李成智杯、世界/亚洲青少年赛等赛事记录和 PGN 挂到同一个 FIDE ID 下。

第一优先级是身份层完整，因为它决定同名棋手不会错合并。中文名和 PGN 可以逐步提高覆盖率，但必须挂到唯一 FIDE ID。

## 数据源分工

| 数据层 | 主数据源 | 更新频率 | 写入位置 |
| --- | --- | --- | --- |
| 棋手身份 | FIDE rating list legacy XML, not-rated included | 每月 | `docs/data/registry/` |
| 中文名/拼音/别名 | 人工审核 CSV、既有种子、赛事名单 | 随 PR 更新 | `data/manual/player-aliases.csv` |
| 赛事索引 | Chess-Results、国内赛事官网、亚洲/世界青少年赛事页 | 每周或按赛事 | `docs/data/index/` |
| PGN | Chess-Results Game Database、赛事官网 PGN、手工导入 | 每周或按赛事 | `docs/data/pgn/` |

FIDE 月度榜单用 legacy XML 版本，因为它包含未定级棋手；普通 rating list 只适合排行榜，不适合作为完整身份库。

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
```

GitHub 页面上有两个可手动运行的 workflow：

- `Update Chinese player registry`：刷新 FIDE CHN 棋手身份库。
- `Update static PGN archive`：刷新已登记赛事的 PGN。

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
