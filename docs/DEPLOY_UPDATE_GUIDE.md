# 站点更新手册 (GitHub Pages + Cloudflare Pages)

本仓库的网页版就是 `docs/` 目录，被**两个**部署 workflow 同时发布到两个托管：

| Workflow | 目标 | 触发 | 产物 |
| --- | --- | --- | --- |
| `.github/workflows/pages.yml` | GitHub Pages | push 改到 `docs/**` 或手动 | `docs/` → `*.github.io` |
| `.github/workflows/cloudflare-pages.yml` | Cloudflare Pages | push 改到 `docs/**` 或手动 | `docs/` → `china-chess-player-pgn.pages.dev` |

两者内容完全一样，只是托管商不同。

## 数据流（谁改了 docs/）

数据类 workflow 抓取数据 → 重建 → commit 到 `docs/data/` → 部署 workflow 发布：

- 定时/手动：`update-pgn`、`promote-public-pgn`、`reconcile-pgn-sources`、
  `update-player-registry`、`update-domestic-registry`、`update-name-aliases`、
  `update-lichess-broadcast-bulk`、`update-event-archive`
- 全部通过 `Scripts/ci_commit_push.sh`（或内联等价逻辑）以 `github-actions[bot]` 提交。

⚠️ **重要机制**：用默认 `GITHUB_TOKEN` 的 bot 提交**不会**再触发 `pages.yml` /
`cloudflare-pages.yml`（GitHub 的防递归规则）。目前只有 `update-event-archive.yml`
在提交后显式 `gh workflow run` 补触发部署，其余数据 workflow **提交后不会自动重新部署**——
网页要等下一次「人类 push」或手动触发才会刷新。

## 最省成本的一次更新（不抓数据、不产生 commit）

站点内容没变、只想强制重新发布时，直接手动触发部署即可，**不要**去跑抓取类
workflow（那些会大量请求外部站点并产生巨大 diff）：

**网页操作**：GitHub → Actions → 选 `Pages`（或 `Cloudflare Pages`）→ Run workflow → 分支 `main` → Run。

**命令行**（需已安装并登录 `gh`）：
```bash
gh workflow run pages.yml --ref main
gh workflow run cloudflare-pages.yml --ref main   # 若也要刷新 Cloudflare
```

## 有真实数据改动时的更新

1. 本地或在 Actions 里跑对应的数据 workflow（例如 `Update static PGN archive`）。
2. 确认它 commit 了 `docs/data/`。
3. 若该 workflow 没有自带「Trigger site deploys」步骤（目前除 `update-event-archive`
   外都没有），**手动补一次上面的部署触发**，否则线上不会刷新。

## 手动本地推送（会自动触发部署）

只有**非 bot** 的 push 才会自动触发两个部署 workflow：
```bash
git add docs/
git commit -m "…"
git push origin main      # 触发 pages.yml + cloudflare-pages.yml
```

## 健康检查

- 部署失败多为 Pages 服务偶发的 `try again later`；`pages.yml` 已内置 60s 后重试一次。
- Cloudflare 需要仓库 Secret `CLOUDFLARE_API_TOKEN`；缺失时部署步骤会被跳过（不报错）。
- 单文件 > 24MiB（Pages）/ 25MiB（Cloudflare）会被卡住；产物上限 850MiB。
