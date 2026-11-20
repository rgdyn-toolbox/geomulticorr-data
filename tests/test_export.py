"""Results-only export: a placeholder keeps a thumb's name and grid, and no image."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from check_dataset import is_placeholder  # noqa: E402
from export import write_placeholder  # noqa: E402


def test_placeholder_keeps_the_grid_and_drops_the_image(tmp_path):
    source = tmp_path / "Site_2020-08-19_planetscope_3m.tif"
    transform = Affine(3, 0, 999753, 0, -3, 6484389)
    pixels = np.arange(1, 41, dtype="uint16").reshape(5, 8)
    with rasterio.open(
        source,
        "w",
        driver="GTiff",
        width=8,
        height=5,
        count=1,
        dtype="uint16",
        crs="EPSG:2154",
        transform=transform,
    ) as dst:
        dst.write(pixels, 1)

    target = write_placeholder(source, tmp_path / "out" / source.name)

    assert target.name == source.name
    assert is_placeholder(target)
    with rasterio.open(target) as out:
        assert out.transform == transform
        assert out.crs.to_epsg() == 2154
        assert out.shape == (5, 8)
        assert not np.isin(pixels, out.read(1)).all()
