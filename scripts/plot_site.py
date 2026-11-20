"""One figure per site: each sensor's inverted series at a moving point and a stable point.

The two points are chosen by rule, not by hand, so a new site gets the same figure:
the **moving** point is the fastest place (smoothed) of the reference sensor's last cumulative
displacement inside the moving areas; the **stable** point is the place within 1 km of the
site centre that lies farthest from any moving area. Reads the exported projects
(``build/datasets/``), so it shows what is published.

Usage: ``python scripts/plot_site.py <site> [reference_sensor]`` → ``build/figures/``.
"""

from __future__ import annotations

import sys

import geopandas as gpd
import matplotlib

matplotlib.use("agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from rasterio.features import geometry_mask  # noqa: E402
from scipy.ndimage import uniform_filter  # noqa: E402
from shapely.geometry import Point  # noqa: E402
from study_site import BUILD_DIR, DATASETS_DIR, load_site  # noqa: E402

from geomulticorr.stats.inversion_extractor import InversionExtractor  # noqa: E402
from geomulticorr.utils.gmc_functions import plot_cumulative_time_series  # noqa: E402

FIGURES_DIR = BUILD_DIR / "figures"
#: Radius (m) averaged around each point, and the smoothing window (px) used to pick it.
BUFFER_M = 30.0
SMOOTH_PX = 5
STABLE_SEARCH_M = 1000.0


def _inversion_dir(sensor) -> "Path":  # noqa: F821
    return (
        DATASETS_DIR / sensor.project / sensor.site.pz_name / "inversion" / "reference"
    )


def _read(path):
    with rasterio.open(path) as src:
        data = src.read(1).astype(float)
        if src.nodata is not None:
            data[data == src.nodata] = np.nan
        return data, src.transform, src.shape


def pick_points(site, reference_key: str) -> gpd.GeoDataFrame:
    sensor = site.sensors[reference_key]
    ew_dir = _inversion_dir(sensor) / "inverse_EW"
    last_ew = sorted(ew_dir.glob("TOT_*_EW.tif"))[-1]
    ew, transform, shape = _read(last_ew)
    ns, _, _ = _read(ew_dir.parent / "inverse_NS" / last_ew.name.replace("_EW", "_NS"))
    outlines = gpd.read_file(site.moving_areas).to_crs(site.epsg)
    moving = geometry_mask(outlines.geometry, shape, transform, invert=True)

    speed = np.hypot(ew, ns)
    smooth = uniform_filter(np.nan_to_num(speed), SMOOTH_PX)
    smooth[~moving | ~np.isfinite(speed)] = -np.inf
    row, col = np.unravel_index(np.argmax(smooth), shape)
    fast = Point(*rasterio.transform.xy(transform, row, col))

    centre = Point(*site.center_xy)
    union = outlines.union_all()
    rows, cols = np.mgrid[0 : shape[0] : 5, 0 : shape[1] : 5]
    xs, ys = rasterio.transform.xy(transform, rows.ravel(), cols.ravel())
    candidates = [
        Point(x, y)
        for x, y in zip(xs, ys)
        if Point(x, y).distance(centre) <= STABLE_SEARCH_M
    ]
    stable = max(candidates, key=union.distance)
    return gpd.GeoDataFrame(
        {"point_id": ["rock_glacier", "stable"]}, geometry=[fast, stable], crs=site.epsg
    )


def main(argv: list[str]) -> None:
    if len(argv) not in (2, 3):
        raise SystemExit(__doc__)
    site = load_site(argv[1])
    reference = argv[2] if len(argv) == 3 else "spot"
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    points = pick_points(site, reference)
    points_path = FIGURES_DIR / f"{site.name}_points.gpkg"
    points.to_file(points_path, driver="GPKG")

    frames = {}
    for key, sensor in site.sensors.items():
        if not (_inversion_dir(sensor) / "inverse_EW").is_dir():
            continue
        frame = InversionExtractor(
            _inversion_dir(sensor), components=["EW", "NS"]
        ).extract(points_path, buffer_m=BUFFER_M)
        frames[key] = frame
    fig = plot_cumulative_time_series(
        frames,
        components=("EW", "NS"),
        point_ids=["rock_glacier", "stable"],
        fig_name=f"{site.name}: cumulative displacement (m), one line per sensor",
    )
    fig = fig[0] if isinstance(fig, tuple) else fig
    out = FIGURES_DIR / f"{site.name}_time_series.png"
    try:
        fig.savefig(out, dpi=110)
    finally:
        plt.close(fig)
    print(
        points.assign(x=points.geometry.x.round(), y=points.geometry.y.round())[
            ["point_id", "x", "y"]
        ]
    )
    print(out)


if __name__ == "__main__":
    main(sys.argv)
