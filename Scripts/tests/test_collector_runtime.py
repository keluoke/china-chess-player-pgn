from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Scripts/local"))

import collector_runtime  # noqa: E402


class CollectorRuntimeTests(unittest.TestCase):
    def fixture(self, root: pathlib.Path):
        spec_path = root / "Scripts/local/collector-runtime-files.json"
        runtime_path = root / "Scripts/example.py"
        spec_path.parent.mkdir(parents=True)
        runtime_path.parent.mkdir(parents=True, exist_ok=True)
        runtime_path.write_text("VALUE = 1\n", encoding="utf-8")
        spec = {
            "schemaVersion": 1,
            "files": [
                {
                    "path": "Scripts/local/collector-runtime-files.json",
                    "kind": "runtime-spec",
                    "profiles": ["core"],
                },
                {
                    "path": "Scripts/example.py",
                    "kind": "runtime",
                    "profiles": ["core"],
                },
            ],
        }
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        rows = []
        for path, kind in ((spec_path, "runtime-spec"), (runtime_path, "runtime")):
            body = path.read_bytes()
            rows.append({
                "path": path.relative_to(root).as_posix(),
                "kind": kind,
                "profiles": ["core"],
                "mode": "100644",
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            })
        manifest_path = root / "installed.json"
        manifest_path.write_text(json.dumps({
            "schemaVersion": 1,
            "sourceCommit": "a" * 40,
            "specSha256": hashlib.sha256(spec_path.read_bytes()).hexdigest(),
            "files": rows,
        }), encoding="utf-8")
        return spec_path, runtime_path, manifest_path

    def test_exact_manifest_verifies_and_drift_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            spec, runtime, manifest = self.fixture(root)
            result = collector_runtime.verify(
                root, profile="core", manifest_path=manifest, spec_path=spec,
            )
            self.assertEqual(result["checked"], 2)
            runtime.write_text("VALUE = 2\n", encoding="utf-8")
            with self.assertRaisesRegex(collector_runtime.CollectorRuntimeError, "COLLECTOR_RUNTIME_DRIFT"):
                collector_runtime.verify(
                    root, profile="core", manifest_path=manifest, spec_path=spec,
                )

    def test_missing_and_stale_manifest_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            spec, _runtime, manifest = self.fixture(root)
            missing = root / "missing.json"
            with self.assertRaisesRegex(collector_runtime.CollectorRuntimeError, "MANIFEST_MISSING"):
                collector_runtime.verify(root, profile="core", manifest_path=missing, spec_path=spec)
            payload = json.loads(manifest.read_text())
            payload["specSha256"] = "0" * 64
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(collector_runtime.CollectorRuntimeError, "MANIFEST_STALE"):
                collector_runtime.verify(root, profile="core", manifest_path=manifest, spec_path=spec)

    def test_production_spec_covers_snapshot_runtime_and_control_inputs(self):
        spec = collector_runtime.parse_spec_bytes(
            (ROOT / "Scripts/local/collector-runtime-files.json").read_bytes()
        )
        rows = {row["path"]: row for row in spec["files"]}
        required = {
            "Scripts/build_player_facts.py",
            "Scripts/build_completeness_report.py",
            "Scripts/build_event_details.py",
            "Scripts/build_player_participation.py",
            "Scripts/build_api.py",
            "Scripts/validate_snapshot_consistency.py",
            "data/community/name-corrections.csv",
            "data/community/federation-overrides.csv",
            "data/community/tournament-name-mappings.csv",
            "docs/data/index/public-events.json",
        }
        self.assertFalse(required - set(rows))
        self.assertEqual(rows["docs/data/index/public-events.json"]["profiles"], ["event", "panel"])
        orchestrator = (ROOT / "Scripts/build_release_snapshot.py").read_text(encoding="utf-8")
        invoked = set(re.findall(r'"(Scripts/[A-Za-z0-9_./-]+\.py)"', orchestrator))
        missing_core = sorted(
            path for path in invoked
            if path not in rows or "core" not in rows[path]["profiles"]
        )
        self.assertEqual(missing_core, [])


if __name__ == "__main__":
    unittest.main()
