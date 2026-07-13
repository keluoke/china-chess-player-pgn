# 维护者本地数据采集

所有网络采集只能由维护者在本机住宅网络执行。GitHub Actions、社区贡献工具和
公开 runner 不访问 Chess-Results、FIDE 或 Lichess；CI 只接收经过校验的发布
manifest，并离线重建派生索引。

## 数据边界

- `data/community/`、`data/manual/`：人工维护，抓取脚本禁止写入或自动提交。
- `data/generated/`：允许发布的机器投影；禁止存放 Chess-Results 原始 HTML。
- 本地私有运行区：原始响应、解析结果、日志、配额和诊断文件，默认位于
  `~/Library/Application Support/ChinaChessPlayerPGN/`。
- Chess-Results 默认 `link-only`：页面、排名、配对、别名候选和 PGN 不进入公开
  仓库。即使棋步属于事实，也不等于获得了数据库提取或再发布授权。
- Lichess Broadcast 数据保留 CC BY-SA 4.0 名称、许可证 URL 和来源署名。
- registry 是姓名和等级分的唯一权威；`name-corrections.csv` 在每次 FIDE 重建中
  最后强制应用，并在发布前再次断言。

## 控制面板

双击仓库根目录的「一键抓取面板.command」或「一键抓取.app」，也可运行：

```bash
python3 Scripts/local/panel.py
```

面板只监听 `127.0.0.1`，POST 请求带随机本地令牌。任务锁、run-id、状态和日志
都持久化在仓库外；关闭或重启面板不会遗失正在运行的任务。

## 安全命令

```bash
# 建议先运行；默认探测三个来源，--offline 只检查本机
bash Scripts/local/refresh.sh health
bash Scripts/local/refresh.sh health -- --offline

# 安全常规刷新：FIDE 满 25 天才更新，并私有采集队列前 3 个赛事
bash Scripts/local/refresh.sh all

# FIDE：唯一临时下载 -> ZIP/语义/人数/分片/勘误校验 -> 原子晋升
bash Scripts/local/refresh.sh registry

# Chess-Results：只写仓库外私有运行区
bash Scripts/local/refresh.sh event-queue
bash Scripts/local/refresh.sh event-queue -- --from-queue 10
bash Scripts/local/refresh.sh event-queue -- 1110333
bash Scripts/local/refresh.sh candidates -- --tournament-id 1110333

# Lichess：暂存、验证、BY-SA manifest、精确发布
bash Scripts/local/refresh.sh bulk
bash Scripts/local/refresh.sh bulk-full

# GitHub 网络失败后只重投已提交发布包，不重新抓取
bash Scripts/local/refresh.sh push

# 仅本地诊断，不自动提交或推送
bash Scripts/local/refresh.sh reindex
```

`crawl*`、`pgn*`、`events*`、`aliases`、`promote`、`reconcile`、`verify` 和
`contrib` 已从一键入口退役。社区只提交赛事 URL/tnr、优先级、身份勘误、联邦
变更证据及质量问题，不提交自动抓取文件。

## 发布事务

每次运行创建独立目录：

```text
runs/<run-id>/
  run.json
  run.log
  raw/
  extracted/
  staging/
  diagnostics/
  release-manifest.json   # 仅存在于可发布运行
```

FIDE/Lichess 发布前必须满足：

1. 发布归属路径和 Git 暂存区在运行前是干净的；
2. 下载写入唯一临时文件，长度、文件签名和内容校验通过；
3. 输出先写 staging，再原子晋升或逐文件原子 overlay；
4. `run_manager.py` 生成精确路径、操作和 SHA-256 manifest；
5. Git 只暂存 manifest 中的文件，不再执行宽目录 `git add`；
6. force-push 到单写者 `local-data`；
7. `ingest-local-data.yml` 使用同一验证器，只把 manifest 文件清单应用到 main；
8. Actions 离线重建索引和部署。

任何 `data/manual`、`data/community`、`data/incoming`、原始 HTML/WARC 路径都会
被本地发布器和 CI 双重拒绝。

## 常见错误码

- `DIRTY_RELEASE_PATH`：机器发布路径已有未提交修改，工具不会覆盖。
- `FIDE_DOWNLOAD_OR_VALIDATION_FAILED`：新文件无效；有效 last-good 不会被替换。
- `SOURCE_CIRCUIT_OPEN`：连续失败后熔断，等待后再试。
- `VISIT_BUDGET_EXHAUSTED`：当日来源预算已耗尽。
- `PARSER_LAYOUT_CHANGED`：来源页面不再符合解析器预期。
- `VALIDATION_REGRESSION`：人数、分片、勘误或解析行数出现异常。
- `COMPLIANCE_POLICY_BLOCKED`：操作违反 link-only 或人工数据边界。
- `GIT_PUSH_FAILED`：数据已按 manifest 提交，可直接运行 `push`。

## 本地不 pull

采集机仍然不执行 pull/rebase。GitHub 推送自动尝试直连、macOS 系统代理和常见
本地代理端口；代理只传给 Git，不传给数据来源请求。代码修改不能通过
`local-data` 发布，仍应走普通短命 PR 分支。
