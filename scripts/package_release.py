"""Tar each checked dataset into ``build/release/`` and record its SHA-256 in registry.json.

Refuses any dataset that fails ``check_dataset``. Publishes nothing.
Usage: ``python scripts/package_release.py NAME [NAME ...]``
"""

from __future__ import annotations

import hashlib
import json
import sys
import tarfile

from check_dataset import check_dataset
from study_site import DATA_VERSION, DATASETS_DIR, REGISTRY_PATH, RELEASE_DIR


def package(name: str) -> tuple[str, int]:
    root = DATASETS_DIR / name
    problems = check_dataset(root)
    if problems:
        raise SystemExit(f"{name} refused:\n  " + "\n  ".join(problems))
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    archive = RELEASE_DIR / f"{name}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(root, arcname=name)
    return hashlib.sha256(archive.read_bytes()).hexdigest(), archive.stat().st_size


def main(names: list[str]) -> None:
    registry = {"version": DATA_VERSION, "assets": {}}
    if REGISTRY_PATH.exists():
        registry = json.loads(REGISTRY_PATH.read_text())
        registry["version"] = DATA_VERSION
    for name in names:
        digest, size = package(name)
        registry["assets"][name] = {"sha256": digest, "bytes": size}
        print(f"{name}: {size / 1e6:.1f} MB  {digest}")
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
