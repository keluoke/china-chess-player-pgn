# 社区治理机制

本仓库是社区共建的中国国际象棋棋手数据库。治理目标:**数据正确性优先、
证据可追溯、贡献门槛尽量低、维护者单点压力尽量小**。

## 角色

| 角色 | 权限与职责 |
|---|---|
| 访客 | 浏览网站与 API;报错开 Issue(有模板) |
| 数据贡献者 | 优先用网页向导直接开结构化 Issue;完整抓取用独立工具提交载荷 PR |
| 审核者/维护者 | 审 PR、跑本地核验、入库、管理白名单与勘误层 |

新增审核者由现任维护者邀请;标准:多次高质量贡献 + 熟悉数据边界规则。

## 数据分层与写入权(谁能改什么)

| 层 | 路径 | 写入方 |
|---|---|---|
| 注册表(权威) | `docs/data/registry/` | 仅维护者爬虫 |
| 机器产出 | `data/generated/` | 仅爬虫与 promote 脚本 |
| 人工/社区数据 | `data/manual/`、`data/community/` | PR(CI 校验) |
| 贡献载荷暂存 | `data/incoming/` | 贡献工具 PR(CI 甄别) |
| 派生索引/网站 | `docs/data/`、`docs/api/` | 仅 Actions 重建 |

铁律见 `AGENTS.md`:注册表是姓名与等级分唯一权威;勘误进
`data/community/name-corrections.csv` 并由 CI 钉死;派生层禁止反写。

## 抓取额度的分摊(为什么需要社区抓取)

Chess-Results 限制约 2000 visits/day/IP,且封锁数据中心 IP(含 GitHub
Actions)。维护者一台机器跑不完 1.1 万名棋手的增量。因此:

- **维护者机器**:跑全量增量(registry / crawl / bulk);
- **贡献者机器**:用贡献工具按需抓「某棋手 / 某赛事」,各自消耗自己的住宅
  IP 额度(工具内置 1.5s 间隔、单次 ≤20 个目标、每日软上限提醒);
- **CI**:永不抓 chess-results,只做离线校验与重建。

## 贡献载荷的生命周期(甄别 → 入库 → 鸣谢)

1. **抓取**:贡献者从独立工具仓库的 Release 下载免 Python 版本,输入 FIDE ID 或 tnr 赛事号,
   工具抓取并同时归档原始 HTML / 原始 PGN 作为证据;
2. **提交**:GitHub 设备码授权后自动 fork + PR 到 `data/incoming/<id>/`;
   无 GitHub 账号可打 zip 包由维护者代交;
3. **CI 甄别(离线,自动)**:
   - manifest sha256 / 字节数逐文件比对,禁止载荷外改动;
   - 用仓库同款解析器重新解析 HTML 快照,与 rows.json 逐行比对;
   - 用同款切分逻辑重切 raw.pgn,与 split/*.pgn 逐字节比对;
   - FIDE ID 必须在注册表;昵称合规;≤25 MB。
   伪造解析结果而不同时伪造出能通过同一解析器的证据,成本极高;
4. **人工核验(维护者)**:合并 PR 后本地 `refresh.sh contrib`,
   `promote_incoming.py --verify` 在住宅 IP 抽查回抓比对,防"精心伪造证据";
5. **入库**:载荷并入 `data/generated` 与 `docs/data/pgn`(与维护者爬虫
   同一套 absorb 逻辑),中文名证据照常过 `sanitize_person_name` 与勘误层;
6. **鸣谢**:贡献者昵称(+可选 GitHub 名)累计进
   `data/community/contributors.csv`,网站首页「社区数据贡献鸣谢」展示。

## 争议与回滚

- 数据争议:开 Issue 引用证据;两名审核者意见一致即可裁定;
- 已入库数据被证伪:走 `name-corrections.csv`(姓名类)或直接修正
  `data/generated`+ 重建(记录类),并在 changelog 中留痕;
- 恶意载荷:关闭 PR、submission-id 加入 `contrib-processed.json` 拉黑,
  屡犯者由维护者在 GitHub 层面 block。

## 独立贡献工具

桌面工具源码、打包工作流与 Release 均位于
[`keluoke/china-chess-contributor`](https://github.com/keluoke/china-chess-contributor)。
主仓库不再携带工具源码和运行状态，避免用户为几十 MB 的工具下载 GB 级数据库。
网页向导复用同一个 OAuth App 的设备码流，隐私请求例外：永不创建公开 Issue。

## 许可

代码 MIT;数据 CC BY 4.0。提交载荷即表示同意按此授权,鸣谢名录即署名途径。
