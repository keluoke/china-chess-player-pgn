"""One allowlist for deployed HTML, validation and public-copy tests."""
import argparse
import json
import pathlib
import shutil

ROOT = pathlib.Path(__file__).resolve().parents[1]


def generated_html_paths(docs_root: pathlib.Path) -> dict[str, pathlib.Path]:
    """Only exact manifest entries may augment/replace the public templates."""
    manifest = docs_root / "data/seo/manifest.json"
    if not manifest.is_file():
        return {}
    from validate_seo_pages import safe_file
    payload = json.loads(manifest.read_text())
    return {str(safe_file(row["file"])): docs_root / "data/seo/output" / row["file"]
            for row in payload["pages"]}


def public_html_paths(docs_root: pathlib.Path = ROOT / "docs") -> list[pathlib.Path]:
    entries = (ROOT / "Scripts/public_html_allowlist.txt").read_text().splitlines()
    paths = []
    for entry in entries:
        relative = pathlib.PurePosixPath(entry)
        if not entry or relative.is_absolute() or ".." in relative.parts or relative.suffix != ".html":
            raise ValueError(f"INVALID_PUBLIC_HTML_PATH: {entry}")
        path = docs_root / entry
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"MISSING_PUBLIC_HTML: {entry}")
        paths.append(path)
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--copy-html", type=pathlib.Path, required=True)
    args = parser.parse_args()
    for path in public_html_paths():
        target = args.copy_html / path.relative_to(ROOT / "docs")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    from validate_seo_pages import validate
    manifest = validate(ROOT)
    for row in manifest["files"]:
        target = args.copy_html / row["file"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "docs/data/seo/output" / row["file"], target)
    route_map = args.copy_html / "data/seo-routes.json"
    route_map.parent.mkdir(parents=True, exist_ok=True)
    route_map.write_text(json.dumps(manifest["routes"], ensure_ascii=False))
    (args.copy_html / "data/seo-public.json").write_text(json.dumps({
        "snapshotId": manifest["snapshotId"],
        "pages": [{key: row[key] for key in ("route", "contentSha256", "lastmod")}
                  for row in manifest["pages"]],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
