# Chess-Results 棋手赛事爬虫（SpielerSuche）

`Scripts/crawl_player_events.py` 按数据仓库里已有的中国棋手 FIDE ID，到 Chess-Results
Player-Database（`https://s3.chess-results.com/SpielerSuche.aspx?lan=1`）逐个搜索该棋手
参加过的全部赛事，建立赛事索引、抓取 tnrid，并顺带把能看到的中文名映射入库；随后可把发现的
tnrid 交给已有的赛事爬虫抓取对局 PGN 与名次。

## 流程

1. **棋手搜索**：读取 `docs/data/registry/players.json` 里所有 `federation=CHN` 的 FIDE ID
   （并补上 `data/manual/player-aliases.csv` 里手工维护的 FIDE ID）。对每个 ID 用 FIDE-ID
   字段 POST 搜索表单，解析结果表格得到每一行参赛记录：tnrid、赛事名、结束日期、该棋手的
   名次 / 轮数 / 参赛人数、俱乐部、联合会、报名序号（snr）。
2. **赛事索引**：把参赛记录写入按棋手聚合的 CSV，并汇总成全局 tnrid 目录 JSON。
3. **中文名映射**：结果行里出现中日韩字符时，作为 `FIDE ID → 中文名` 证据写入映射 CSV，
   供 `sync_chinese_players.py` / 别名重整流程复核后合并。
4. **对局 / 名次**（`--fetch-games`）：把新发现的 tnrid 交给
   `fetch_event_pgn.process_event`，按赛事整包下载 PGN 并按中国棋手拆分入静态归档。棋手在每个
   赛事的名次已随第 1 步的搜索结果一并入库。

爬取状态会持久化，支持断点续爬与按天增量刷新，适合一次跑完上万名棋手。

## 输出

```text
data/manual/chess-results-player-events.csv          # 按棋手的参赛记录（含名次）
data/manual/chess-results-player-name-map.csv        # FIDE ID → 中文名 映射证据
data/manual/chess-results-spielersuche-state.json    # 断点续爬状态
docs/data/index/chess-results-tournaments.json       # 全局 tnrid 赛事目录
docs/data/index/chess-results-spielersuche-manifest.json
```

## 用法

```bash
# 爬取全部 CHN 注册棋手，1 秒/次，断点续爬
python3 Scripts/crawl_player_events.py

# 只爬几名棋手，并立刻抓取他们的对局
python3 Scripts/crawl_player_events.py --player 8603677 --player 8601429 --fetch-games

# 只重爬 30 天内没爬过的棋手
python3 Scripts/crawl_player_events.py --refresh-days 30

# 干跑，不写文件
python3 Scripts/crawl_player_events.py --max-players 20 --dry-run
```

常用参数：`--delay`（请求间隔，默认 1 秒）、`--workers`（并发，默认 2）、
`--checkpoint`（每 N 名棋手落盘一次，默认 50）、`--skip-done`（跳过已爬过的棋手）、
`--max-events`（`--fetch-games` 时限制赛事数）、`--overwrite`（即使已有 PGN 也重抓）。

> 全量约 1.1 万名棋手，按 1 秒/次、2 并发估算需数小时。中途可随时中断，下次带
> `--refresh-days` 或 `--skip-done` 续跑。

## 自动化

`.github/workflows/crawl-player-events.yml` 每周一 03:30 UTC 增量刷新，也可在 Actions 页面
手动触发（可指定单个 FIDE ID、棋手上限、刷新天数、请求间隔，以及是否顺带抓对局）。运行后会
重建静态 PGN 索引并提交 `data/manual` 与 `docs/data` 的变化。
