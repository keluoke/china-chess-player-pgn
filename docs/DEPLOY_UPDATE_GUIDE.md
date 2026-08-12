# 数据更新与部署指南

## 网络采集

GitHub Actions 不提供任何抓取 workflow。维护者在本机运行：

```bash
bash Scripts/local/refresh.sh health
bash Scripts/local/refresh.sh all
```

FIDE/Lichess/Chess-Results 通过校验后生成精确 release manifest 并 force-push
`local-data`。Chess-Results 原始页面只写本地私有运行区；通过完整性门禁的
清洗后结构化赛事数据随 manifest 发布，由云端 ingest 比对合并（设计基线见
`docs/EVENT_DATA_COMPLETENESS_BASELINE.md`）。

## 云端流程

1. `ingest-local-data.yml` 验证 manifest、来源策略、禁止路径和 SHA-256；
2. 只应用 manifest 中列出的文件，不镜像宽目录；
3. `rebuild-indexes.yml` 在 GitHub-hosted runner 离线重建索引/API；
4. `deploy.yml` 发布静态站点。

人工 `data/manual` / `data/community` PR 合并后也只触发离线重建，不访问来源。

## 故障恢复

- 来源失败：使用面板错误码处理，成功的独立阶段不会丢失；
- 投递失败：运行 `bash Scripts/local/refresh.sh publish`，不重新抓取；
- 校验失败：检查对应 `runs/<run-id>/run.log` 和 staging，禁止手工绕过 manifest；
- 页面部署失败：只重跑离线 rebuild/deploy workflow，不运行任何抓取命令。
