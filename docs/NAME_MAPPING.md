# 棋手名称映射查看与维护

## 在 mac 应用里查看

打开 `dist/中国棋手 PGN.app`，进入左侧“手工入库”。

- “用户名称映射表”：只显示你手工维护的 `user-mapping`。
- “全部来源映射”：显示 seed / FIDE / PGN / user-mapping 的别名映射，页面默认展示前 1000 条。
- “导出全部 CSV”：导出完整别名表到：
  `~/Library/Application Support/ChinaChessPlayerPGN/all-name-mappings.csv`

点击“全部来源映射”里的任意一行，会把该行复制到上方编辑区；保存后会作为 `user-mapping` 写入，不会直接覆盖 seed/FIDE/PGN 的证据来源。

## CSV 样式

手工维护文件在：

`~/Library/Application Support/ChinaChessPlayerPGN/user-name-mapping.csv`

字段：

```csv
alias,fide_id,display_name,chinese_name,pinyin_name,english_name,federation,birth_year,standard_rating,rapid_rating,blitz_rating,note
```

最少填 `alias`。有 FIDE ID 时填 `fide_id`，无 FIDE ID 的低龄组选手可只填中文名/拼音/出生年。

## SQLite 表

本地数据库在：

`~/Library/Application Support/ChinaChessPlayerPGN/china-chess-player-pgn.sqlite`

核心表：

- `players`：唯一棋手记录，优先用 `fide-<FIDE ID>`，无 FIDE ID 时用稳定 `local-<hash>`。
- `player_aliases`：名称映射表，包含 alias、normalized_alias、alias_type、source、player_id。

常用查询：

```sql
SELECT a.player_id, a.alias, a.alias_type, a.source,
       p.fide_id, p.chinese_name, p.pinyin_name, p.english_name
FROM player_aliases a
JOIN players p ON p.id = a.player_id
ORDER BY a.source, a.alias;
```

## source 含义

- `seed`：代码内置种子棋手和重点棋手资料。
- `FIDE` / `Chess-Results + FIDE`：联网补齐得到的 FIDE/Chess-Results 信息。
- `Chess-Results` / `pgn`：赛事搜索或 PGN 入库时从 PGN/赛事记录提取的名字。
- `user-mapping`：你在 mac 页面或 CSV 中手工确认的映射，优先用于解决无 FIDE ID、重名、中文名/拼音/英文名不一致。
