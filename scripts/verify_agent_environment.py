"""Verify the CPython runtime and exact Agent dependency lock."""

from importlib import metadata
import hashlib
import json
from pathlib import Path
import platform
import re
import struct
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PINNED = re.compile(r"^([A-Za-z0-9_.-]+)==([^;\s]+)(?:\s*;\s*(.+))?$")


def _canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _marker_applies(marker):
    if marker is None:
        return True
    marker = marker.strip().replace('"', "'")
    if marker == "sys_platform == 'win32'":
        return sys.platform == "win32"
    if marker == "python_version < '3.9'":
        return sys.version_info[:2] < (3, 9)
    if marker == "python_version >= '3.9'":
        return sys.version_info[:2] >= (3, 9)
    raise AssertionError("Unsupported requirement marker: " + marker)


def pinned_requirements(path):
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r "):
            result.update(pinned_requirements(path.parent / line[3:].strip()))
            continue
        match = PINNED.fullmatch(line)
        assert match, "Unpinned Agent dependency: " + line
        if _marker_applies(match.group(3)):
            result[_canonical(match.group(1))] = match.group(2)
    return result


def main():
    lock = json.loads((ROOT / "agent-runtime-lock.json").read_text(encoding="utf-8"))
    runtime = lock["runtime"]
    assert platform.python_implementation() == runtime["implementation"]
    assert platform.python_version() == runtime["version"]
    assert struct.calcsize("P") == 8 and runtime["architecture"] == "x64"
    assert sys.prefix != sys.base_prefix, "Use the isolated .venv-agent environment"
    uv_version = subprocess.run(
        ["uv", "--version"], capture_output=True, text=True, check=True,
    ).stdout.strip().split()[1]
    assert uv_version == runtime["uv"], "uv differs from the Agent runtime lock"

    verified = {}
    for name, digest in lock["sha256"].items():
        path = Path(sys.base_prefix) / name
        measured = hashlib.sha256(path.read_bytes()).hexdigest()
        assert measured == digest, "Agent runtime differs from lock: " + str(path)
        verified[name] = measured

    packager = lock["packager"]
    assert metadata.version(packager["name"]) == packager["version"]
    bootloader_root = Path(sys.prefix) / "Lib/site-packages" / packager["bootloader_directory"]
    for name, digest in packager["bootloader_sha256"].items():
        path = bootloader_root / name
        measured = hashlib.sha256(path.read_bytes()).hexdigest()
        assert measured == digest, "Agent bootloader differs from lock: " + str(path)
        verified["bootloader:" + name] = measured

    expected = pinned_requirements(ROOT / "requirements-agent-build.txt")
    actual = {_canonical(dist.metadata["Name"]): dist.version
              for dist in metadata.distributions() if dist.metadata["Name"]}
    actual.pop("prism", None)
    assert actual == expected, "Agent packages differ from lock: " + repr({
        name: (expected.get(name), actual.get(name))
        for name in set(expected) | set(actual)
        if expected.get(name) != actual.get(name)
    })

    output = ROOT / "artifacts/p11-validation/agent-environment.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "python": sys.version,
        "architecture": 64,
        "uv": uv_version,
        "packages": actual,
        "packager": {"name": packager["name"], "version": packager["version"]},
        "verified_sha256": verified,
        "status": "passed",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Agent dependency and runtime lock passed: " + str(output))


if __name__ == "__main__":
    main()
