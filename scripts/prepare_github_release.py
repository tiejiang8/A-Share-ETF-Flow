"""Export an allowlisted source tree and ZIP without copying operator data."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import zipfile


ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = {".github", "config", "contracts", "docs", "examples", "scripts", "src", "tests", "etf_temperature"}
FILES = {".gitignore", ".gitattributes", "README.md", "README.zh-CN.md", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md", "LICENSE", "LICENSE.md", "NOTICE", "pyproject.toml", "setup.py", "MANIFEST.in", "CHANGELOG_W28_NAV_BACKFILL.md"}
SUFFIXES = {".py", ".ps1", ".md", ".json", ".yaml", ".yml", ".txt", ".toml"}
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".venv", "node_modules", ".git", "_default_config", ".workbuddy", ".codex", ".agents", ".baoyu-skills", ".tmp", "tmp"}


def release_files(root: Path) -> list[Path]:
    candidates = [root / name for name in FILES if (root / name).is_file() and not (root / name).is_symlink()]
    for directory in sorted(DIRECTORIES):
        folder = root / directory
        if folder.exists() and not folder.is_symlink():
            candidates.extend(path for path in folder.rglob("*") if path.is_file() and path.suffix in SUFFIXES and not any(part in EXCLUDED_PARTS or part.endswith(".egg-info") for part in path.relative_to(root).parts) and not path.name.startswith(".env") and not path.is_symlink())
    return sorted(candidates, key=lambda path: path.relative_to(root).as_posix())


def prepare_release(destination: Path, *, root: Path = ROOT) -> dict:
    destination = destination.resolve()
    # No deletion or overwrite: each export gets its own inspectable directory.
    destination.mkdir(parents=True, exist_ok=False)
    manifest = []
    for source in release_files(root):
        relative = source.relative_to(root)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        manifest.append({"path": relative.as_posix(), "bytes": source.stat().st_size, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
    document = {"project": "A-Share-ETF-Flow", "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "files": manifest, "excluded": ["data/", "outputs/", ".venv/", ".git/", ".workbuddy/", ".agents/", ".codex/", ".baoyu-skills/", ".tmp/", "tmp/", "credentials and logs"]}
    (destination / "RELEASE_MANIFEST.json").write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    archive = Path(str(destination) + ".zip")
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(destination.rglob("*")):
            if path.is_file():
                bundle.write(path, Path("A-Share-ETF-Flow") / path.relative_to(destination))
    return {"directory": str(destination), "archive": str(archive), "file_count": len(manifest), "source_bytes": sum(item["bytes"] for item in manifest), "archive_bytes": archive.stat().st_size}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=ROOT / "dist/github" / ("A-Share-ETF-Flow-" + datetime.now().strftime("%Y%m%d-%H%M%S")))
    result = prepare_release(parser.parse_args().destination)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
