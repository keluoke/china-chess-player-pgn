#!/usr/bin/env python3
"""CI guard: public artifacts must not expose private fields or runbooks.

Domestic and youth event records can include minors. Public projections may
carry province/city-level ``publicLocation`` but raw club/school/contact values
stay in maintainer-only sources outside the deployed tree. Markdown is
deny-by-default: only the shared allowlist may enter a static-site artifact.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS_ROOT = REPO_ROOT / "docs"
MARKDOWN_ALLOWLIST = REPO_ROOT / "Scripts" / "public_markdown_allowlist.txt"
FORBIDDEN_KEYS = {"club", "school", "contact", "phone", "email", "wechat"}

# De-sourcing contract (AGENTS.md): the public surface never exposes the
# Chess-Results identity, external links or capture evidence. Lichess keeps
# its CC BY-SA attribution, so "source": "Lichess …" values are allowed.
DESOURCE_KEYS = {
    "sourceRefs", "sourceSnapshots", "sourceChineseName",
    "sourceFederation", "evidence", "pgnURL",
}
# sourceURL / sourceName / source may describe the Lichess Broadcast
# attribution (CC BY-SA obligation) — only the Chess-Results identity and
# links are banned from the public surface.
ATTRIBUTION_KEYS = {"sourceURL", "sourceName", "source"}
SOURCE_VALUE_BLOCKLIST = ("chess-results",)
MARKDOWN_BLOCKLIST = (
    "chess-results",
    "chess-results.com",
    "sourcerefs",
    "sourcesnapshots",
    "sourcechinesename",
    "sourcefederation",
    "pgnurl",
    "/volumes/",
    "docs/reviews/",
    "scripts/local/refresh.sh",
)


def _blocked_source_value(value) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith(SOURCE_VALUE_BLOCKLIST)


def public_markdown_allowlist() -> tuple[str, ...]:
    rows = []
    for line in MARKDOWN_ALLOWLIST.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value:
            continue
        path = pathlib.PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".md":
            raise ValueError(f"invalid public Markdown allowlist entry: {value}")
        rows.append(path.as_posix())
    if len(rows) != len(set(rows)):
        raise ValueError("duplicate public Markdown allowlist entry")
    return tuple(rows)


def markdown_offenses(text: str) -> list[str]:
    lowered = text.casefold()
    return [term for term in MARKDOWN_BLOCKLIST if term in lowered]


def offending_keys(node, path="$"):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in FORBIDDEN_KEYS and value not in (None, "", [], {}):
                yield f"{path}.{key}"
            if key in DESOURCE_KEYS and value not in (None, "", [], {}):
                yield f"{path}.{key} (de-sourcing)"
            if key in ATTRIBUTION_KEYS and _blocked_source_value(value):
                yield f"{path}.{key}={value!r} (de-sourcing)"
            if isinstance(value, str) and "chess-results.com" in value.lower():
                yield f"{path}.{key} contains chess-results.com URL"
            yield from offending_keys(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            if isinstance(item, str) and "chess-results.com" in item.lower():
                yield f"{path}[{index}] contains chess-results.com URL"
            yield from offending_keys(item, f"{path}[{index}]")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--site-root",
        type=pathlib.Path,
        help="validate an assembled static-site root instead of repository docs/",
    )
    args = parser.parse_args()
    public_root = args.site_root.resolve() if args.site_root else DOCS_ROOT
    roots = [root for root in (public_root / "data", public_root / "api") if root.exists()]
    failures: list[str] = []
    files_checked = 0
    for root in roots:
        for path in sorted(root.rglob("*.json")):
            files_checked += 1
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                failures.append(f"{path.relative_to(public_root)}: invalid JSON ({error})")
                continue
            hits = list(offending_keys(payload))
            if hits:
                rel = path.relative_to(public_root)
                failures.append(f"{rel}: {len(hits)} private field(s), e.g. {hits[0]}")

    allowlist = public_markdown_allowlist()
    if args.site_root:
        markdown_paths = sorted(public_root.rglob("*.md"))
        unexpected = [
            path.relative_to(public_root).as_posix()
            for path in markdown_paths
            if path.relative_to(public_root).as_posix() not in allowlist
        ]
        failures.extend(f"{path}: Markdown is not in the public allowlist" for path in unexpected)
    else:
        markdown_paths = [public_root / relative for relative in allowlist]

    markdown_checked = 0
    for path in markdown_paths:
        if not path.exists():
            failures.append(f"{path.relative_to(public_root)}: allowlisted Markdown is missing")
            continue
        markdown_checked += 1
        hits = markdown_offenses(path.read_text(encoding="utf-8"))
        if hits:
            failures.append(
                f"{path.relative_to(public_root)}: forbidden public Markdown term {hits[0]!r}"
            )
    if failures:
        print("PRIVACY CHECK FAILED — private fields or internal Markdown on the public surface:")
        for line in failures[:20]:
            print(f"  - {line}")
        print("Reduce JSON to public fields and keep non-allowlisted Markdown out of the deploy artifact.")
        return 1
    print(
        f"privacy check OK: {files_checked} JSON and {markdown_checked} allowlisted Markdown files"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
