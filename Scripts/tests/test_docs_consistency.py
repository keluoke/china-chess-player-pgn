"""Documentation-as-contract checks.

Operational docs (AGENTS/README/docs/Scripts-local) must only present
commands that exist in the current refresh.sh safe whitelist, must never
describe retired commands as runnable, and must keep the local-data /
full-data completeness governance language intact (link-only may only be
mentioned as retired). Historical review/plan documents under
``docs/reviews/`` are exempt.
"""

from __future__ import annotations

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]

# Keep in sync with the refresh.sh command router.
SAFE_COMMANDS = {
    "health", "all", "registry", "event-queue", "discover-events", "candidates",
    "bulk", "bulk-full", "deliver", "push", "redeliver", "receipts", "reindex",
    "recover-events", "storage-migrate", "shadow-deliver", "help",
}
RETIRED_COMMANDS = {
    "crawl", "crawl-full", "pgn", "pgn-full", "events", "events-full",
    "aliases", "promote", "reconcile", "verify", "contrib",
}

COMMAND_PATTERN = re.compile(r"refresh\.sh[ \t]+([a-z][a-z-]*)")


def operational_docs() -> list[pathlib.Path]:
    docs = [
        REPO / "AGENTS.md",
        REPO / "README.md",
        REPO / "CONTRIBUTING.md",
        REPO / "Scripts" / "local" / "README.md",
    ]
    for path in sorted((REPO / "docs").rglob("*.md")):
        if "reviews" in path.parts:
            continue
        docs.append(path)
    for path in sorted((REPO / "data").rglob("README.md")):
        docs.append(path)
    return [path for path in docs if path.is_file()]


class RefreshCommandContractTests(unittest.TestCase):
    def test_event_archive_upload_detects_new_gitignored_pgn(self) -> None:
        refresh = (REPO / "Scripts" / "local" / "refresh.sh").read_text(encoding="utf-8")
        self.assertIn("worktree-baseline.json", refresh)
        self.assertIn("-newer \"$archive_baseline\"", refresh)

    def test_every_documented_refresh_command_is_in_the_safe_whitelist(self) -> None:
        violations = []
        for path in operational_docs():
            text = path.read_text(encoding="utf-8")
            for match in COMMAND_PATTERN.finditer(text):
                command = match.group(1)
                if command == "bash":  # "refresh.sh bash -n" style test invocations
                    continue
                if command not in SAFE_COMMANDS:
                    violations.append(f"{path.relative_to(REPO)}: refresh.sh {command}")
        self.assertEqual(violations, [], "docs reference commands outside the safe whitelist")

    def test_retired_commands_are_never_documented_as_runnable(self) -> None:
        violations = []
        for path in operational_docs():
            text = path.read_text(encoding="utf-8")
            for match in COMMAND_PATTERN.finditer(text):
                if match.group(1) in RETIRED_COMMANDS:
                    violations.append(f"{path.relative_to(REPO)}: refresh.sh {match.group(1)}")
        self.assertEqual(violations, [], "retired commands must not appear as refresh.sh invocations")

    def test_safe_whitelist_matches_refresh_router(self) -> None:
        script = (REPO / "Scripts" / "local" / "refresh.sh").read_text(encoding="utf-8")
        for command in SAFE_COMMANDS - {"help"}:
            self.assertRegex(
                script, rf"(^|[|\s]){re.escape(command)}[)|]",
                f"refresh.sh router is missing safe command {command}",
            )
        retired_line = re.search(r"^\s*(crawl\|[^)]+)\)", script, re.MULTILINE)
        self.assertIsNotNone(retired_line, "refresh.sh must keep the retired-command block")

    def test_bulk_git_manifest_excludes_immutable_lichess_shards(self) -> None:
        script = (REPO / "Scripts" / "local" / "refresh.sh").read_text(encoding="utf-8")
        block = re.search(r"BULK_PATHS=\((.*?)\)\nEVENT_PATHS=", script, re.DOTALL)
        self.assertIsNotNone(block)
        self.assertNotIn('"docs/data/bulk"', block.group(1))
        self.assertNotIn("lichess-broadcast/shards", block.group(1))
        self.assertIn('"docs/data/bulk/lichess-events"', block.group(1))


class GovernanceLanguageTests(unittest.TestCase):
    def test_no_current_scraped_upload_pipeline(self) -> None:
        for path in operational_docs():
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(
                "data/incoming/<id>/(解析结果", text,
                f"{path}: the retired scraped-upload pipeline must not be described as current",
            )

    def test_local_data_description_keeps_manifest_and_ingest(self) -> None:
        local_readme = (REPO / "Scripts" / "local" / "README.md").read_text(encoding="utf-8")
        for term in ("manifest", "ingest", "local-data", "SHA-256"):
            self.assertIn(term, local_readme)
        agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
        for term in ("local-data", "manifest", "outbox", "永不 pull"):
            self.assertIn(term, agents)

    def test_data_and_code_publication_are_distinguished(self) -> None:
        agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("publish_code_via_api", agents)
        # Full-data completeness is the standard; link-only appears only as retired.
        self.assertIn("数据完备性", agents)
        self.assertIn("link-only 政策已退役", agents)
        # Default workflow is main-only; branches only on explicit request.
        self.assertIn("默认只在 `main` 上工作", agents)
        self.assertIn("不要为普通任务创建新分支", agents)
        local_readme = (REPO / "Scripts" / "local" / "README.md").read_text(encoding="utf-8")
        self.assertIn("默认直接在 `main` 上工作", local_readme)

    def test_all_player_frontends_use_shared_presentation_name_resolver(self) -> None:
        app = (REPO / "docs" / "app.js").read_text(encoding="utf-8")
        leaderboards = (REPO / "docs" / "leaderboards.js").read_text(encoding="utf-8")
        shared = (REPO / "docs" / "presentation-names.js").read_text(encoding="utf-8")
        for text in (app, leaderboards):
            self.assertIn('from "./presentation-names.js"', text)
            self.assertIn("resolvePlayerDisplayName", text)
            self.assertIn("buildPresentationNameIndex", text)
        self.assertNotIn("player.displayName || player.chineseName || player.name", leaderboards)
        self.assertIn("player?.chineseName", shared)
        self.assertIn("presentationNameConfidence === \"high\"", shared)
        self.assertIn("presentationNameConfidence === \"medium\"", shared)
        self.assertIn('"8602980"', shared)
        self.assertIn('"8608288"', shared)

    def test_dual_workspace_and_terminal_proxy_are_documented(self) -> None:
        agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
        local_readme = (REPO / "Scripts" / "local" / "README.md").read_text(encoding="utf-8")
        helper = (REPO / "Scripts" / "local" / "code_workspace.sh").read_text(encoding="utf-8")
        for text in (agents, local_readme):
            self.assertIn("代码工作区", text)
            self.assertIn("采集工作区", text)
            self.assertIn("http://127.0.0.1:15236", text)
            self.assertIn("HTTP_PROXY", text)
            self.assertIn("HTTPS_PROXY", text)
        self.assertIn("chessdb.workspaceRole code", helper)
        self.assertIn("git -C \"$CODE_ROOT\" config --local http.proxy", helper)
        self.assertIn("!/data/generated/", helper)
        self.assertIn("!/docs/data/", helper)
        self.assertIn("!/docs/api/", helper)
        self.assertIn("merge-base --is-ancestor origin/main main", helper)
        self.assertIn("main_ahead=", helper)
        self.assertIn("main_behind=", helper)
        self.assertIn("shallow 边界", local_readme)


if __name__ == "__main__":
    unittest.main()
