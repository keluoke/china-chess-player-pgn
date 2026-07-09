# 改版计划:开源社区共建的中国国际象棋棋手数据库

> 基于外部 review 文档(china-chess-player-database-proposal.md)的评审结论 + 项目实际目标制定。
>
> 四个目标:① 社区共同维护的开源数据库;② 数据量扩大到所有 CHN 棋手,转出/转入棋手特殊处理;③ 年龄维度覆盖成年棋手;④ 删除风格模拟,改为对外提供棋手数据 API。

---

## 一、对 review 文档的评审

### 采纳(合理)

| 建议 | 评价 |
|---|---|
| CONTRIBUTING.md + Issue/PR 模板 + CODEOWNERS | 成本极低、社区化的第一步,照单采纳 |
| CI 数据校验(FIDE ID 格式、CJK 检查、别名去重、URL 白名单) | 强烈同意。本项目刚经历过脏数据事故(中文名带尾逗号、赛事标题混入人名),校验应更早存在 |
| 机器产出与人工数据目录分离(data/generated vs data/community) | 正确。data/manual/ 目前混放爬虫状态文件和人工 CSV,边界确实模糊 |
| 静态 API(docs/api/v1,构建期生成、零运行时) | 与目标 ④ 完全一致,构建成本低,采纳并扩充(见第五节) |
| 数据仓库与风格引擎拆分 | 同意且更进一步:本仓库直接删除风格模拟,不做迁移承诺(目标 ④) |
| 证据分层(L1-L5)+ 同名消歧 SOP + identity link 置信度分级 | 设计正确,写入 CONTRIBUTING,但初期简化执行(见"缓行"第 2 条) |
| 数据 Changelog、前端"数据有误?"贡献入口 | 采纳,成本低、激励闭环 |

### 驳回或修正(不合理)

| 建议 | 问题 | 修正 |
|---|---|---|
| **CI 自动回抓 Chess-Results 做交叉校验** | **事实性错误**:chess-results 封锁 GitHub 数据中心 IP——这是本项目抓取/索引分离架构存在的根本原因,文档自己在 1.1 里还称赞了这一点。CI 里回抓必然失败 | 校验拆两级:CI 只做离线校验并打 `needs-local-verification` 标签;本地 refresh.sh 新增 `verify` 命令消费待验证队列,结果推回仓库 |
| **全量棋手迁移为一人一 YAML** | 规模错配:扩到全量 CHN 后注册表约 1.2 万人(还会增长)。注册表数据(姓名/等级分/头衔/出生年)由 FIDE 月度榜机器生成,为它们手工维护 1.2 万个 YAML 是把机器数据当人工数据,徒增噪音和构建成本 | 改为**覆盖层模型**:注册表保持机器生成;`data/community/players/` 只存放"有社区补充内容"的棋手 YAML(中文名、别名、外链、转会备注等),按需创建,当前约 3700 人有中文名、实际需要 YAML 的更少 |
| Contributor/Reviewer/Maintainer 三级信任体系、2-reviewer 规则 | 过度设计:项目目前是零社区,先建三层权限体系是本末倒置 | 初期用 GitHub 原生机制(CODEOWNERS + branch protection + 1 approve)。社区活跃后再考虑 |
| PR 审核摘要 bot | 有价值但非关键路径 | 降级至最后阶段 |
| 前端 ES modules 拆分(1.5 天) | 合理但 app.js 1200 行尚可维护,不阻塞任何数据目标 | 保留,排在数据目标之后 |

### 文档遗漏(目标里有、文档没覆盖)

1. **转会棋手处理**——目标 ② 的核心需求,文档只字未提(见第三节)。
2. **成年棋手年龄维度**——文档 5.3 仍只有 U8-U18,目标 ③ 未覆盖(见第四节)。
3. **数据许可证**——开源数据项目必须声明:代码许可(建议 MIT)与数据许可(建议 CC BY 4.0,并说明 PGN 原始权利归属与 chess-results 使用条款边界)。缺这个,"开源共建"不成立。
4. **风格模拟删除路径**——文档只说"拆分",未给出本仓库的删除清单(见第二节)。

---

## 二、阶段 0:删除风格模拟(先做减法)

删除清单(先打 tag `pre-mimic-removal` 存档,不做独立仓库迁移):

- `mimic-engine/`(956 KB,含 webplay/Stockfish 封装)
- `docs/mimic/`(42 MB profile 数据,删除后部署体积显著下降)
- `Scripts/build_youth_mimic_profiles.py`
- `.github/workflows/update-mimic-profiles.yml`
- 前端:棋手页"模拟对局"入口及相关 JS/CSS;README、ARCHITECTURE 相应章节
- refresh.sh / rebuild-indexes 中的关联步骤(如有)

替代承诺写入 README:"风格模拟由外部项目基于本库 API 实现"(见第五节 API)。

## 三、阶段 1:全量 CHN 注册表与转会处理

现状:`sync_chinese_players.py` 用 `federation == "CHN"` 硬过滤 FIDE 月度榜,1.16 万人,含 inactive 标记。

1. **联邦快照留痕**:每次 registry 抓取保存 `{fide_id: federation}` 的轻量快照(`data/generated/federation-snapshots/YYYY-MM.json`)。有了时序快照,转出/转入自动可检测,不依赖人工申报。
2. **转会覆盖表** `data/community/federation-overrides.yml`:
   - `transferred_out`(如转 SGP/USA 的原中国棋手):FIDE 现联邦≠CHN,但**保留在库中**,标记 `formerFederation: CHN` + 生效时间 + 证据链接;
   - `transferred_in`(转入 CHN 的外籍棋手):FIDE 现联邦=CHN,默认收录,标记 `formerFederation`,前端可筛选排除;
   - 争议/双重情形逐条备注。
