"""One allowlist for deployed HTML, validation and public-copy tests."""
import argparse
import pathlib
import shutil

ROOT = pathlib.Path(__file__).resolve().parents[1]


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


if __name__ == "__main__":
    main()
