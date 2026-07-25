# 完整性与发布安全加固交付（2026-07-25）

## 交付基线

- 代码工作区：`/Volumes/AI/coding/kimi-code`
- 远端起点：`main@a3740b0fba72`
- 迁入的已审计提交：`55fce14110c`（Lichess 目标赛事交叉归档，不含机器数据）
- 本次只修改代码、测试和契约文档；没有手改 `data/generated/`，没有访问任何
  Chess-Results、FIDE 或 Lichess 来源。

## 已完成

1. 对阵引用改为三态：普通对局、明确轮空、unresolved。解析器仅接受名单内唯一
   姓名回填编号；其余缺号记录报 `PAIRING_REFS_MISSING`。完整度报告将 unresolved
   赛事降为 partial，并写入 `repair-pairing-player-numbers` P0 队列。
2. 没有采用 `minRoundRosterCoverage < 1` 硬门禁。奇数名单仍可 results-complete，
   避免把正常轮空误报为缺台。
3. Lichess manifest 的 `broadcastComplete` 与
   `linkedContainerUnmatchedGames` 接入完整度门禁。范围未验证或存在残差时，
   禁止 `source-published-complete` / `full-board-complete`，并进入离线复核。
4. 必需事实 JSON 读取失败改为响亮失败；可选回执/配置仍允许显式默认值。
5. release manifest 新增逐路径 `baseBlobOid` / `baseSha256`。云端 ingest 在
   checkout/index 写入前验证 baseline/current/candidate；真实并发冲突整包隔离。
   API fallback 在创建远端对象前执行同一判定，并记录 delivery baseline。
6. snapshot consistency gate 失败时原子恢复旧 `snapshot.json`。
7. 来源节流等待移到共享 flock 外；Lichess/FIDE manifest 写入接入稳定 JSON，
   删除会直接访问来源的调试草稿 `Scripts/test_cr_download.py`。

## 真实数据只读复算

使用采集工作区现有 959 个 event-details 在内存中重算，未写生成文件：

| 检查 | 结果 |
|---|---:|
| unresolved 赛事 | 10 |
| unresolved 对阵 | 1,738 |
| `repair-pairing-player-numbers` P0 任务 | 10 |
| 含 Lichess 未匹配残差的赛事 | 28 |
| 修复后仍误标 complete | 0 |
| 奇数名单 `tnr1049967` / `tnr1049971` | 均保持 results-complete |

## 明确未采用

- 不把每轮名单覆盖率直接接成硬门禁。
- 不把“单侧缺号”自动解释成轮空。
- 不做一次性 `Scripts/common.py` 全仓替换、Pairing dataclass 改写或
  `sync_domestic_players.py` 大拆分；这些重构与正确性修复解耦。
- 不 hard reset 任一工作区，不覆盖两份未跟踪历史评审文档。

## 尚存边界

本次完成的是发布冲突的安全检测与隔离，保证不会静默覆盖 main。自动字段级
三方合并及按自然键生成新增/更新/覆盖/隔离明细回执仍是独立后续能力；在它落地
前，任何真实冲突都必须 fail closed，而不是猜测合并。
