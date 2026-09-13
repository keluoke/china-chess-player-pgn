# 历史 PGN 工具（已退役）

pgn_scout.py、promote_public_pgn.py、sync_static_pgn.py 的命令行入口已退役，
执行立即返回 MIGRATION_ONLY，不创建目录、访问来源或改写投影。
reconcile_pgn_sources.py 只保留离线审计；来源发现入口同样阻断。

当前维护入口以 [本地维护手册](../Scripts/local/README.md) 和
[Lichess 月度维护](LICHESS_MONTHLY_MAINTENANCE.md) 为准。
Chess-Results 使用 refresh.sh event-queue；广播由 GitHub 月度工作流维护；
所有派生构建只走 build_release_snapshot.py。

历史库函数保留给有明确输入的离线迁移、解析 fixture 和审计引用。
迁移不得调用旧 main、读取派生产物重建身份、直接发布或访问来源；
输出必须经过现行 staging、完整性校验和精确 manifest。新功能不得依赖旧 CLI。