3. **注册表构建规则**:收录 = `当前联邦 CHN` ∪ `overrides 中 transferred_out`。每人新增字段 `federationHistory`(由快照+覆盖表推导)与 `transferFlags`。
4. **前端**:棋手卡片显示转会徽标(如"CHN → SGP (2023)");搜索/排行榜增加"包含已转出 / 仅现役 CHN / 仅本土培养"筛选。
5. 首次回填:用现有注册表 + FIDE 官网核对整理一批已知转会名单(丁-、转 SGP/AUS/USA 的青少年等)进覆盖表。

## 四、阶段 2:年龄维度扩展到成年组

现状:排行榜只有 U8-U18(李成智杯口径),出生年覆盖 11248/11623,足够支撑。

1. 年龄组计算统一为纯函数(输入出生年+基准日),口径文档化:
   - 青少年:U8/U10/U12/U14/U16/U18(保留现口径)
   - 新增:U20、成年公开组(18+)、S50、S65(FIDE 元老口径)
2. 排行榜与搜索增加"年龄组"筛选维度,成年组默认按现行 standard 分排序;youth-leaderboards.json 泛化为 `leaderboards.json`(按组分片,避免单文件过大)。
3. by-player PGN 分段包同步扩展:在 U8-U18 之外增加 `adult.pgn`(18 岁后对局),API 同步暴露。

## 五、阶段 3:静态数据 API v1(风格引擎等外部项目的接口)

采纳 proposal 第 5 节设计,做三处扩充:

1. 路径:`docs/api/v1/`,由 `Scripts/build_api.py` 在 rebuild-indexes 末尾生成(纯计算)。端点:`manifest.json`、`players.json`(分片)、`search.json`、`players/fide-{id}.json`、`players/fide-{id}/events.json`、`players/fide-{id}/pgn/{all,U8...U18,adult}.pgn`。
2. 扩充字段:`federationHistory`/`transferFlags`(阶段 1)、成年分段(阶段 2)、`license` 与 `attribution` 字段写入 manifest。
3. 稳定性承诺:v1 路径冻结,破坏性变更走 v2;`docs/API.md` 写清端点、字段、更新频率(数据更新即重建)、CORS(Pages 默认 `*`)。
4. 风格引擎(未来外部项目)按 proposal 附录方式消费:轮询 manifest → 拉增量 PGN → 自行建模,与本仓库零代码耦合。

## 六、阶段 4:社区化基建

1. **许可与文档**:LICENSE(代码 MIT)+ LICENSE-DATA(CC BY 4.0 + PGN 来源说明);CONTRIBUTING.md 含四种贡献路径 SOP、证据分层 L1-L5、同名消歧规则(≥2 项证据);Issue 模板(纠错/新棋手/转会申报)。
2. **目录重构**:
   - `data/community/`:players/*.yml(覆盖层)、federation-overrides.yml、domestic-player-sightings.csv、player-identity-links.csv
   - `data/generated/`:爬虫产出(events csv、name-map csv、spielersuche-state、federation-snapshots)
   - 迁移脚本一次性完成 + 兼容读取旧路径一个过渡期
3. **CI 校验**(全部离线):`validate_community_data.py`——YAML schema、FIDE ID 与文件名一致、CJK 人名规则(复用已实现的 sanitize_person_name)、别名去重、覆盖表引用存在性、sightings 域名白名单;PR 触发,校验失败阻断。
4. **本地核验队列**:CI 对带 chess-results 证据的提交打 `needs-local-verification`;`refresh.sh verify` 在住宅 IP 回抓比对,结果(verified/mismatch)以提交回写。
5. **Changelog 与贡献者**:rebuild 时生成 `docs/data/changelog.json`(新增棋手/新中文名/新 PGN 计数),前端展示;贡献者列表由 git log + YAML contributors 生成。
6. **前端贡献入口**:棋手页"数据有误?"→ Issue 模板预填该棋手;"补充信息"→ GitHub 网页编辑对应 YAML(不存在则从模板创建)。

## 七、阶段 5:前端工程化(最后做)

ES modules 拆分(按 proposal 6.1 结构)、i18n 抽离、加载层统一缓存。不引入构建工具,保持纯静态。

---

## 八、实施顺序与依赖

```
阶段0 删风格模拟          —— 独立,先做,立减 42MB 部署体积
阶段1 全量CHN+转会        —— 依赖 registry 脚本改造
阶段2 成年组维度          —— 依赖阶段1 的注册表字段
阶段3 静态API v1          —— 依赖阶段1/2 的数据模型定稿(否则 v1 冻结即背债)
阶段4 社区化基建          —— 许可/文档/CI 可与阶段1-3 并行;目录重构放在 API 定稿后
阶段5 前端工程化          —— 最后
```

估算(单人):阶段 0 半天;阶段 1 约 2 天(含转会名单首次回填);阶段 2 约 1 天;阶段 3 约 1.5 天;阶段 4 约 3 天;阶段 5 约 1.5 天。合计 ~9.5 天,可按阶段独立交付,每阶段结束即可部署。

## 九、风险

1. **PGN 再分发边界**:chess-results 的 PGN 公开可下,但批量再分发的条款边界需在 LICENSE-DATA 里明确声明来源与非商用建议,保留 `promote_public_pgn` 的"可公开性"把关逻辑。
2. **转会数据准确性**:FIDE 快照只能发现"变化",无法给出准确生效日期,覆盖表需人工补证据——正好作为社区化后的第一批贡献任务。
3. **v1 API 冻结时机**:必须在阶段 1/2 数据模型定稿后再发布,避免刚发布就 v2。
