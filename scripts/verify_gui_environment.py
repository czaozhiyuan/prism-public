"""Check the GUI dependency/runtime lock before packaging; Python 3.8 only."""
import hashlib
from importlib import metadata
import json
from pathlib import Path
import struct
import sys

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


ROOT = Path(__file__).resolve().parents[1]


def pinned_requirements(path):
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r "):
            result.update(pinned_requirements(path.parent / line[3:]))
            continue
        requirement = Requirement(line)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        specifiers = list(requirement.specifier)
        assert len(specifiers) == 1 and specifiers[0].operator == "==", "Unpinned dependency: " + line
        result[canonicalize_name(requirement.name)] = specifiers[0].version
    return result


def main():
    assert sys.version_info[:3] == (3, 8, 10) and struct.calcsize("P") == 8, "GUI requires CPython 3.8.10 x64"
    assert sys.prefix != sys.base_prefix, "Use an isolated GUI virtual environment"
    expected = pinned_requirements(ROOT / "requirements-gui-build.txt")
    actual = {canonicalize_name(dist.metadata["Name"]): dist.version for dist in metadata.distributions()}
    actual.pop("prism", None)
    assert actual == expected, "GUI packages differ from the lock: " + repr({
        name: (expected.get(name), actual.get(name)) for name in set(expected) | set(actual)
        if expected.get(name) != actual.get(name)})
    lock = json.loads((ROOT / "gui-runtime-lock.json").read_text(encoding="utf-8"))
    roots = {"python": Path(sys.base_prefix), "site": Path(sys.prefix) / "Lib/site-packages"}
    verified = {}
    for group, files in lock["sha256"].items():
        for name, digest in files.items():
            path = roots[group] / name
            measured = hashlib.sha256(path.read_bytes()).hexdigest()
            assert measured == digest, "GUI runtime differs from the lock: " + str(path)
            verified[group + ":" + name] = measured
    output = ROOT / "artifacts/p1-validation/gui-environment.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"python": sys.version, "packages": actual,
        "runtime": lock["versions"], "verified_sha256": verified,
        "status": "passed"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("GUI dependency and runtime lock passed: " + str(output))


if __name__ == "__main__":
    main()
