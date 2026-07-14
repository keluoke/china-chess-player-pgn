"""Documentation-as-contract checks.

Operational docs (AGENTS/README/docs/Scripts-local) must only present
commands that exist in the current refresh.sh safe whitelist, must never
describe retired commands as runnable, and must keep the local-data /
link-only governance language intact. Historical review/plan documents under
``docs/reviews/`` are exempt.
"""

from __future__ import annotations

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]

# Keep in sync with the refresh.sh command router.
SAFE_COMMANDS = {
    "health", "all", "registry", "event-queue", "candidates",
    "bulk", "bulk-full", "deliver", "push", "redeliver", "receipts", "reindex", "help",
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
        self.assertIn("link-only", agents)
        # Default workflow is main-only; branches only on explicit request.
        self.assertIn("默认只在 `main` 上工作", agents)
        self.assertIn("不要为普通任务创建新分支", agents)
        local_readme = (REPO / "Scripts" / "local" / "README.md").read_text(encoding="utf-8")
        self.assertIn("默认直接在 `main` 上工作", local_readme)


if __name__ == "__main__":
    unittest.main()
