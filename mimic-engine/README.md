# mimic-engine — 棋手模仿引擎原型

用某位棋手的对局集合成一个模仿该棋手的 UCI 引擎。首个原型基于
**Zhang, Hongya（FIDE 8640980，2014 年生，标准分 2064，90 局）**。

## 三层架构

1. **开局库**：`build_player_profile.py` 从棋手 PGN 生成 polyglot
   `white.bin` / `black.bin`，只收录棋手自己走的着法，权重 = 频率 ×
   结果加成（胜 1.6 / 和 1.0 / 负 0.6）× 近期加成。
2. **兜底引擎**：出库后由限强 Stockfish（`UCI_Elo` ≈ 棋手等级分）出
   multiPV 候选。**Maia 接入点**是 `mimic_uci.py` 里的
   `backend_candidates()`——换成 lc0+maia 的 policy 查询即可，其余不动。
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

# 3. 作为 UCI 引擎接入任意 GUI（Arena/Cute Chess/BanksiaGUI）
python3 mimic_uci.py --profile profiles/fide-8640980 --stockfish /path/to/stockfish

# 4. 冒烟测试
python3 play_test.py --profile profiles/fide-8640980 --stockfish /path/to/stockfish
```

UCI 选项：`MimicElo`（默认 2050）、`BookTemp`（开局库采样温度，越低越
贴近高频谱系）。

## Zhang Hongya 画像速览

白棋 39/40 局走 1.e4（对西西里主用 2.Nf3、偶用 2.Nc3）；黑棋对 1.e4
主打西西里 ...c5 + ...e6 体系；场均 85 回合半的缠斗型，67% 对局交换皇后；
易位过的对局里约 1/3 是长易位。

## 已知限制（原型级）

- 90 局慢棋只够撑开局库和粗粒度风格向量；细粒度风格需关联其线上
  账号（lichess/chess.com）扩样本，仓库的 `player-identity-links.csv`
  即为此准备。
- 未接真 Maia（lc0 需编译；FIDE→lichess 分段映射待做），当前用限强
  Stockfish 近似，中局"人味"不足。
- 风格加权是启发式，尚未用留出对局的着法命中率做过校准。
