# Chess-Results 赛事采集（维护者私有模式）

旧版全量 SpielerSuche/PGN 公开抓取流程已经退役。`crawl_player_events.py` 和
`fetch_event_pgn.py` 仅为历史兼容或未来取得明确授权后的工具，默认受到
`COMPLIANCE_POLICY_BLOCKED` 保护。

当前入口：

```bash
bash Scripts/local/refresh.sh event-queue -- --from-queue 3
bash Scripts/local/refresh.sh event-queue -- 1110333
```

它读取社区/人工维护的目标队列，并将 HTML、解析排名和轮次写到仓库外
`runs/<run-id>/raw|extracted`。全局配额账本提供跨线程/跨进程限速、每日预算、
指数退避和熔断。默认 link-only，不生成公开 PGN、别名或赛事数据库。

社区只提交 URL、tnr、FIDE ID 和缺失原因，不运行本脚本、不上传抓取结果。
