# mimic-engine — 棋手模仿引擎原型

用某位棋手的对局集合成一个模仿该棋手的 UCI 引擎。首个原型基于
**Zhang, Hongya（FIDE 8640980，2014 年生，标准分 2064，90 局）**。

## 三层架构

1. **开局库**：`build_player_profile.py` 从棋手 PGN 生成 polyglot
   `white.bin` / `black.bin`，只收录棋手自己走的着法，权重 = 频率 ×
   结果加成（胜 1.6 / 和 1.0 / 负 0.6）× 近期加成。
2. **兜底引擎**：出库后由 Maia3 UCI 或限强 Stockfish 出 multiPV 候选。
   Maia3 走 policy-first 排序，更接近人类着法分布；Stockfish 使用
   `UCI_LimitStrength` + `UCI_Elo`，适合作为无 Maia3 环境的稳定 fallback。
3. **风格与失误模型**：
   - `build_player_profile.py` 统计非引擎风格项（换子率、将军率、
     长/短易位比例、后交换时机、局长）→ `style.json`
   - `analyze_phases.py` 用 Stockfish 标注每一步，产出**分阶段**
     ACPL / 漏着率 / 与引擎第一选择吻合率，驱动模仿引擎按阶段注入
     人类化失误（开局稳、残局飘的真实曲线）。

## 用法

```bash
# 1. 生成画像（秒级）
python3 build_player_profile.py \
  --pgn ../docs/data/pgn/by-player/fide-8640980/all.pgn \
  --player "Zhang, Hongya" --fide-id 8640980 \
  --out-dir profiles/fide-8640980

# 2. 引擎标注分阶段失误画像（分钟级，可选但推荐）
python3 analyze_phases.py --pgn <同上> --profile profiles/fide-8640980 \
  --stockfish /path/to/stockfish

# 3a. 作为 UCI 引擎接入任意 GUI：Maia3 后端
# 需要本机已有 maia3-uci；默认模型为 maia3-5m，并启用 UCI history。
python3 mimic_uci.py --profile profiles/fide-8640980 --backend maia3

# 3b. Stockfish 限强 fallback
python3 mimic_uci.py --profile profiles/fide-8640980 \
  --backend stockfish --stockfish /path/to/stockfish

# 4. 冒烟测试
python3 play_test.py --profile profiles/fide-8640980 --stockfish /path/to/stockfish
```

UCI 选项：`Backend`（`maia3` / `stockfish`）、`MimicElo`（默认
2050）、`MultiPV`、`BookTemp`（开局库采样温度，越低越贴近高频谱系）、
`StockfishPath`、`Maia3Command`、`Maia3Model`、`Maia3CheckpointPath`。

## 批量生成网页版 profile

所有青少年棋手的浏览器 profile 由仓库根目录脚本生成：

```bash
python3 Scripts/build_youth_mimic_profiles.py
```

脚本读取 `docs/data/index/by-player/players.json` 和
`docs/data/pgn/by-player/fide-*/all.pgn`，按 `stages` 或出生年识别青少年
棋手。原始 polyglot/style profile 默认放在 `.build/mimic-profiles/`，只把
浏览器实际需要的 `profile.js` 输出到：

```text
docs/mimic/profiles/fide-<FIDE_ID>/profile.js
docs/mimic/profiles/manifest.json
docs/data/mimic/profiles/manifest.json
```

manifest 记录每个 PGN 的 SHA-256；再次运行时只重建 PGN 变化或缺失的棋手。
GitHub Actions 的 `Update mimic profiles` workflow 每周运行，也可手动传入
`player_fide_id` 单独刷新某位棋手。

## Zhang Hongya 画像速览

白棋 39/40 局走 1.e4（对西西里主用 2.Nf3、偶用 2.Nc3）；黑棋对 1.e4
主打西西里 ...c5 + ...e6 体系；场均 85 回合半的缠斗型，67% 对局交换皇后；
易位过的对局里约 1/3 是长易位。

## 已知限制（原型级）

- 90 局慢棋只够撑开局库和粗粒度风格向量；细粒度风格需关联其线上
  账号（lichess/chess.com）扩样本，仓库的 `player-identity-links.csv`
  即为此准备。
- Maia3 依赖本机或 CI 环境安装 `maia3-uci`；未安装时仍可切到 Stockfish
  限强后端。
- 风格加权是启发式，尚未用留出对局的着法命中率做过校准。
