# 本地数据抓取管线操作指南

## 当前流程

```text
住宅网络本机抓取
  → 私有 raw / 逐页检查点
  → 本地清洗与完整性校验
  → 不可变 outbox + SHA-256 manifest
  ├→ GitHub local-data → main ingest → 离线 rebuild → Cloudflare Pages（生产）
  └→ 鉴权 Worker → R2/D1/Queue → 影子快照与回执（可选、非生产）
```

数据不是默认直接写入 Cloudflare 生产。Git 生产链路仍承担代码/人工数据边界、
当前 main 基线、可审计提交和离线重建；Cloudflare ingest 目前只做独立影子双写。

## 日常操作

1. 双击根目录“`一键抓取面板.command`”，或运行：

   ```bash
   python3 Scripts/local/panel.py
   ```

2. 先点“环境健康检查”。只有来源直连、私有状态区和发布路径预检通过后再抓取。
3. 在“抓取指定赛事”粘贴 TNR、`tnr123456`、Chess-Results URL，或从表格复制的
   逗号/空白分隔文本；面板会在后端再次校验、去重，每批最多 10 场。
4. 点击开始后观察逐场状态。`partial` 可续跑缺页，`retry-wait` 等待退避到期，
   `quarantined`/`unsupported` 需要检查保存的私有证据或更新解析器；不要用重抓
   代替发布重投。
5. “GitHub 生产自动推进”默认开启。面板把 outbox 依次推进到
   `online-verified`；只有这个状态才表示生产线上已确认。
6. 若要参与 Cloudflare 影子对账，单独开启“Cloudflare 自动影子双写”并确认授权。
   它不会切换生产读取，也不会替代 GitHub 回执。

## 发布与故障恢复

只重投现有 outbox，不访问任何数据源：

```bash
bash Scripts/local/refresh.sh publish
bash Scripts/local/refresh.sh receipts
```

若 GitHub 自动推进已暂停、只需继续已授权的影子回执：

```bash
bash Scripts/local/refresh.sh shadow-publish
```

显式回填某个历史包到影子服务：

```bash
bash Scripts/local/refresh.sh shadow-deliver -- <run-id>
```

- GitHub 网络失败：保留 `pending`，恢复后运行 `publish`，不要重新抓取。
- `RELEASE_BASE_CONFLICT`：整包隔离，人工核对 baseline/current/candidate。
- 影子 `ineligible`：逻辑包超过 384 文件、96 MiB 或单文件 96 MiB；按契约不上传。
  16 MiB 以上的合格文件自动使用 8 MiB multipart 传输片，合成完成前不形成快照。
  384 文件以内由客户端和 Queue 自动按 10 文件分块处理，但对外仍是一个原子快照；
  GitHub 生产继续。
- 影子 `conflict`/`failed`：只处理影子回执，不回滚已验证的 GitHub 生产阶段。

## 切流条件

Cloudflare 影子只有在连续 7 天、至少 20 个合格真实包逐路径 SHA-256 对账一致，
并完成幂等、乱序、冲突、Queue 重试、配额耗尽、回滚和原始 URL 验证后，才允许
另行评审生产切流。在契约状态仍为 `shadow` 时，面板和脚本不得绕过 Git。
