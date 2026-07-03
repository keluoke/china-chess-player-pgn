# Mac 手工赛事入库

## 赛事链接入库

1. 打开 mac 应用左侧的“手工入库”。
2. 在“赛事链接”中粘贴 Chess-Results 赛事链接，例如：
   `https://chess-results.com/tnr935824.aspx?lan=1`
3. 点击“分析、入库并同步 GitHub”。
4. 程序会下载该 TournamentID 的整场 PGN，去重后按棋手拆分，先写入本地：
   `~/Library/Application Support/ChinaChessPlayerPGN/PGNArchive/`
5. 随后自动运行 `Scripts/sync_static_pgn.py --from-local-cache`，把本地归档同步到仓库 `docs/data/pgn/` 并重建静态索引。
6. 如果 `docs/data` 有变化，应用会执行 `git add docs/data`、提交并 `git push origin HEAD`。
7. 入库后首页缓存统计、棋手搜索、棋手看板会读取这些本地归档；GitHub Pages 网页端读取 `docs/data/`。

默认策略：

- 已在本地库或用户映射表中识别的棋手会入库。
- PGN 头中明确标记 `CHN` 的棋手会入库。
- 中国站赛事可开启“自动创建未映射棋手”，用于国内赛、低龄组、无 FIDE ID 棋手。
- 世界/亚洲青少年赛中无 FIDE ID 的棋手建议先补映射表，再重新入库。

## 用户名称映射表

在“手工入库”页点击“打开映射表”，应用会创建并打开：

`~/Library/Application Support/ChinaChessPlayerPGN/user-name-mapping.csv`

字段：

```csv
alias,fide_id,display_name,chinese_name,pinyin_name,english_name,federation,birth_year,standard_rating,rapid_rating,blitz_rating,note
```

最少填写 `alias`。推荐填写方式：

```csv
alias,fide_id,display_name,chinese_name,pinyin_name,english_name,federation,birth_year,standard_rating,rapid_rating,blitz_rating,note
Yan Xuan,8627215,姜天瑜,姜天瑜,jiang tianyu,"Jiang, Tianyu",CHN,2010,,,,示例
王小明,,王小明,王小明,wang xiaoming,,CHN,2016,,,,无 FIDE ID 低龄组选手
```

保存 CSV 后回到应用，点击“导入映射表”。之后再次提交赛事链接，PGN 中出现的 `alias` 会绑定到对应棋手。

完整别名来源、SQLite 查询和全部映射导出说明见 [NAME_MAPPING.md](NAME_MAPPING.md)。

## 清洗规则

- 按 PGN `[Event "..."]` 切分棋局。
- 过滤 HTML/空响应/缺少 Event、White、Black 的无效棋局。
- 按赛事、日期、双方、结果和走法做去重。
- 每个识别出的棋手生成独立 PGN 归档。
- 未识别棋手会在结果区列出，可补到映射表后重试。

## GitHub 同步要求

- 从仓库内的 `dist/中国棋手 PGN.app` 启动应用，程序会自动向上查找 `.git`、`Scripts/sync_static_pgn.py` 和 `docs/data`。
- 如果把 `.app` 复制到了其他位置，需设置环境变量 `CHINA_CHESS_PGN_REPO=/path/to/china-chess-player-pgn` 后再启动。
- 本机 Git 需要能向 `origin` 推送；当前仓库 remote 应指向 `git@github.com:keluoke/china-chess-player-pgn.git`。
- 自动提交只暂存 `docs/data`，不会把 Swift 源码、说明文档或其他工作区改动一起提交。
