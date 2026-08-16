import hashlib
import json
import pathlib
import subprocess
import tempfile
import unittest

from Scripts.local import cloudflare_baseline


class CloudflareBaselineTests(unittest.TestCase):
    def test_source_partition_and_two_dimensional_packing(self):
        source = {"source": "Chess-Results", "releasePolicy": "full-data"}
        entries = [
            {
                "path": f"data/generated/chess-results-event-details/tnr{index}.json",
                "bytes": 30 * 1024 * 1024,
                "sourceKey": "chess-results",
                "source": source,
            }
            for index in range(4)
        ]
        packages = cloudflare_baseline.pack_entries(entries)
        self.assertEqual([len(package) for package in packages], [3, 1])
        self.assertTrue(all(sum(item["bytes"] for item in package) <= cloudflare_baseline.MAX_BYTES for package in packages))
        key, metadata = cloudflare_baseline.source_for_path("docs/data/registry/players.json")
        self.assertEqual(key, "fide")
        self.assertEqual(metadata["releasePolicy"], "factual-registry-projection")

    def test_prepare_builds_exact_root_and_immutable_outbox(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            candidate = repo / "data/generated/chess-results-event-details/tnr1.json"
            candidate.parent.mkdir(parents=True)
            candidate.write_text("{}\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "snapshot"], cwd=repo, check=True)
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            migration_dir = cloudflare_baseline.prepare(repo, commit, root / "state", "baseline-test")
            migration = json.loads((migration_dir / "migration.json").read_text(encoding="utf-8"))
            self.assertEqual(migration["targetCommit"], commit)
            self.assertEqual(migration["expectedFiles"], 1)
            self.assertEqual(migration["expectedBytes"], 3)
            package = migration["packages"][0]
            manifest = json.loads((migration_dir / "outbox" / package["runId"] / "manifest.json").read_text(encoding="utf-8"))
            item = manifest["files"][0]
            self.assertEqual(item["sha256"], hashlib.sha256(b"{}\n").hexdigest())
            self.assertEqual(item["baseSha256"], item["sha256"])
            self.assertTrue((migration_dir / "outbox" / package["runId"] / "files" / item["path"]).is_file())

    def test_prepare_catchup_builds_minimal_delta_and_new_exact_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            registry = repo / "docs/data/registry"
            registry.mkdir(parents=True)
            changed = registry / "changed.json"
            deleted = registry / "deleted.json"
            changed.write_text('{"value":1}\n', encoding="utf-8")
            deleted.write_text('{"delete":true}\n', encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)
            base_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            baseline_dir = cloudflare_baseline.prepare(repo, base_commit, root / "state", "baseline-test")
            baseline = json.loads((baseline_dir / "migration.json").read_text(encoding="utf-8"))
            baseline["status"] = "delivered"
            for package in baseline["packages"]:
                package["status"] = "complete"
            cloudflare_baseline.run_manager.atomic_json(baseline_dir / "migration.json", baseline)

            changed.write_text('{"value":2}\n', encoding="utf-8")
            deleted.unlink()
            added = registry / "added.json"
            added.write_text('{"added":true}\n', encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "target"], cwd=repo, check=True)
            target_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

            catchup_dir = cloudflare_baseline.prepare_catchup(repo, baseline_dir, target_commit, None)
            catchup = json.loads((catchup_dir / "migration.json").read_text(encoding="utf-8"))
            self.assertEqual(catchup["baseCommit"], base_commit)
            self.assertEqual(catchup["targetCommit"], target_commit)
            self.assertEqual(catchup["expectedFiles"], 2)
            self.assertEqual(
                [(item["operation"], item["path"]) for item in catchup["changes"]],
                [
                    ("upsert", "docs/data/registry/added.json"),
                    ("upsert", "docs/data/registry/changed.json"),
                    ("delete", "docs/data/registry/deleted.json"),
                ],
            )
            package = catchup["packages"][0]
            bundle = catchup_dir / "outbox" / package["runId"]
            manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
            manifest_items = {item["path"]: item for item in manifest["files"]}
            self.assertIsNone(manifest_items["docs/data/registry/added.json"]["baseSha256"])
            self.assertNotEqual(
                manifest_items["docs/data/registry/changed.json"]["baseSha256"],
                manifest_items["docs/data/registry/changed.json"]["sha256"],
            )
            self.assertFalse((bundle / "files/docs/data/registry/deleted.json").exists())
            self.assertEqual(
                (bundle / "files/docs/data/registry/changed.json").read_text(encoding="utf-8"),
                '{"value":2}\n',
            )


if __name__ == "__main__":
    unittest.main()
