# 代码审查覆盖清单（2026-09-11）

以已提交 main 代码为范围；排除生成数据和第三方压缩 vendor 文件。所有条目进行结构、入口/引用与风险模式扫描；关键数据与发布链逐段人工审阅，测试提供行为证据。清单不表示每一行已经形式化证明正确。既有未跟踪实验文件单独列为工作区卫生问题。

共 159 个自有代码/配置文件，50,963 行。

| 文件 | 行数 | 函数数（Python） | 分区 | 职责/模块说明 |
|---|---:|---:|---|---|
| `.github/ISSUE_TEMPLATE/data-correction.yml` | 35 | — | CI/部署 | 配置/页面结构与调用关系扫描 |
| `.github/ISSUE_TEMPLATE/new-player.yml` | 31 | — | CI/部署 | 配置/页面结构与调用关系扫描 |
| `.github/ISSUE_TEMPLATE/transfer-report.yml` | 37 | — | CI/部署 | 配置/页面结构与调用关系扫描 |
| `.github/actions/dispatch-workflow/action.yml` | 46 | — | CI/部署 | 配置/页面结构与调用关系扫描 |
| `.github/actions/prepare-static-site/action.yml` | 132 | — | CI/部署 | 配置/页面结构与调用关系扫描 |
| `.github/actions/rebuild-indexes/action.yml` | 34 | — | CI/部署 | 配置/页面结构与调用关系扫描 |
| `.github/actions/setup-python-deps/action.yml` | 29 | — | CI/部署 | 配置/页面结构与调用关系扫描 |
| `.github/workflows/ci.yml` | 104 | — | CI/部署 | 配置/页面结构与调用关系扫描 |
| `.github/workflows/deploy.yml` | 131 | — | CI/部署 | 配置/页面结构与调用关系扫描 |
| `.github/workflows/ingest-local-data.yml` | 137 | — | CI/部署 | 配置/页面结构与调用关系扫描 |
| `.github/workflows/rebuild-indexes.yml` | 150 | — | CI/部署 | 配置/页面结构与调用关系扫描 |
| `.github/workflows/update-contribution-funnel.yml` | 44 | — | CI/部署 | 配置/页面结构与调用关系扫描 |
| `.github/workflows/update-lichess-broadcasts.yml` | 74 | — | CI/部署 | 配置/页面结构与调用关系扫描 |
| `.github/workflows/validate-data.yml` | 27 | — | CI/部署 | 配置/页面结构与调用关系扫描 |
| `Scripts/age_groups.py` | 77 | 4 | 采集/事实/派生/校验 | Unified age-group rules for the whole pipeline. |
| `Scripts/apply_aliases_to_registry.py` | 279 | 11 | 采集/事实/派生/校验 | Apply reviewed Chinese-name sources to the static registry JSON in place. |
| `Scripts/archive_rating_observations.py` | 113 | 1 | 采集/事实/派生/校验 | Archive official-rating observations keyed by FIDE list month. |
| `Scripts/backfill_domestic_pinyin.py` | 67 | 2 | 采集/事实/派生/校验 | Fill missing domestic-player pinyin search aliases in the manual evidence CSV. |
| `Scripts/build_api.py` | 338 | 7 | 采集/事实/派生/校验 | Build the static data API under docs/api/v1/. |
| `Scripts/build_changelog.py` | 60 | 3 | 采集/事实/派生/校验 | Append a data changelog entry (docs/data/changelog.json) when totals move. |
| `Scripts/build_completeness_report.py` | 1105 | 32 | 采集/事实/派生/校验 | Build the per-event CompletenessReport (multi-dimensional gates). |
| `Scripts/build_dashboard.py` | 183 | 6 | 采集/事实/派生/校验 | Build docs/data/dashboard.json — homepage data dashboard payload. |
| `Scripts/build_data_quality_audit.py` | 125 | 5 | 采集/事实/派生/校验 | Build an offline anomaly queue for event metadata and PGN/result joins. |
| `Scripts/build_domestic_event_queue.py` | 285 | 9 | 采集/事实/派生/校验 | Build the demand-driven domestic event ingestion queue (offline only). |
| `Scripts/build_domestic_progressions.py` | 130 | 7 | 采集/事实/派生/校验 | Build reviewed domestic-player progression and promotion evidence. |
| `Scripts/build_event_catalog.py` | 1094 | 32 | 采集/事实/派生/校验 | Build the event catalogs from committed source and index artifacts. |
| `Scripts/build_event_details.py` | 408 | 20 | 采集/事实/派生/校验 | Build public domestic-event standings/round payloads and PGN cross-links. |
| `Scripts/build_leaderboards.py` | 139 | 3 | 采集/事实/派生/校验 | Build docs/data/leaderboards.json — all age groups, youth AND adult. |
| `Scripts/build_master_series_summary.py` | 250 | 7 | 采集/事实/派生/校验 | Build the public 2022-2026 Chess Association Master series summary. |
| `Scripts/build_person_observations.py` | 257 | 9 | 采集/事实/派生/校验 | Project Entry/Standing facts into PersonObservation rows (plan §5, P1-1). |
| `Scripts/build_pgn_collection_status.py` | 143 | 5 | 采集/事实/派生/校验 | Reconcile durable per-event PGN source state from local machine facts. |
| `Scripts/build_player_facts.py` | 656 | 28 | 采集/事实/派生/校验 | Build canonical player-event and player-game facts without derived feedback. |
| `Scripts/build_player_participation.py` | 151 | 6 | 采集/事实/派生/校验 | Build public per-player participation history independent of PGN coverage. |
| `Scripts/build_public_metrics.py` | 57 | 3 | 采集/事实/派生/校验 | Write the canonical metric contract and align the legacy index manifest. |
| `Scripts/build_release_snapshot.py` | 259 | 7 | 采集/事实/派生/校验 | Single-entry derived-data rebuild under one atomic snapshot id. |
| `Scripts/build_search_bootstrap.py` | 324 | 9 | 采集/事实/派生/校验 | Build the search bootstrap payloads for the homepage. |
| `Scripts/build_static_player_pgn.py` | 1225 | 56 | 采集/事实/派生/校验 | Build player-centric static PGN packs from committed PGN assets. |
| `Scripts/canonical_player_facts.py` | 105 | 4 | 采集/事实/派生/校验 | Read and validate the canonical player fact datasets. |
| `Scripts/ci_commit_push.sh` | 88 | — | 采集/事实/派生/校验 | 配置/页面结构与调用关系扫描 |
| `Scripts/crawl_player_events.py` | 746 | 28 | 采集/事实/派生/校验 | Legacy full Chess-Results player crawler (disabled by default). |
| `Scripts/event_targeting.py` | 124 | 7 | 采集/事实/派生/校验 | Shared maintainer-local event targeting helpers. |
| `Scripts/fetch_event_pgn.py` | 808 | 26 | 采集/事实/派生/校验 | Fetch full-tournament PGN from Chess-Results — high-efficiency rewrite. |
| `Scripts/import_master_tournament_markdown.py` | 209 | 10 | 采集/事实/派生/校验 | Import verified Chess-Results master-event sections from a Markdown list. |
| `Scripts/import_name_mapping_tsv.py` | 134 | 2 | 采集/事实/派生/校验 | Import pre-fetched player name-mapping rows (TSV) into the manual mapping CSVs. |
| `Scripts/import_web_contribution.py` | 127 | 8 | 采集/事实/派生/校验 | Import a downloaded web contribution into the offline demand/source queues. |
| `Scripts/local/check_receipts.py` | 527 | 15 | 本地控制与发布 | Advance outbox delivery states with real cloud receipts. |
| `Scripts/local/cloudflare_baseline.py` | 655 | 20 | 本地控制与发布 | Prepare, deliver and reconcile an exact Git snapshot into shadow ingest. |
| `Scripts/local/cloudflare_ingest.py` | 601 | 15 | 本地控制与发布 | Deliver one immutable local outbox bundle to the Cloudflare shadow ingest. |
| `Scripts/local/code_workspace.sh` | 151 | — | 本地控制与发布 | 配置/页面结构与调用关系扫描 |
| `Scripts/local/collector_runtime.py` | 351 | 16 | 本地控制与发布 | Install and verify an exact collector runtime/control-input overlay. |
| `Scripts/local/discover_player_events.py` | 188 | 5 | 本地控制与发布 | Discover recent tournament IDs by searching a bounded set of FIDE IDs. |
| `Scripts/local/health_check.py` | 241 | 9 | 本地控制与发布 | Read-only preflight for the maintainer-local collection workstation. |
| `Scripts/local/identity_review.py` | 57 | 2 | 本地控制与发布 | Read the repo-external identity workbench without changing review data. |
| `Scripts/local/import_identity_dispute.py` | 107 | 4 | 本地控制与发布 | Import downloaded identity dispute contributions into presentation-disputes.csv. |
| `Scripts/local/panel.py` | 1320 | 34 | 本地控制与发布 | Maintainer-local control panel for the policy-enforced refresh entrypoint. |
| `Scripts/local/publish_code_via_api.py` | 333 | 18 | 本地控制与发布 | Publish local code changes on top of remote main without fetching/cloning. |
| `Scripts/local/publish_data_via_api.py` | 400 | 13 | 本地控制与发布 | Manifest-driven API fallback for delivering an outbox release bundle. |
| `Scripts/local/reconcile_pgn_conflicts.py` | 183 | 11 | 本地控制与发布 | Reconcile dirty per-player PGNs against complete local event archives. |
| `Scripts/local/refresh.sh` | 1220 | — | 本地控制与发布 | 配置/页面结构与调用关系扫描 |
| `Scripts/local/resolve_release_conflict.py` | 361 | 11 | 本地控制与发布 | Create a new immutable successor for terminal machine-release conflicts. |
| `Scripts/local/run_manager.py` | 1376 | 45 | 本地控制与发布 | Persistent local-run state and manifest-driven data delivery. |
| `Scripts/local/upload_bulk_to_r2.py` | 862 | 19 | 本地控制与发布 | Upload large immutable assets (bulk PGN shards, event archives) to R2. |
| `Scripts/pgn_scout.py` | 1005 | 53 | 采集/事实/派生/校验 | Local raw PGN scout for Chinese chess assets. |
| `Scripts/promote_incoming.py` | 257 | 9 | 采集/事实/派生/校验 | 把已合并进 main 的 data/incoming/ 社区载荷核验后并入正式数据(维护者本地)。 |
| `Scripts/promote_public_pgn.py` | 532 | 30 | 采集/事实/派生/校验 | Promote public, quality-checked PGN into docs/data/pgn. |
| `Scripts/public_metrics.py` | 49 | 2 | 采集/事实/派生/校验 | Canonical public metrics shared by every user-facing manifest. |
| `Scripts/reconcile_pgn_sources.py` | 957 | 56 | 采集/事实/派生/校验 | Audit PGN source coverage and stage Chess-Results discovery batches. |
| `Scripts/snapshot_context.py` | 42 | 2 | 采集/事实/派生/校验 | Shared snapshot identity for derived builds. |
| `Scripts/source_http.py` | 393 | 22 | 采集/事实/派生/校验 | Strict, source-responsive HTTP client shared by source collectors. |
| `Scripts/source_policy.py` | 110 | 6 | 采集/事实/派生/校验 | Shared source and local-storage policy for every network collector. |
| `Scripts/stable_json.py` | 62 | 2 | 采集/事实/派生/校验 | Deterministic JSON writer for derived artifacts. |
| `Scripts/sync_chess_results_event.py` | 1747 | 71 | 采集/事实/派生/校验 | Fetch one or more Chess-Results events by tnr ID. |
| `Scripts/sync_chess_results_starting_rank_aliases.py` | 843 | 47 | 采集/事实/派生/校验 | Collect private Chinese-name candidates from Chess-Results starting ranks. |
| `Scripts/sync_chinese_players.py` | 843 | 44 | 采集/事实/派生/校验 | Build the static Chinese player registry from FIDE rating-list exports. |
| `Scripts/sync_domestic_players.py` | 2029 | 56 | 采集/事实/派生/校验 | Build domestic provisional player registry from event sightings. |
| `Scripts/sync_lichess_broadcast_bulk.py` | 1226 | 56 | 采集/事实/派生/校验 | Mirror and index the Lichess broadcast PGN bulk dataset. |
| `Scripts/sync_static_pgn.py` | 740 | 33 | 采集/事实/派生/校验 | Sync cached/fetched PGN files into the GitHub Pages static data tree. |
| `Scripts/tests/__init__.py` | 1 | — | 测试 | Local pipeline regression tests. |
| `Scripts/tests/fixtures/chess_results/empty_event.html` | 7 | — | 测试 | 配置/页面结构与调用关系扫描 |
| `Scripts/tests/fixtures/chess_results/pairings_missing_refs.html` | 10 | — | 测试 | 配置/页面结构与调用关系扫描 |
| `Scripts/tests/fixtures/chess_results/pairings_names_only.html` | 10 | — | 测试 | 配置/页面结构与调用关系扫描 |
| `Scripts/tests/fixtures/chess_results/pairings_out_of_roster.html` | 10 | — | 测试 | 配置/页面结构与调用关系扫描 |
| `Scripts/tests/fixtures/chess_results/pairings_round.html` | 10 | — | 测试 | 配置/页面结构与调用关系扫描 |
| `Scripts/tests/fixtures/chess_results/pairings_shifted.html` | 11 | — | 测试 | 配置/页面结构与调用关系扫描 |
| `Scripts/tests/fixtures/chess_results/standings_individual.html` | 13 | — | 测试 | 配置/页面结构与调用关系扫描 |
| `Scripts/tests/fixtures/chess_results/standings_no_rounds.html` | 12 | — | 测试 | 配置/页面结构与调用关系扫描 |
| `Scripts/tests/fixtures/chess_results/starting_rank_domestic_minimal.html` | 9 | — | 测试 | 配置/页面结构与调用关系扫描 |
| `Scripts/tests/fixtures/chess_results/starting_rank_individual.html` | 11 | — | 测试 | 配置/页面结构与调用关系扫描 |
| `Scripts/tests/fixtures/chess_results/team_player_list.html` | 11 | — | 测试 | 配置/页面结构与调用关系扫描 |
| `Scripts/tests/fixtures/chess_results/team_round.html` | 12 | — | 测试 | 配置/页面结构与调用关系扫描 |
| `Scripts/tests/fixtures/chess_results/tournament_details.html` | 7 | — | 测试 | 配置/页面结构与调用关系扫描 |
| `Scripts/tests/frontend_presentation_names_test.mjs` | 62 | — | 测试 | 配置/页面结构与调用关系扫描 |
| `Scripts/tests/frontend_search_core_test.mjs` | 33 | — | 测试 | 配置/页面结构与调用关系扫描 |
| `Scripts/tests/pgn_proxy_test.mjs` | 74 | — | 测试 | 配置/页面结构与调用关系扫描 |
| `Scripts/tests/test_chess_results_parser.py` | 823 | 65 | 测试 | Contract tests for the Chess-Results event collector. |
| `Scripts/tests/test_cloudflare_baseline.py` | 118 | 3 | 测试 | Python 模块；按调用关系归类 |
| `Scripts/tests/test_cloudflare_ingest_client.py` | 555 | 18 | 测试 | Python 模块；按调用关系归类 |
| `Scripts/tests/test_collector_runtime.py` | 125 | 4 | 测试 | Python 模块；按调用关系归类 |
| `Scripts/tests/test_completeness_and_identity.py` | 1057 | 70 | 测试 | Second-review (2026-07-18) mechanism tests. |
| `Scripts/tests/test_docs_consistency.py` | 170 | 12 | 测试 | Documentation-as-contract checks. |
| `Scripts/tests/test_domestic_event_queue.py` | 67 | 1 | 测试 | Python 模块；按调用关系归类 |
| `Scripts/tests/test_frontend_initialization_order.py` | 150 | 10 | 测试 | Python 模块；按调用关系归类 |
| `Scripts/tests/test_homepage_brand_story.py` | 95 | 6 | 测试 | Python 模块；按调用关系归类 |
| `Scripts/tests/test_identity_clustering_quality.py` | 128 | 6 | 测试 | Python 模块；按调用关系归类 |
| `Scripts/tests/test_identity_dispute_import.py` | 59 | 3 | 测试 | Python 模块；按调用关系归类 |
| `Scripts/tests/test_identity_evidence_chains.py` | 252 | 11 | 测试 | Python 模块；按调用关系归类 |
| `Scripts/tests/test_lichess_monthly.py` | 114 | 7 | 测试 | Failure boundaries for the hosted Lichess maintenance transaction. |
| `Scripts/tests/test_lichess_target_events.py` | 231 | 8 | 测试 | Python 模块；按调用关系归类 |
| `Scripts/tests/test_local_pipeline.py` | 2916 | 159 | 测试 | Python 模块；按调用关系归类 |
| `Scripts/tests/test_master_series_summary.py` | 106 | 6 | 测试 | Python 模块；按调用关系归类 |
| `Scripts/tests/test_player_facts.py` | 246 | 9 | 测试 | Python 模块；按调用关系归类 |
| `Scripts/tests/test_player_pgn_r2_integrity.py` | 320 | 15 | 测试 | Python 模块；按调用关系归类 |
| `Scripts/tests/test_public_catalog.py` | 456 | 32 | 测试 | Contract tests for the curated public event catalog and round gating. |
| `Scripts/tests/test_release_conflict_successor.py` | 165 | 8 | 测试 | Python 模块；按调用关系归类 |
| `Scripts/tests/test_search_and_rankings.py` | 216 | 13 | 测试 | Python 模块；按调用关系归类 |
| `Scripts/update_contribution_funnel.py` | 78 | 4 | 采集/事实/派生/校验 | Refresh the observable community contribution funnel from GitHub metadata. |
| `Scripts/update_lichess_monthly.py` | 251 | 9 | 采集/事实/派生/校验 | Prepare a verified Lichess release; publish that artifact without source access. |
| `Scripts/validate_community_data.py` | 404 | 15 | 采集/事实/派生/校验 | Offline validation of community-editable data files. Runs in CI on PRs. |
| `Scripts/validate_identity_clustering.py` | 450 | 16 | 采集/事实/派生/校验 | Measure domestic identity projection quality against embedded FIDE truth. |
| `Scripts/validate_incoming.py` | 169 | 7 | 采集/事实/派生/校验 | Validate target-only community submissions under ``data/incoming``. |
| `Scripts/validate_master_group_labels.py` | 121 | 5 | 采集/事实/派生/校验 | Validate reviewed master-tournament group labels against local event titles. |
| `Scripts/validate_no_tracked_raw_evidence.py` | 100 | 3 | 采集/事实/派生/校验 | Fail CI if Git tracks raw HTML/WARC capture evidence. |
| `Scripts/validate_player_pgn_r2_receipt.py` | 196 | 5 | 采集/事实/派生/校验 | Fail closed unless every by-player PGN package is certified in R2. |
| `Scripts/validate_public_metrics.py` | 40 | 2 | 采集/事实/派生/校验 | Fail CI when public manifests disagree about PGN coverage totals. |
| `Scripts/validate_public_privacy.py` | 152 | 5 | 采集/事实/派生/校验 | CI guard: public artifacts must not expose private fields or runbooks. |
| `Scripts/validate_registry_authority.py` | 111 | 5 | 采集/事实/派生/校验 | Reject derived player identities that diverge from the registry. |
| `Scripts/validate_registry_release.py` | 87 | 3 | 采集/事实/派生/校验 | Validate a staged registry before it is atomically promoted or released. |
| `Scripts/validate_snapshot_consistency.py` | 328 | 8 | 采集/事实/派生/校验 | Gate: every public derived manifest must reference ONE snapshotId. |
| `Scripts/verify_community_sources.py` | 84 | 2 | 采集/事实/派生/校验 | Locally verify community-submitted evidence URLs (residential IP required). |
| `cloudflare/ingest/migrations/0001_initial.sql` | 78 | — | 影子 ingest | 配置/页面结构与调用关系扫描 |
| `cloudflare/ingest/migrations/0002_chunked_release_state.sql` | 22 | — | 影子 ingest | 配置/页面结构与调用关系扫描 |
| `cloudflare/ingest/migrations/0003_d1_storage_ledger.sql` | 11 | — | 影子 ingest | 配置/页面结构与调用关系扫描 |
| `cloudflare/ingest/migrations/0004_multipart_uploads.sql` | 23 | — | 影子 ingest | 配置/页面结构与调用关系扫描 |
| `cloudflare/ingest/src/policy.js` | 369 | — | 影子 ingest | 配置/页面结构与调用关系扫描 |
| `cloudflare/ingest/src/worker.js` | 745 | — | 影子 ingest | 配置/页面结构与调用关系扫描 |
| `cloudflare/ingest/test/policy.test.mjs` | 255 | — | 测试 | 配置/页面结构与调用关系扫描 |
| `cloudflare/ingest/wrangler.toml` | 49 | — | 影子 ingest | 配置/页面结构与调用关系扫描 |
| `docs/app.js` | 2621 | — | 前端 | 配置/页面结构与调用关系扫描 |
| `docs/contribute.html` | 94 | — | 前端 | 配置/页面结构与调用关系扫描 |
| `docs/contribute.js` | 214 | — | 前端 | 配置/页面结构与调用关系扫描 |
| `docs/coverage.html` | 61 | — | 前端 | 配置/页面结构与调用关系扫描 |
| `docs/coverage.js` | 68 | — | 前端 | 配置/页面结构与调用关系扫描 |
| `docs/events.html` | 85 | — | 前端 | 配置/页面结构与调用关系扫描 |
| `docs/events.js` | 89 | — | 前端 | 配置/页面结构与调用关系扫描 |
| `docs/index.html` | 197 | — | 前端 | 配置/页面结构与调用关系扫描 |
| `docs/leaderboards.html` | 44 | — | 前端 | 配置/页面结构与调用关系扫描 |
| `docs/leaderboards.js` | 232 | — | 前端 | 配置/页面结构与调用关系扫描 |
| `docs/master-series.css` | 141 | — | 前端 | 配置/页面结构与调用关系扫描 |
| `docs/master-series.html` | 99 | — | 前端 | 配置/页面结构与调用关系扫描 |
| `docs/master-series.js` | 229 | — | 前端 | 配置/页面结构与调用关系扫描 |
| `docs/presentation-names.js` | 104 | — | 前端 | 配置/页面结构与调用关系扫描 |
| `docs/search-core.js` | 106 | — | 前端 | 配置/页面结构与调用关系扫描 |
| `docs/styles.css` | 2655 | — | 前端 | 配置/页面结构与调用关系扫描 |
| `docs/theme.js` | 59 | — | 前端 | 配置/页面结构与调用关系扫描 |
| `functions/api/event-pgn.js` | 28 | — | 边缘接口 | 配置/页面结构与调用关系扫描 |
| `functions/api/github/device-code.js` | 18 | — | 边缘接口 | 配置/页面结构与调用关系扫描 |
| `functions/api/github/device-token.js` | 27 | — | 边缘接口 | 配置/页面结构与调用关系扫描 |
| `functions/api/v1/players/[[path]].js` | 35 | — | 边缘接口 | 配置/页面结构与调用关系扫描 |
| `functions/data/pgn/[[path]].js` | 117 | — | 边缘接口 | 配置/页面结构与调用关系扫描 |
