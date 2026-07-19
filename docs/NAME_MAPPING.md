# 姓名与身份解决机制（现行口径）

> 2026-07 重写。旧版本允许"无 FIDE 行写入 player-aliases"的口径已废除：
> `player-aliases.csv` 只服务 FIDE 棋手；无 FIDE 身份一律走 domestic 实体
> 与 `player-identity-links.csv`，两者不可混用。

## 五层结构

1. **来源观察层**（私有）：抓取产物中的原始文本，允许是脏的；只存在于
   维护者本机私有区和 `data/generated/` 机器层，绝不直接进入公共别名。
2. **清洗候选层**：`sanitize_person_name`（2–6 汉字、防标点/赛事标题/尾逗号）
   过滤后的候选；未通过者进入隔离区，不得进入公开数据。
3. **审核别名层**：`data/manual/player-aliases.csv`——已确认的 FIDE 棋手
   中文名/拼音/别名。**必须有 `fide_id`**。
4. **强制勘误层**：`data/community/name-corrections.csv` 在构建最后应用，
   可覆盖已有值并清除错误别名（tombstone 防复活）。已确认勘误一律写这里。
5. **身份链接层**：`data/manual/player-identity-links.csv`——解决
   "这条赛事观察属于谁"。与"这个人叫什么"分开维护；只接受人工审核，
   可撤销、可追溯。

## 无 FIDE 棋手

- 每条赛事观察投影为 `PersonObservation`（`Scripts/build_person_observations.py`，
  机器层 `data/generated/person-observations.csv`），带组别、名次、得分、轮数。
- `sync_domestic_players.py` 将观察与人工 sightings 合并成 domestic 实体；
  同名从不自动合并。
- 机器候选卡（弱证据：同俱乐部 +25、低组得分率 ≥65% 且 24 个月内升组 +35、
  年龄连续 +15、出生年一致 +20、公开地区一致 +10；硬冲突 -100 禁边）输出到
  仓库外 `identity-workbench/identity-candidates.json`，仅用于排序人工审核队列。
- 人工确认后写 `player-identity-links.csv`；拒绝或不可能的合并记入
  `presentation-disputes.csv`（负链接/tombstone），防止同一错误建议反复出现。

高置信候选可以进入前端默认展示聚合，但不构成永久身份链接。FIDE 中文名候选
使用 `suggestedChineseName` 单独投影；重复赛事一致时可暂定展示，审核接受后才写
`player-aliases.csv`。完整审核步骤见 `MAINTAINER_IDENTITY_REVIEW_GUIDE.md`。

## 权威规则（不变）

- registry 是姓名、FIDE ID、联邦、等级分唯一权威；派生层禁止反向覆盖。
- 8602980=侯逸凡、8603006=居文君、8608288=许翔宇 等历史勘误由
  `validate_community_data.py` 钉死断言，回归即红灯。

## 常用操作

```bash
# 从已抓赛事重建观察层并更新 domestic 实体
python3 Scripts/build_person_observations.py
python3 Scripts/sync_domestic_players.py

# FIDE 棋手中文名证据（Starting rank 表）
python3 Scripts/sync_chess_results_starting_rank_aliases.py
python3 Scripts/sync_chinese_players.py
```
