"""Build or verify the deterministic manifest of distributed assets."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path("assets/ASSET_MANIFEST.csv")
FORMAL_ASSET_DIRS = {"templates", "reference", "models", "calendars", "guides", "schemas", "prompts"}


def manifest_bytes(root: Path) -> bytes:
    assets = root / "assets"
    if not assets.is_dir():
        raise ValueError("assets directory is missing: " + str(assets))
    present = {entry.name for entry in assets.iterdir() if entry.is_dir()}
    if present != FORMAL_ASSET_DIRS:
        raise ValueError("unexpected or missing formal asset directories: " + repr(present))
    paths = sorted(
        (path for path in assets.rglob("*") if path.is_file() and path != root / MANIFEST),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("path", "size", "sha256"))
    for path in paths:
        if path.is_symlink():
            raise ValueError("asset symlink is not allowed: " + str(path))
        data = path.read_bytes()
        writer.writerow((path.relative_to(root).as_posix(), len(data), hashlib.sha256(data).hexdigest()))
    return output.getvalue().encode("utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    expected = manifest_bytes(root)
    target = root / MANIFEST
    if args.check:
        if not target.is_file() or target.read_bytes() != expected:
            raise SystemExit("assets manifest differs from distributed assets: " + str(target))
    else:
        target.write_bytes(expected)
    print("assets manifest {}: {}".format("verified" if args.check else "written", target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
