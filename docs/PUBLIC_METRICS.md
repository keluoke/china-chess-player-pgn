# 对外数据指标口径

所有对外页面与 API 只使用 `docs/data/public-metrics.json` 这一份指标契约。

## 唯一口径

- **有对局棋手**：`docs/data/index/by-player/` 中存在至少一盘可用、去重后 PGN 的棋手。
- **总对局**：按棋手聚合后的可用 PGN 盘数；同一盘棋会分别出现在两位中国棋手名下时，按棋手数据包的使用口径计数。
- **来源范围**：包含 direct、bulk、Lichess broadcast 与已审核入库的社区来源。
- **统计权威**：`docs/data/index/by-player/manifest.json`。其他构建脚本只能读取该聚合结果，不得自行重算全库总量。

`docs/data/index/manifest.json` 原有的 direct 静态源统计保留在 `sourceTotals`，仅用于管线诊断，不是全库覆盖数字。

CI 运行 `Scripts/validate_public_metrics.py`，强制检查 index manifest、API manifest、changelog 最新项和 dashboard 的“有对局棋手/总对局”完全一致。
