# 中国国际象棋 PGN 侦察兵

`Scripts/pgn_scout.py` 是本地原始 PGN 资产采集器。它的职责是先把所有可能有价值的原始 PGN、ZIP、ZST 文件收进本机资产库，做拆分、索引和中国相关对局过滤；确认质量后，再用 `Scripts/promote_public_pgn.py` 晋升到 `docs/data/pgn/`，最后由 `Scripts/sync_static_pgn.py` 重建网页索引。

默认本地资产目录：

```text
~/Library/Application Support/ChinaChessPlayerPGN/RawPGNScout/
```

仓库不提交这里的原始大文件，避免把商业库导出、TWIC 全集或大规模直播包误放进 Git。

## 来源入口

- Chess-Results PGN search: <https://chess-results.com/PartieSuche.aspx?lan=1>
- Lichess API: <https://lichess.org/api>
- Lichess database archives: <https://database.lichess.org/>
- Chess.com Published Data API: <https://www.chess.com/news/view/published-data-api>
- TWIC: <https://theweekinchess.com/twic>

## 初始化

```bash
python3 Scripts/pgn_scout.py init
```

本地结构：

```text
RawPGNScout/
  pgn_scout.sqlite
  raw/
  extracted/
  filtered/china/
  manifests/
  reports/
```

`raw/` 保存原始下载或导入文件；`extracted/` 保存从 ZIP/ZST 解出的 PGN；`filtered/china/` 保存按中国棋手白名单、FIDE ID、Site/Event 关键词筛出的中国相关 PGN。

## Chess-Results

先从当前仓库索引生成 TournamentID/FIDE ID 目标表：

```bash
python3 Scripts/pgn_scout.py seed-chess-results-targets
```

批量试探下载：

```bash
python3 Scripts/pgn_scout.py fetch-chess-results --max-requests 50
```

只盯某个棋手：

```bash
python3 Scripts/pgn_scout.py fetch-chess-results --player 8657238 --max-requests 20
```

撞某个 TournamentID：

```bash
python3 Scripts/pgn_scout.py fetch-chess-results --tournament 1210266 --player 8657238
```

Chess-Results 是优先源，但不是每个赛事都上传 PGN。脚本只把能解析出 `[Event "..."]` header 的内容入库。

## Lichess Broadcasts

导入单个 broadcast 页面或 raw PGN URL：

```bash
python3 Scripts/pgn_scout.py fetch-lichess --url "https://lichess.org/broadcast/..." --extract
```

扫描 Lichess 开放数据库中的 broadcast 档案：

```bash
python3 Scripts/pgn_scout.py fetch-lichess --database-broadcasts --year 2025 --max-downloads 3 --extract
```

`.pgn.zst` 会优先用 Python `zstandard` 解压；如果没有该模块，会尝试系统 `zstd` 命令。

## Chess.com Public API

下载单个用户名的月度 PGN：

```bash
python3 Scripts/pgn_scout.py fetch-chesscom --username username --year 2025 --month 01
```

按国家代码先发现用户，再拉取月度档案：

```bash
python3 Scripts/pgn_scout.py fetch-chesscom --country CN --year 2025 --max-users 20 --max-downloads 40
```

Chess.com 这条线更适合侦察线上活动和公开账号对局。它不等同于线下 FIDE 正规赛事，进入正式静态 PGN 前仍需要人工确认赛事身份和授权。

## TWIC 全集

下载并解压 TWIC issue 范围：

```bash
python3 Scripts/pgn_scout.py fetch-twic --start 1500 --end 1510 --extract
```

TWIC 文件会被完整保存到本地 `raw/twic/`，再拆 PGN 做中国相关过滤。大规模跑 TWIC 前，先小范围验证筛选规则。

## ChessBase Mega Database

商业库不应进入仓库。推荐流程：

1. 在 ChessBase 里用 Advanced Search 导出 `Historical_CHN_Base.pgn`。
2. 放在本机任意目录。
3. 用侦察兵导入：

```bash
python3 Scripts/pgn_scout.py ingest ~/Downloads/Historical_CHN_Base.pgn --source chessbase-mega
```

这个 PGN 只保存在本地 RawPGNScout。后续如果要公开分发，需要确认版权和授权。

## 国内直播 H5 / 协会官网 / Wayback

这类来源先按“手工抓包、脚本导入”的方式处理：

```bash
python3 Scripts/pgn_scout.py ingest ~/Downloads/li-chengzhi-live.pgn --source ccf-live
python3 Scripts/pgn_scout.py ingest ~/Downloads/old-association-pgn.zip --source ccf-archive --extract
```

抓包得到 JSON 走子流时，先另写转换脚本生成 PGN，再交给 `ingest`。不要把未确认接口稳定性的抓包 token 或小程序私有接口写进仓库。

## 报告

```bash
python3 Scripts/pgn_scout.py report --write ~/Desktop/pgn-scout-report.md
```

报告会列出本地资产数、总棋局数、中国相关棋局数、按来源统计和最近导入资产。

## 晋升到网页静态 PGN

侦察兵只负责本地原始资产。进入网页前必须满足：

1. 文件是合法 PGN，不是 HTML 错误页。
2. 来源在 `data/manual/public-pgn-sources.csv` 中标记为可公开分发。
3. 可以挂到明确的 FIDE ID 和赛事 ID。
4. 经过 `Scripts/promote_public_pgn.py` 写入 `docs/data/pgn/`，再由 `Scripts/sync_static_pgn.py` 更新 `docs/data/index/`。

按 FIDE ID 扫 Chess-Results 并按赛事拆包发布：

```bash
python3 Scripts/promote_public_pgn.py --scan-chess-results --player 8657238 --max-players 0
```

从本地侦察兵资产库晋升已允许再分发的来源：

```bash
python3 Scripts/promote_public_pgn.py --promote-scout --source lichess
```

TWIC、Chess.com 和国内官网默认只进本地侦察兵，不直接发布。只有确认某批文件允许公开再分发后，才把 `data/manual/public-pgn-sources.csv` 对应来源改为 `redistributable=yes,status=approved`，再运行晋升命令。

商业库、私有抓包和授权不明确的 PGN，只做本地研究资产，不提交进 GitHub。
