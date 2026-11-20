"""Turn a built project and its bank into publishable, relocatable folders.

A GMC geodatabase stores absolute paths (``th_path``, ``pa_path``, the bank map's
``file_path``), and ``get_thumbs``/``get_pairs`` read them as they are. Export replaces the
project root with ``@GMC_PROJECT@`` and the bank root with ``@GMC_BANK@``, in every text
column of every table; ``geomulticorr.examples`` swaps them back after copying.

Usage: ``python scripts/export.py <site> <sensor>``
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import rasterio
from study_site import DATASETS_DIR, Sensor, load_sensor

import geomulticorr
from geomulticorr.utils._grid import write_binary_raster

PROJECT_TOKEN = "@GMC_PROJECT@"
BANK_TOKEN = "@GMC_BANK@"

#: Pair files a test starts from or compares with; ASP's image-bearing outputs stay out.
PAIR_SUFFIXES = (
    "-F.tif",
    "-F-ncc.tif",
    "-F_EW.tif",
    "-F_NS.tif",
    "-F_CC.tif",
    "-F_EW_corr.tif",
    "-F_NS_corr.tif",
    "-F_EWmask.tif",
    "-F_NSmask.tif",
    "_CorrParameters.txt",
    ".json",
)
TEXT_SUFFIXES = {".json", ".txt", ".xml"}
SKIPPED_NAMES = ("-shm", "-wal", ".aux.xml")


def rewrite_geodatabase(path: Path, replacements: dict[str, str]) -> int:
    """Replace substrings in every text column of every layer; returns the cells changed.

    Layers are read and written back with pyogrio, as ``Session._rewrite_geodatabase_paths``
    does through geopandas: an SQL ``UPDATE`` would fire the GeoPackage R-tree triggers, which
    call spatial functions plain ``sqlite3`` does not have. pyogrio also handles the layers
    without geometry, such as ``layer_styles``.
    """
    import pyogrio

    changed = 0
    for layer, _ in pyogrio.list_layers(path):
        frame = pyogrio.read_dataframe(path, layer=layer)
        touched = False
        for column in frame.columns:
            if frame[column].dtype != object or column == "geometry":
                continue
            values = frame[column]
            new = values
            for old, token in replacements.items():
                new = new.map(
                    lambda v: v.replace(old, token) if isinstance(v, str) else v
                )
            diff = int((new != values).sum())
            if diff:
                frame[column] = new
                changed += diff
                touched = True
        if touched:
            pyogrio.write_dataframe(frame, path, layer=layer)
    # pyogrio leaves the file in WAL mode: back to a single self-contained file.
    db = sqlite3.connect(path)
    try:
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("VACUUM")
    finally:
        db.close()
    for sidecar in (Path(f"{path}-shm"), Path(f"{path}-wal")):
        sidecar.unlink(missing_ok=True)
    return changed


def _recompress_float(source: Path, target: Path) -> bool:
    """Float rasters barely shrink in a tarball: DEFLATE + float predictor, same values."""
    with rasterio.open(source) as src:
        if not src.dtypes[0].startswith("float"):
            return False
        profile = src.profile | {"compress": "deflate", "predictor": 3, "zlevel": 9}
        with rasterio.open(target, "w", **profile) as dst:
            dst.write(src.read())
    return True


def _copy(source: Path, target: Path, replacements: dict[str, str]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix in TEXT_SUFFIXES:
        text = source.read_text(errors="replace")
        for old, new in replacements.items():
            text = text.replace(old, new)
        target.write_text(text)
    elif source.suffix.lower() != ".tif" or not _recompress_float(source, target):
        shutil.copy2(source, target)


def _keep_project_file(path: Path, project: Path) -> bool:
    if path.is_symlink() or any(path.name.endswith(s) for s in SKIPPED_NAMES):
        return False
    parts = path.relative_to(project).parts
    if "image_correlation" in parts and path.parent.name != "image_correlation":
        return path.name.endswith(PAIR_SUFFIXES)
    if "inversion" in parts:
        if path.parent.name == "inverse_magn" or path.parent.name == "binary":
            return False
        if path.parent.name == "diagnostics":
            return path.name == "rms.tif"
        return path.name.startswith("TOT_") or path.name.endswith("_parameters.json")
    return True


def export_project(sensor: Sensor) -> Path:
    project = sensor.work_dir
    root = DATASETS_DIR / sensor.project
    if root.exists():
        shutil.rmtree(root)
    replacements = {str(project): PROJECT_TOKEN, str(sensor.bank_dir): BANK_TOKEN}
    for path in sorted(project.rglob("*")):
        target = root / path.relative_to(project)
        if path.is_dir() and not path.is_symlink():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file() and _keep_project_file(path, project):
            if sensor.results_only and path.parent.name == "optical":
                write_placeholder(path, target)
            else:
                _copy(path, target, replacements)
    for gpkg in root.glob("*.gpkg"):
        rewrite_geodatabase(gpkg, replacements)
    _write_notices(sensor, root, kind="project")
    return root


def write_placeholder(source: Path, target: Path) -> Path:
    """The thumb's name, grid and CRS with no image: one constant 1-bit band.

    For a results-only project (images that may not be redistributed): ``Pair`` opens both
    thumb files, so they must exist, but extraction, corrections and inversion read only the
    pair rasters. ``check_dataset`` refuses any thumb that is not such a placeholder.
    """
    with rasterio.open(source) as src:
        ones = np.ones(src.shape, dtype=bool)
        transform, crs = src.transform, src.crs
    target.parent.mkdir(parents=True, exist_ok=True)
    return write_binary_raster(ones, transform, crs, target)


def export_bank(sensor: Sensor) -> Path:
    root = DATASETS_DIR / sensor.bank_asset
    if root.exists():
        shutil.rmtree(root)
    shutil.copytree(sensor.bank_dir, root / sensor.key)
    _write_notices(sensor, root, kind="bank")
    return root


def _write_notices(sensor: Sensor, root: Path, kind: str) -> None:
    lines = [
        f"{root.name}: GeoMultiCorr test data, site {sensor.site.name}, sensor {sensor.key}.",
        "",
        f"Terms: {sensor.licence}",
        f"Credit: {sensor.attribution}",
        *sensor.credits,
        "",
        "Source images and the DEM keep their providers' terms above. The products",
        "made with GeoMultiCorr (thumbs, correlation, corrected and inverted surface displacement maps,",
        "geodatabase) are licensed CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/):",
        "credit 'GeoMultiCorr test data, Cusicanqui & Duvanel, 2025; DOI:10.5281/zenodo.17079419' and the sources above.",

        "",
    ]
    (root / "LICENSE.txt").write_text("\n".join(lines))
    write_manifest(sensor, root, kind)


def write_manifest(sensor: Sensor, root: Path, kind: str) -> None:
    files = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path == root / "manifest.json":
            continue  # its own; a Planet order's manifest.json inside a bank is data
        files[path.relative_to(root).as_posix()] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
    manifest = {
        "name": root.name,
        "kind": kind,
        "site": sensor.site.name,
        "sensor": sensor.key,
        "project": sensor.project,
        "bank": sensor.bank_asset,
        "pz_name": sensor.site.pz_name,
        "epsg": sensor.site.epsg,
        "aoi_bounds": list(sensor.aoi_bounds),
        "resolution_m": sensor.resolution_m,
        "selected_band": sensor.selected_band,
        # The local archive path stays out: it names a private disk, not a source.
        "source": {k: v for k, v in sensor.source.items() if k != "root"},
        "pairing": sensor.pairing,
        "correlation": sensor.correlation,
        "results_only": sensor.results_only,
        "thumbs_are_placeholders": sensor.results_only and kind == "project",
        "path_tokens": {"project": PROJECT_TOKEN, "bank": BANK_TOKEN},
        "licence": sensor.licence,
        "attribution": sensor.attribution,
        "gmc_version": geomulticorr.__version__,
        "asp_version": _asp_version(),
        "files": files,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2, default=list))


def _asp_version() -> str:
    try:
        out = subprocess.run(
            ["parallel_stereo", "--version"], capture_output=True, text=True, timeout=60
        ).stdout
    except OSError:
        return "unknown"
    return out.splitlines()[0].strip() if out else "unknown"


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        raise SystemExit(__doc__)
    sensor = load_sensor(argv[1], argv[2])
    if sensor.results_only:
        print(f"{sensor.bank_asset}: not exported, the images are results-only")
    else:
        print(export_bank(sensor))
    print(export_project(sensor))


if __name__ == "__main__":
    main(sys.argv)
