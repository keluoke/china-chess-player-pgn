# 社区治理与数据采集边界

治理目标是数据正确、证据可追溯、社区贡献门槛低，同时把第三方来源访问和发布
责任集中到明确的维护者流程。

## 角色

| 角色 | 权限与职责 |
|---|---|
| 访客 | 浏览网站/API，提交错误或缺失线索 |
| 数据贡献者 | 提交 URL、tnr、FIDE ID、人工名称、勘误和质量报告；不抓取数据 |
| 审核者 | 审核人工证据、身份消歧、姓名勘误和联邦覆盖 |
| 采集维护者 | 在登记的本机住宅网络运行抓取、清洗、私有留存和发布审批 |

## 数据分层与写入权

| 层 | 路径 | 写入方 |
|---|---|---|
| 注册表权威 | `docs/data/registry/` | 维护者 FIDE 发布包 |
| 机器投影 | `data/generated/` | 维护者发布包 / Actions 构建 |
| 人工知识 | `data/manual/`、`data/community/` | 社区 PR + 审核 |
| 目标线索 | `data/incoming/` | target-only 社区 PR |
| 私有原始区 | 仓库外 `runs/<run-id>/raw/` | 仅采集维护者本机 |
| 派生索引/API | `docs/data/`、`docs/api/` | GitHub Actions 离线重建 |

铁律见 `AGENTS.md`：registry 是姓名与等级分唯一权威；已确认姓名错误必须进入
`data/community/name-corrections.csv` 并由 CI 钉死；任何派生层不得反写 registry。

## 社区可以做什么

- 提交赛事 URL、tnr、FIDE ID、缺失原因和优先级；
- 维护中文赛事名、人工别名及官方来源链接；
- 提交姓名勘误、联邦变更证据和身份关联建议；
- 参与数据质量审核、消歧和产品反馈；
- 对涉及未成年人的敏感材料使用私下渠道。

社区不得：

- 运行项目的 Chess-Results/FIDE/Lichess 抓取管线；
- 上传 HTML、PGN、WARC、解析表、响应头或抓取缓存；
- 共享访问额度、代理、cookie 或绕过限制的方法；
- 把第三方内容作为自己的 CC BY 数据重新授权。

## 目标线索生命周期

1. 贡献者通过网页 Issue 或 `data/incoming/<id>/manifest.json` 提交目标；
2. CI 只校验 schema、URL、标识符和“无抓取产物”边界；
3. 审核者把有效线索加入人工目标队列；
4. 采集维护者运行 `Scripts/local/refresh.sh event-queue`；
5. Chess-Results 原始页面保存在仓库外；清洗后的结构化赛事数据与已发布副本比对合并后经 manifest 发布（full-data，旧 link-only 已退役）；
6. 只有符合来源许可、隐私和质量策略的数据才可能形成 release manifest；
7. 社区贡献者可进入鸣谢名录，但鸣谢不意味着其执行过数据抓取。

## 维护者采集控制

- 使用持久化跨进程锁，同一时间只有一个采集任务；
- 三个来源共享本地配额账本、全局间隔和熔断状态；
- FIDE 使用唯一临时下载、完整 ZIP/名单校验和多个 last-good 版本；
- Chess-Results 为 `full-data`：清洗后的结构化赛事数据与 PGN 经 manifest 发布；原始 HTML 永不公开；
- Lichess Broadcast 明确标注 CC BY-SA 4.0；
- 失败运行不形成发布 manifest；
- Git 与 CI 只处理 manifest 精确列出的路径和 SHA-256；
- `data/manual`、`data/community` 和原始网页路径被发布器硬拒绝。

## 争议、勘误与回滚

- 静态站点 Markdown 默认不发布；只有
  `Scripts/public_markdown_allowlist.txt` 明确列出的公共契约文档可进入部署包，
  评审、交付说明、采集手册和维护者运行文档一律保留在仓库侧；
- 赛事目录、数据覆盖和通用贡献向导当前不进入公共导航，并标记为
  `noindex,nofollow`；维护者仍可通过直达 URL 使用，相关构建与数据产出保持不变；
- 删除或匿名化请求不随通用贡献入口隐藏：首页页脚与棋手详情始终保留直达入口；
- 姓名错误必须写入 `name-corrections.csv`，不能只改生成 JSON；
- 联邦转入/转出必须写入 `federation-overrides.csv` 并附证据；
- 身份争议至少由两名审核者复核，未决项不得进入确定性身份层；
- 发布包异常时停止 ingest，保留 run-id、日志和私有诊断；
- 已发布数据被证伪时，通过人工纠错机制修正并在 changelog 留痕。

## 许可

代码采用 MIT。人工社区数据遵守 `LICENSE-DATA.md`；第三方来源按来源级 manifest
分别处理，不能用仓库默认数据许可覆盖其原始许可或条款。
