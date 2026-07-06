# 棋手名称映射查看与维护

## CSV 样式

手工维护文件在 `data/manual/player-aliases.csv`：

```csv
fide_id,chinese_name,pinyin_name,english_name,aliases,federation,birth_year,standard_rating,rapid_rating,blitz_rating
```

有 FIDE ID 时填 `fide_id`，无 FIDE ID 的低龄组选手可只填中文名/拼音/出生年。

## source 含义

- `seed`：代码内置种子棋手和重点棋手资料。
- `FIDE` / `Chess-Results + FIDE`：联网补齐得到的 FIDE/Chess-Results 信息。
- `Chess-Results` / `pgn`：赛事搜索或 PGN 入库时从 PGN/赛事记录提取的名字。
- `chess-results-starting-rank`：从 Chess-Results Starting rank 表读取的中文名证据。
- `user-mapping`：用户手工确认的映射。

## 常用操作

```bash
# 从 Chess-Results Starting rank 表补中文名
python3 Scripts/sync_chess_results_starting_rank_aliases.py
python3 Scripts/sync_chinese_players.py
python3 Scripts/sync_domestic_players.py

# 查看当前别名统计
python3 -c "
import json, pathlib
r = json.loads(pathlib.Path('docs/data/registry/players.json').read_text())
print(f'{len(r)} 名棋手')
"
```
