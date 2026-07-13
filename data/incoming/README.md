# data/incoming/ — 社区目标线索暂存区

社区可以提交“应该由维护者采集什么”，但不能提交任何自动抓取结果。本目录仅接受
目标 URL、Chess-Results tnr、FIDE ID、缺失原因和优先级提示。

禁止提交：

- HTML、WARC、截图式网页存档；
- PGN、棋局切分文件；
- 抓取后生成的 rows/standings/pairings/games；
- cookies、请求/响应头、代理或访问额度信息；
- 未成年人联系方式或其他敏感身份材料。

每个提交目录只能包含一个 `manifest.json`：

```json
{
  "schema": "china-chess-target-submission/v2",
  "contributor": {"nickname": "可选昵称", "github": "optional-user"},
  "targets": [
    {
      "type": "event-target",
      "tournamentID": "1110333",
      "sourceURL": "https://chess-results.com/tnr1110333.aspx?lan=1",
      "reason": "赛事缺失",
      "priority": 70
    }
  ]
}
```

支持的 `type`：`event-target`、`player-target`、`source-clue`、
`quality-report`。每次 1-20 条，manifest 最大 64 KiB。

CI 的 `Scripts/validate_incoming.py` 会拒绝额外附件、抓取产物字段、HTML/PGN 内容、
凭据 URL 和公开联系方式。通过审核的线索由维护者加入人工目标队列，再通过
`Scripts/local/refresh.sh event-queue` 在本机私有采集；本目录本身永不晋升为数据。
