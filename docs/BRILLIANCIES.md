# 实战妙手

产品入口 `/brilliancies`；公开 API 见 [API 文档](API.md) 与 `/api/v1/brilliancies/openapi.json`。

## 数据与发布

人工审核记录在 `data/manual/brilliancies/curated.json`。每条绑定原始 PGN 的 SHA-256、规范化棋局指纹、半回合位置与实际招法。注册表决定棋手展示姓名。`!!` 是本站精选标记，不是其他平台评级，也不保证唯一最佳着法。

`Scripts/build_release_snapshot.py` 统一构建分片、列表、片段 PGN、API 和 SEO，再验证原局回放、SAN、完整 FEN、实际续着、分析合法性及署名。GitHub main → 统一重建 → R2 认证 → Pages 部署；前端与只读 Functions 使用同一快照，无新增数据库。单项分享使用查询参数；列表可收录，查询页不重复收录。

候选分析是离线工具，只读已归档 PGN，不抓取来源，不自动发布：

```sh
python3 Scripts/local/analyze_brilliancies.py --input /absolute/path/to/archive.pgn --limit-games 100
```

默认结果写入用户私有运行目录 `~/Library/Application Support/ChinaChessPlayerPGN/brilliancies/candidates.json`。候选不是已审核内容；人工核对原局、引擎、文字和许可后才能进入 curated。当前精选集不代表全库已扫描，扫描覆盖记为未计量。

## 维护

撤回条目保留 ID 和原局引用，将状态改成 `withdrawn` 后走同一发布链：列表/SEO 移除、片段清理、API 返回 410。分析数值反映记录中的引擎与预算；真实实战续着和引擎变化分开显示。

验证：`python3 -m unittest Scripts.tests.test_brilliancies Scripts.tests.test_seo_pages`、`node Scripts/tests/brilliancies_api_test.mjs`、`node Scripts/tests/test_seo_middleware.mjs`；完整交付仍需统一构建和线上原局跳转验收。
