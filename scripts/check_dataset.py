"""Publish guard: refuse to package a dataset that could leak or misrepresent anything.

Usage: ``python scripts/check_dataset.py build/datasets/<asset>`` (an image bank or a GMC
project). Exit code 1 lists every problem found. ``package_release.py`` runs the same checks
and will not package on failure.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import rasterio
from study_site import MAX_ASSET_MB

from geomulticorr.similarity.selection import sensor_family

#: ASP outputs that hold the (normalised) source image, or can be used to rebuild it.
IMAGE_BEARING_SUFFIXES = ("-L.tif", "-R.tif", "-RD.tif", "-L-R-disp-diff.tif")
#: Nothing else may stand in for imagery in a results-only set.
IMAGE_LIKE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".jp2")

TEXT_EXTENSIONS = {".json", ".txt", ".sh", ".yaml", ".yml", ".md", ".csv", ".xml"}
FORBIDDEN_TEXT = {
    "local path": re.compile(r"/home/|/media/|/data/risques|/tmp/"),
    "credential-shaped value": re.compile(
        r"(?i)(api[_-]?key|token|secret|password)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{16,}"
    ),
    "signed URL": re.compile(r"[?&](sig|se|sv|X-Amz-Signature)="),
}

#: A geodatabase is binary, so its paths are looked for as bytes.
FORBIDDEN_GEODATABASE_BYTES = (b"/home/", b"/media/", b"/data/risques", b"/tmp/")

PAIR_KEY = re.compile(
    r"^(?P<pz>[^_]+)_\d{4}-\d{2}-\d{2}_(?P<left>[^_]+)(?:_[^_]+)?"
    r"__\d{4}-\d{2}-\d{2}_(?P<right>[^_]+)(?:_[^_]+)?$"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_placeholder(path: Path) -> bool:
    """A placeholder thumb is a single constant 1-bit band: georeferencing and no image."""
    with rasterio.open(path) as src:
        if src.count != 1 or src.tags(1, ns="IMAGE_STRUCTURE").get("NBITS") != "1":
            return False
        return bool((src.read(1) == 1).all())


def check_results_only(root: Path, files: list[Path]) -> list[str]:
    problems = []
    for path in files:
        if path.name.endswith(IMAGE_BEARING_SUFFIXES):
            problems.append(f"image-bearing ASP output in a results-only set: {path}")
        if path.suffix.lower() in IMAGE_LIKE_EXTENSIONS:
            problems.append(f"image file in a results-only set: {path}")
    for thumb in root.glob("*/optical/*.tif"):
        if not is_placeholder(thumb):
            problems.append(f"thumb is not a placeholder: {thumb}")
    return problems


def check_same_sensor(root: Path) -> list[str]:
    """This first version holds same-sensor pairs only, and one family per dataset."""
    problems = []
    families = set()
    for folder in (p for p in root.glob("*/image_correlation/*") if p.is_dir()):
        match = PAIR_KEY.match(folder.name)
        if match is None:
            problems.append(f"unparseable pair key: {folder.name}")
        elif sensor_family(match["left"]) != sensor_family(match["right"]):
            problems.append(f"cross-sensor pair: {folder.name}")
        else:
            families.add(sensor_family(match["left"]))
    if len(families) > 1:
        problems.append(f"more than one sensor in a dataset: {sorted(families)}")
    return problems


def check_manifest(root: Path, manifest: dict, files: list[Path]) -> list[str]:
    problems = []
    recorded = manifest.get("files", {})
    on_disk = {p.relative_to(root).as_posix() for p in files} - {"manifest.json"}
    for missing in sorted(on_disk - recorded.keys()):
        problems.append(f"file not in the manifest: {missing}")
    for ghost in sorted(recorded.keys() - on_disk):
        problems.append(f"manifest lists a missing file: {ghost}")
    for name in sorted(on_disk & recorded.keys()):
        if sha256(root / name) != recorded[name]["sha256"]:
            problems.append(f"checksum mismatch: {name}")
    for field in ("name", "licence", "attribution", "gmc_version"):
        if not manifest.get(field):
            problems.append(f"manifest has no {field!r}")
    return problems


def check_geodatabases(files: list[Path]) -> list[str]:
    """Every stored path must be a placeholder: ``examples.get_project`` rewrites them."""
    problems = []
    for path in files:
        if path.suffix.lower() != ".gpkg":
            continue
        data = path.read_bytes()
        for needle in FORBIDDEN_GEODATABASE_BYTES:
            if needle in data:
                problems.append(
                    f"absolute path ({needle.decode()}) in geodatabase {path}"
                )
    return problems


def check_dataset(root: Path) -> list[str]:
    root = Path(root)
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        return [f"no manifest.json in {root}"]
    manifest = json.loads(manifest_path.read_text())

    problems = []
    every = sorted(root.rglob("*"))
    problems += [f"symlink: {p}" for p in every if p.is_symlink()]
    files = [p for p in every if p.is_file() and not p.is_symlink()]

    for path in files:
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        text = path.read_text(errors="replace")
        for label, pattern in FORBIDDEN_TEXT.items():
            if pattern.search(text):
                problems.append(f"{label} in {path}")

    problems += check_geodatabases(files)
    if manifest.get("results_only") and manifest.get("kind") != "bank":
        problems += check_results_only(root, files)
    problems += check_same_sensor(root)
    problems += check_manifest(root, manifest, files)

    total_mb = sum(p.stat().st_size for p in files) / 1e6
    if total_mb > MAX_ASSET_MB:
        problems.append(f"{total_mb:.0f} MB uncompressed, limit is {MAX_ASSET_MB} MB")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    problems = check_dataset(Path(argv[1]))
    for problem in problems:
        print(f"REFUSED: {problem}")
    if not problems:
        print(f"OK: {argv[1]}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
