# 本地抓取（Scripts/local）

chess-results.com 和 ratings.fide.com 会封 GitHub Actions 的数据中心 IP，所以**抓取放在本地跑**（住宅 IP），**索引和部署交给 GitHub Actions**。

## 用法

```bash
# 常规增量刷新：先更新 FIDE 注册表，再爬 Chess-Results 赛事
bash Scripts/local/refresh.sh all

# 单项
bash Scripts/local/refresh.sh registry      # 下载 FIDE 榜、重建 CHN 注册表
bash Scripts/local/refresh.sh crawl          # 爬棋手赛事 + 抓 PGN
bash Scripts/local/refresh.sh events         # 起始排名名字 + 整赛事 PGN
bash Scripts/local/refresh.sh aliases        # 抓中文名并入注册表
bash Scripts/local/refresh.sh promote        # 晋升可公开 PGN
bash Scripts/local/refresh.sh reconcile      # 核对覆盖 + 补抓
bash Scripts/local/refresh.sh bulk           # 镜像 Lichess broadcast 分片
bash Scripts/local/refresh.sh pgn            # 补抓缺失赛事 PGN

# 只提交、不推送（不触发 Actions）
bash Scripts/local/refresh.sh crawl --no-push

# 透传参数给底层脚本（-- 之后原样传入）
bash Scripts/local/refresh.sh crawl -- --player 8622388

# 本地纯重建索引（一般不用，Actions 会做；无网络）
bash Scripts/local/refresh.sh reindex
```

## 流程（免 pull 设计）

本机**永不 pull / rebase**，与远端的唯一交互是一次 force-push：

1. 脚本在本地跑对应的网络抓取脚本（直连，不走代理）。
2. 提交**原始**抓取数据到本地历史。
3. `git push --force` 到单写者分支 `local-data`——强推永不被拒，无需拉取远端。
4. GitHub 上 `ingest-local-data.yml` 把该分支的数据目录镜像进 main（bot 身份），
   随后 `rebuild-indexes.yml` 重建全部派生索引并触发 `deploy.yml` 发布双端。

即：**抓取 = 本地；合入 + 索引 + 部署 = Actions。** 冲突在结构上不可能：
本机是原始数据唯一生产者，且独占 local-data 分支。

git push 连不上 GitHub 时自动探测代理：macOS 系统代理（Veee/Clash 等登记的）→
环境变量 → 常见本地端口；也可 `GITHUB_PROXY=http://127.0.0.1:端口` 指定。
推送失败时数据已提交在本地，之后任意时刻选「push」补推即可。

## 前置

- 已装 `python3`，并按需 `pip install certifi python-chess`。
- 已 clone 仓库并配置好 `git push` 权限。
- 首次可 `chmod +x Scripts/local/refresh.sh` 后直接 `./Scripts/local/refresh.sh ...`。

## 也可用自托管 runner

抓取类 workflow 仍保留在 `.github/workflows/`，但已改为 `runs-on: [self-hosted]` + 仅手动触发。如果你挂了自托管 runner，也可以直接在 GitHub UI 上手动 dispatch，效果与本地脚本一致（抓取→提交→触发 rebuild-indexes）。
