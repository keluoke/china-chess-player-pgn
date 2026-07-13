# 贡献指南

这是一个社区共建的中国国际象棋棋手数据库。欢迎任何人提交数据修正、补充棋手信息或报告错误——多数贡献**只需在 GitHub 网页上编辑文件**,不需要本地环境。治理机制(角色、数据分层、审核与鸣谢)见 [docs/GOVERNANCE.md](docs/GOVERNANCE.md)。

## 不安装任何工具：网页贡献向导

打开网站首页的 **「网页贡献向导」**，即可填写赛事 tnr、赛事中文名、无 FIDE 棋手参赛记录或身份线索。向导通过 GitHub 设备码授权后直接创建结构化 Issue，不需要复制 JSON；授权失败时才退回到已预填内容的 Issue 页面。该方式不要求 Python 或 Git。

网页向导只负责整理线索，不会自动合并同名棋手。维护者仍会按证据核验后，将确认结果写入 `data/manual/` 或 `data/community/`。

## 最简单的贡献:一键抓数据(推荐)

Chess-Results 限制每个 IP 每天约 2000 次访问,维护者一台机器抓不完全部棋手。
你可以把自己的额度借给数据库,全程无需命令行:

1. 优先使用网站的[网页贡献向导](https://china-chess-player-pgn.pages.dev/contribute.html)，授权 GitHub 后直接创建数据 Issue;
2. 需要抓取完整赛事时，从[独立工具 Releases](https://github.com/keluoke/china-chess-contributor/releases/latest)下载对应系统版本，无需克隆本仓库或安装 Python;
3. 浏览器会打开本地页面:填一个昵称 → 输入想抓的棋手 FIDE ID 或赛事 tnr 号 → 点「开始抓取」;
4. 点「自动提交」,首次会让你在 GitHub 页面输入一个 8 位码完成授权,之后工具自动开 PR;
   没有 GitHub 账号就点「打包 zip」,把 zip 传到 Issue 里;
5. CI 离线甄别 → 维护者核验入库 → 你的昵称进入网站首页的「社区数据贡献鸣谢」。

抓谁最有价值:自己孩子/学生参加过的赛事(tnr 号就在 chess-results 链接里)、
排行榜上还没有赛事记录的棋手。流程细节见 `data/incoming/README.md`。

## 目录边界(改哪里?)

| 目录 | 性质 | 能否手工编辑 |
|---|---|---|
| `data/manual/`、`data/community/` | 人工/社区数据 | ✅ 通过 PR 编辑 |
| `data/incoming/` | 贡献工具载荷暂存 | 🤖 由贡献工具提交,勿手改 |
| `data/generated/` | 爬虫机器产出 | ❌ 不要手改,会被下次抓取覆盖 |
| `docs/data/`、`docs/api/` | 构建产物(索引/API) | ❌ 由 Actions 自动重建 |

## 四种贡献路径

### 1. 修正/补充中文名与别名

编辑 `data/manual/player-aliases.csv`,一行一名棋手:
`fide_id,chinese_name,pinyin_name,latin_name,aliases(竖线分隔),source_url,notes`。
中文名必须是合理人名(2-6 个汉字,可含民族名字的"·"),`source_url` 必填且域名需在白名单内(chess-results.com、ratings.fide.com、lichess.org、官方棋协域名等)。

### 2. 申报转会棋手

编辑 `data/community/federation-overrides.csv`(列说明见 `data/community/README.md`)。
`evidence_url` 必填,推荐用 FIDE 档案页。每次注册表更新会自动生成
`data/generated/transfer-candidates.json`(上月在、这月不在的棋手),从那里认领待核对条目是最有价值的贡献。

### 3. 登记无 FIDE ID 的小龄棋手(sighting)

在 `data/manual/domestic-player-sightings.csv` 追加行。证据分层:

| 层级 | 证据 | 置信度 |
|---|---|---|
| L1 | Chess-Results 赛事 URL(带 tnr 编号) | 高 |
| L2 | 赛事成绩册 PDF / 官方公告 | 中-高 |
| L3 | 俱乐部/棋院公告 | 中 |
| L4 | 棋手/家长自述 | 低 |
| L5 | 人工记忆 | 需交叉验证 |

**每条 sighting 必须锚定至少一个 L1/L2 证据**。

### 4. 关联身份(identity link)

在 `data/manual/player-identity-links.csv` 提议"两条记录是同一人"。**同名消歧规则:至少满足以下 2 项**才可标 `probable`,仅 1 项标 `speculative`(不进生产库):

1. 俱乐部/学校一致;2. 出生年份一致(±1);3. 同省市;4. 姓名生僻字一致(强证据);5. 参赛编号跨赛事一致;6. 教练/家长确认(注明来源)。

## 流程

1. Fork 或直接在 GitHub 网页编辑 → 提交 PR。
2. CI 自动校验格式(FIDE ID、人名规则、URL 白名单、重复检测),失败会在 PR 里说明原因。
3. 带 chess-results 证据的条目会被标 `needs-local-verification`,由维护者在住宅 IP 环境回抓核验(GitHub 的服务器被 chess-results 封锁,CI 无法自行核验)。
4. 维护者 approve 后合并;合并即触发索引重建与网站/API 部署,你的贡献几分钟内上线。

报告错误不想提 PR?直接开 Issue(有现成模板),说明棋手 FIDE ID、哪个字段有误、正确值和证据链接即可。

## 许可

提交即表示你同意:代码按 MIT、数据按 CC BY 4.0(见 LICENSE / LICENSE-DATA.md)授权。
