"""Record the source commit and asset manifest used by a release build."""

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/p11-build/BUILD_INFO.json"


def main() -> None:
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True,
    ).strip()
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise ValueError("invalid build commit")
    manifest = ROOT / "assets/ASSET_MANIFEST.csv"
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({
        "build_commit": commit,
        "assets_manifest": "sha256:" + digest,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("build metadata written: " + str(OUTPUT))


if __name__ == "__main__":
    main()
