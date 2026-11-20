"""The publish guard: each forbidden item planted in a clean dataset must be refused."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from check_dataset import check_dataset  # noqa: E402

KEY = "Site_2021-08-12_planetscope_3m__2023-08-22_planetscope_3m"


def _write_tif(path: Path, data: np.ndarray, **profile) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=data.shape[1],
        height=data.shape[0],
        count=1,
        dtype=data.dtype,
        crs="EPSG:2154",
        transform=Affine(3, 0, 0, 0, -3, 0),
        **profile,
    ) as dst:
        dst.write(data, 1)


def _refresh_manifest(root: Path, **overrides) -> None:
    path = root / "manifest.json"
    manifest = json.loads(path.read_text()) if path.exists() else {}
    manifest |= {
        "name": "demo",
        "licence": "x",
        "attribution": "x",
        "gmc_version": "0",
        "results_only": True,
        **overrides,
    }
    manifest["files"] = {
        p.relative_to(root).as_posix(): {
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest()
        }
        for p in root.rglob("*")
        if p.is_file() and not p.is_symlink() and p.name != "manifest.json"
    }
    path.write_text(json.dumps(manifest))


@pytest.fixture
def clean(tmp_path) -> Path:
    root = tmp_path / "demo"
    ones = np.ones((4, 4), dtype="uint8")
    _write_tif(
        root / "Site" / "optical" / "Site_2021-08-12_planetscope_3m.tif", ones, nbits=1
    )
    pair = root / "Site" / "image_correlation" / KEY
    _write_tif(pair / f"{KEY}-F_EW.tif", np.zeros((4, 4), dtype="float32"))
    (pair / f"{KEY}.json").write_text('{"metadata": {}}')
    _refresh_manifest(root)
    return root


def _refused(root: Path, needle: str) -> bool:
    return any(needle in problem for problem in check_dataset(root))


def test_clean_dataset_passes(clean):
    assert check_dataset(clean) == []


@pytest.mark.parametrize(
    "suffix", ["-L.tif", "-R.tif", "-RD.tif", "-L-R-disp-diff.tif"]
)
def test_image_bearing_output_is_refused(clean, suffix):
    pair = clean / "Site" / "image_correlation" / KEY
    _write_tif(pair / f"{KEY}{suffix}", np.zeros((4, 4), dtype="float32"))
    _refresh_manifest(clean)
    assert _refused(clean, "image-bearing")


def test_symlink_is_refused(clean):
    pair = clean / "Site" / "image_correlation" / KEY
    os.symlink("/etc/hostname", pair / "link.tif")
    assert _refused(clean, "symlink")


def test_real_image_as_thumb_is_refused(clean):
    pixels = np.arange(16, dtype="uint16").reshape(4, 4)
    _write_tif(
        clean / "Site" / "optical" / "Site_2022-01-01_planetscope_3m.tif", pixels
    )
    _refresh_manifest(clean)
    assert _refused(clean, "not a placeholder")


def test_jpeg_is_refused_in_a_results_only_set(clean):
    pair = clean / "Site" / "image_correlation" / KEY
    (pair / f"{KEY}_raw_disp.jpg").write_bytes(b"\xff\xd8")
    _refresh_manifest(clean)
    assert _refused(clean, "image file")


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ('{"path": "/home/someone/project"}', "local path"),
        ('{"path": "/data/risques/x"}', "local path"),
        ('{"path": "/media/someone/LaCie/x"}', "local path"),
        ('{"api_key": "abcdef0123456789abcd"}', "credential"),
        ('{"url": "https://x.blob/y?sv=1&sig=abc"}', "signed URL"),
    ],
)
def test_leaks_in_text_files_are_refused(clean, text, label):
    pair = clean / "Site" / "image_correlation" / KEY
    (pair / f"{KEY}.json").write_text(text)
    _refresh_manifest(clean)
    assert _refused(clean, label)


def test_cross_sensor_pair_is_refused(clean):
    key = "Site_2021-08-12_spot6_3m__2023-08-22_planetscope_3m"
    pair = clean / "Site" / "image_correlation" / key
    _write_tif(pair / f"{key}-F_EW.tif", np.zeros((4, 4), dtype="float32"))
    _refresh_manifest(clean)
    assert _refused(clean, "cross-sensor")


def test_spot6_with_spot7_is_one_family(clean):
    key = "Site_2021-08-12_spot6__2023-08-22_spot7"
    root = clean
    other = root / "Site" / "image_correlation" / KEY
    other.rename(root / "Site" / "image_correlation" / key)
    _refresh_manifest(clean, results_only=False)
    assert check_dataset(clean) == []


def test_tampered_file_is_refused(clean):
    pair = clean / "Site" / "image_correlation" / KEY
    (pair / f"{KEY}.json").write_text('{"changed": true}')
    assert _refused(clean, "checksum mismatch")


def test_unlisted_file_is_refused(clean):
    (clean / "extra.txt").write_text("x")
    assert _refused(clean, "not in the manifest")


def test_absolute_path_in_a_geodatabase_is_refused(clean):
    (clean / "GMC_geodatabase_demo.gpkg").write_bytes(
        b"SQLite format 3\x00 /home/someone/p"
    )
    _refresh_manifest(clean)
    assert _refused(clean, "absolute path")


def test_placeholder_paths_in_a_geodatabase_pass(clean):
    (clean / "GMC_geodatabase_demo.gpkg").write_bytes(
        b"SQLite format 3\x00 @GMC_PROJECT@/p"
    )
    _refresh_manifest(clean)
    assert check_dataset(clean) == []


def test_missing_licence_is_refused(clean):
    _refresh_manifest(clean, licence="")
    assert _refused(clean, "licence")
