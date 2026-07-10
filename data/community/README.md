# data/community/ — 社区可编辑数据

本目录下的文件由社区通过 PR 维护,CI 自动校验格式。机器产出的数据在 `data/generated/`,请勿手工编辑那边的文件。

## federation-overrides.csv — 转会棋手覆盖表

注册表默认收录 FIDE 现联邦为 CHN 的全部棋手。转会棋手用本表标注:

| 列 | 说明 |
|---|---|
| `fide_id` | FIDE ID(必填) |
| `type` | `transferred_out`(转出:现联邦已非 CHN,但保留在库中并打标)或 `transferred_in`(转入:现联邦为 CHN,标注原联邦) |
| `former_federation` | 原联邦三字码;转出行可留空(默认 CHN) |
| `current_federation` | 现联邦三字码(转出行填写,便于核对) |
| `effective` | 生效年月(YYYY-MM,尽量提供) |
| `evidence_url` | 证据链接(FIDE 档案页 / 新闻,必填) |
| `notes` | 备注 |

示例:

```csv
fide_id,type,former_federation,current_federation,effective,evidence_url,notes
8600000,transferred_out,CHN,SGP,2023-05,https://ratings.fide.com/profile/8600000,示例行
```

每次 registry 抓取会对比上月联邦快照(`data/generated/federation-snapshots/`),
自动把"消失/新出现"的棋手写进 `data/generated/transfer-candidates.json` 供人工核对后补录本表。

## tournament-name-mappings.csv — 赛事中文名映射

Chess-Results 同一赛事可能只有截断英文名，也会随界面语言变化。赛事的稳定键是
`source + tournament_id`；中文展示名必须在本表经人工核对后维护，禁止直接编辑
`docs/data/index/events.json` 或任何爬虫输出。

| 列 | 说明 |
|---|---|
| `source` | 来源名称，目前为 `Chess-Results` |
| `tournament_id` | 信源稳定赛事 ID；Chess-Results 使用 `tnr` 后的数字 |
| `chinese_name` | 面向用户的中文赛事全称（可含组别） |
| `evidence_url` | 对应 Chess-Results / FIDE 等公开赛事页，必填 |
| `notes` | 映射依据，例如组别或信源原始标题 |

CI 校验唯一性、ID 格式和证据域名。派生脚本只读取本表：映射不会覆盖原始赛事标题，
会同时保留 `name`（信源原文）与 `chineseName`（社区核验名），方便复核。
