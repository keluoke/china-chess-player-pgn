# ADR:大体积数据迁移至对象存储(R2)

- 状态:提议(待维护者开通 Cloudflare R2 后实施)
- 日期:2026-07-13
- 背景问题:静态 Git/Pages 架构即将触顶

## 背景

当前公开数据产物随 git 仓库分发并整体部署到 Cloudflare Pages:部署体积已达上限约 92%、文件数达 2 万上限的约 87%,本地 `.git` 约 2.6 GB。逐棋手 API 分片和 PGN 归档是公开部署的主要增长源；仓库历史中的 Chess-Results 快照则是待迁出的遗留私有材料，新采集已不再进入 git。

## 决策

数据按"人工/机器、小/大"两个维度切分归属:

| 数据 | 体积 | 去向 |
|---|---|---|
| 人工与社区表(`data/manual`、`data/community`) | KB 级 | **留 git**(评审与追溯必需) |
| manifest、注册表、索引、API JSON、audit | ~250 MB | **留 git + Pages**(小、纯计算可重建) |
| `docs/data/bulk/`(Lichess 分片,836 MB) | 大 | **迁 R2** |
| `docs/data/pgn/`(481 MB,由 by-player 聚合层服务前端) | 大 | **迁 R2** |
| 历史 Chess-Results 快照 | 遗留 git 体积 | **迁维护者私有归档后从 git 移除**；不得上传公开 R2，也不公开对象路径清单 |
| `docs/api/v1/players/`(逐棋手分片,文件数大户) | 文件多 | 第二阶段迁 R2 或合并分片 |

选 R2 的理由:与 Pages 同属 Cloudflare,同域路由零 CORS 问题;出站流量免费;S3 兼容 API 便于脚本上传;免费额度(10 GB)足够起步。

## URL 稳定性(对 API 消费者零破坏)

API v1 的契约是**路径由 manifest 给出**,消费者不硬编码。实施时:

1. R2 桶绑定自定义域或经 Pages 路由(`/data/pgn/*` → R2),**现有 URL 原样保留**;
2. 每个 manifest/packages 条目已带 `sha256`,迁移前后校验一致;
3. 过渡期 Pages 与 R2 双写一个版本周期,比对访问日志无 404 后再删 Pages 侧副本。

## 实施步骤(半天)

1. 开通 R2,建桶 `chess-data`,绑定 `data.<域名>` 或配置 Pages 路由;
2. `rclone`/`wrangler r2` 只上传许可允许公开的 bulk + pgn（带 sha256 元数据）；
3. 上传脚本进 `refresh.sh`:commit 前把新增大文件同步 R2,git 里以清单(路径+sha256+字节数)替代实体;`.gitignore` 加入已迁移目录;
4. `prepare-static-site` action 排除已迁移目录;CI 加"部署体积/文件数预算"检查(阈值 80% 报警);
5. 将历史 Chess-Results 快照迁入访问受控的维护者私有归档，再从工作树移除；不得把私有对象键写入公开 manifest；
6. git 历史瘦身(可选,最后做):`git filter-repo` 移除历史大 blob,或全新浅仓库 + 旧仓库归档只读。

## 风险与回滚

- R2 故障:公开数据在本地与 git 历史(迁移前 tag)双备份,可临时回切 Pages 直服小子集;
- 私有归档故障:Chess-Results 证据只从维护者加密备份恢复，不回退到 git/Pages/R2 公开分发；
- 大陆可达性:R2 走 Cloudflare 边缘,与 Pages 同一网络,不引入新的可达性变量;
- 唯一不可逆动作是第 5 步历史重写——放最后,且强制先打归档 tag。

## 不做什么

不迁人工表(评审流程依赖 PR diff);不把 Chess-Results 原始/解析材料放进公开对象存储;不引入数据库或动态服务(纯静态原则不变,R2 只是获准公开内容的"更大的静态盘");不改 API 版本(v1 契约不动)。
