# SEO / GEO 维护与验收

公开页面由统一快照生成，禁止手改 `docs/data/seo/`。入口模板和 `seo.css/js` 修改会触发重建；部署前校验模板、生成器、页面正文哈希与 snapshot。代码提交直接触发的部署若发现旧快照会等待重建，不发布混合版本。

## 已接入的机制

- 主域：`https://chessdb.aigclabs.cc`；4chess / 正式 Pages 域名的 HTML 入口永久跳转，API/PGN 兼容保留。
- 核心内容生成到 `docs/data/seo/output/`，精确 manifest 将允许的内容复制到公开路径；内部生成目录不部署。
- 全量覆盖本站 FIDE 注册表，不按等级分或是否有棋谱筛选；无棋谱时明确说明。扩展至最多 1,100 个有公开成绩或棋谱的赛事、1,000 个有至少两条公开赛事记录的中文姓名目录。姓名目录不合并身份，不把展示聚合或同名记录认定为某个棋手。
- HTML 生成上限 14,500 页；实际装配的整个部署包超过 17,000 文件预警、超过 19,000 文件拒绝发布，保留免费版 20,000 上限前的增长缓冲。预算不足时必须先调整分配，禁止静默漏掉 FIDE 棋手。棋手/姓名/赛事均有可直接访问的分页目录。
- robots 与真实 404；首页和内容页 canonical / JSON-LD / 社交元信息；有内容的 sitemap。
- 旧棋手和赛事链接仅对已生成实体跳转，轮次定位与显式交互模式保持原路由。任意站内查询 noindex；静态规范页可收录。
- `lastmod` 只随语义正文变化。IndexNow 在部署及普通网址验证完成后通知差异 URL，按每批最多 10,000 URL 通知，保存接收或待重试回执；它不代表搜索引擎收录。
- 每次部署保存 `seo-verification` artifact：SEO 线上检查、IndexNow 回执、技术基线及 30 条固定评估查询。

## 常用验证

```bash
python3 Scripts/validate_seo_pages.py
python3 Scripts/verify_seo_online.py --output /tmp/seo-canary.json
python3 Scripts/seo_observation_report.py --output /tmp/seo-observation.json
```

以上命令从完整发布 checkout 读取对应快照。精简代码工作区缺少派生数据时，在云端构建或隔离导出的同版本数据上验证；不要把采集工作区拉取成最新 main。

`notify_search_engines.py` 默认只形成待通知差异，`--submit` 才通知 IndexNow。失败回执中的 `pendingURLs` 是失败批次的精确重试集合（旧回执兼容 `changedURLs`），不要为通知失败重新采集或全量重建。可在对应发布版本运行 `--retry-receipt <indexnow-receipt.json> --submit --output <new-receipt.json>` 精确重试；不会重新抓来源或重建。

## 外部平台接入

Google Search Console、Bing Webmaster、百度搜索资源平台需要维护者的已验证站点权限。当前项目不存放这些账号的凭据；未接通时观测值为 null，不能填 0。

1. 验证主域产权；优先 DNS 验证，或将平台给出的验证文件/标签以精确文件加入发布。
2. 提交 `/sitemap.xml`，分别抽查首页、棋手、赛事、排行榜、年度页的抓取与渲染。
3. 记录上线前后的 28 天搜索曝光、点击、排除原因；查看账号可用的 AI 效果报告。平台定义、地区、日期范围须随导出保存。
4. 将平台导出整理为 JSON 后通过 `seo_observation_report.py --platform-export <file>` 合并报告。该报告标明是维护者导出，不冒充 API 自动采集。
5. 用 artifact 中的固定问题测试实际 AI 搜索，记录产品/模式/地区、引用 URL、事实准确性与日期；不把人工样本当全网排名。

Cloudflare WAF、机器人策略、Web Analytics 需要相应的 API 或控制台权限；Pages 部署令牌不一定有这些权限。只能在核验真实机器人身份后调整规则，不能单凭 User-Agent 全局放行。当前 robots 保留公共访问默认，不额外更改训练许可政策。

## 验收口径

工程交付：代码测试、重建、部署、普通 URL 正文/跳转/404/站点地图与 PGN 回读全部通过。

运营效果：收录、曝光、引用和下载行为需要真实平台数据及观察周期；未取得数据不得宣称增长。浏览器实验室检查也不等于真实用户 Core Web Vitals 达标。
