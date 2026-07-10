# data/incoming/ — 社区抓取载荷暂存区

社区贡献工具(`Scripts/contrib/contrib_tool.py`,双击仓库根目录的
「贡献工具-双击启动」)抓取 Chess-Results 数据后,自动以 PR 形式把载荷提交到
本目录:`data/incoming/<submission-id>/`。

**这里是暂存区,不是正式数据。** 正式入库路径:

```text
贡献者本地抓取(住宅 IP,分摊 2000 visits/day 限制)
  → PR 到 data/incoming/<id>/(manifest + 解析结果 + 原始 HTML/PGN 证据)
  → CI 离线甄别(Scripts/validate_incoming.py:sha256、证据重解析逐行比对、
    PGN 重切逐字节比对;CI 无法访问 chess-results,不做在线核验)
  → 维护者合并 PR
  → 维护者本地 `refresh.sh contrib`(promote_incoming.py --verify 抽查回抓)
  → 并入 data/generated / docs/data/pgn,贡献者记入
    data/community/contributors.csv 鸣谢名录
  → reindex 重建派生索引 → 网站上线,鸣谢名录展示昵称
```

## 载荷结构

```text
<submission-id>/               # YYYYMMDD-HHMMSS-hex6
├── manifest.json              # 贡献者、目标、统计、每个文件的 sha256
├── players/<fideID>/
│   ├── rows.json              # SpielerSuche 解析出的参赛记录
│   └── spielersuche.html.gz   # 原始响应快照(证据)
└── events/tnr<ID>/
    ├── raw.pgn.gz             # 赛事原始 PGN(证据)
    └── split/fide-*.pgn       # 按中国棋手切分的对局
```

规则:载荷 PR 不得修改本目录以外的任何路径(CI 强制);单载荷 ≤ 25 MB;
已入库的载荷记录在 `data/generated/contrib-processed.json`,可由维护者清理。
