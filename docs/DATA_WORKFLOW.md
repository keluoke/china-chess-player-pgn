# 中国棋手数据库工作机制

## 分层

1. 身份层：FIDE ID 为唯一主键，registry 是姓名和等级分唯一权威。
2. 国内临时身份层：人工审核的 sighting 和 identity link。
3. 中文检索层：人工别名、拼音和强制姓名勘误。
4. 赛事/棋局层：只组合符合来源发布策略的资料；Chess-Results 清洗后的结构化赛事数据经完整性门禁与云端比对合并后发布（设计基线见 `EVENT_DATA_COMPLETENESS_BASELINE.md`），原始页面不发布。

## 数据源与发布策略

| 数据层 | 来源 | 频率 | 发布位置/策略 |
|---|---|---|---|
| 棋手身份和等级分 | FIDE legacy XML | 每月 | staging 校验后发布至 `docs/data/registry/` |
| 人工别名/勘误/转会 | 社区审核证据 | 随 PR | `data/manual/`、`data/community/` |
| 目标队列 | 社区 URL/tnr/FIDE ID | 随 PR | 人工队列；不含抓取内容 |
| Chess-Results 赛事全量数据 | 维护者本机 | 按队列 | 本地清洗后比对合并，发布至 `data/generated/chess-results-event-*`、`docs/data/pgn/chess-results/`；raw 留仓库外 |
| Lichess Broadcast | Lichess 开放数据库 | 每月 | `docs/data/bulk/`，CC BY-SA 4.0 |
| 派生索引/API | 已审核仓库输入 | 每次 ingest | Actions 离线重建 |

## 网络采集

唯一入口是 `Scripts/local/refresh.sh` 或本地面板：

```bash
bash Scripts/local/refresh.sh health
bash Scripts/local/refresh.sh all
bash Scripts/local/refresh.sh registry
bash Scripts/local/refresh.sh event-queue -- --from-queue 3
bash Scripts/local/refresh.sh bulk
```

仓库不提供抓取类 GitHub workflow。社区、GitHub-hosted runner 和自托管 runner
都不执行来源抓取。

每次任务持久化：

```text
runs/<run-id>/run.json
runs/<run-id>/run.log
runs/<run-id>/raw/
runs/<run-id>/extracted/
runs/<run-id>/staging/
runs/<run-id>/release-manifest.json
```

Chess-Results 运行只产生前三类私有文件，不产生 release manifest。FIDE 和 Lichess
必须先通过下载完整性、语义、数据量、隐私/勘误和来源策略校验。

## 发布与 CI

```text
staging → 校验 → 原子晋升 → 精确 release manifest → 本地 commit
  → force-push local-data → CI 复核路径/哈希/来源策略
  → 精确 ingest main → 离线重建索引 → 部署
```

本地发布器和 CI 都拒绝：

- `data/manual/`、`data/community/`、`data/incoming/` 自动发布；
- Chess-Results 原始页面或未清洗产物（link-only 已退役，只发布清洗后的结构化数据）；
- HTML/WARC 原始网页；
- manifest 之外的文件；
- SHA-256 不一致的内容。

## 中文名与身份

- 人工中文名进入 `data/manual/player-aliases.csv`；
- 已确认错名必须进入 `data/community/name-corrections.csv`；
- 转会必须进入 `data/community/federation-overrides.csv`；
- 机器姓名候选只保存在私有 `extracted/`，不得直接覆盖 registry；
- FIDE 重建最后强制应用勘误，并验证 compact players 与所有 shard 权威字段一致。

无 FIDE ID 棋手继续采用 sighting → domestic player → identity link 的人工审核模型：

```text
data/manual/domestic-player-sightings.csv
data/manual/player-identity-links.csv
```

## 质量检查

```bash
python3 -m unittest -v Scripts.tests.test_local_pipeline
python3 -m compileall -q Scripts
python3 Scripts/validate_community_data.py
python3 Scripts/validate_incoming.py
python3 Scripts/validate_registry_authority.py  # 需在离线重建派生索引后运行
bash -n Scripts/local/refresh.sh
git diff --check
```
