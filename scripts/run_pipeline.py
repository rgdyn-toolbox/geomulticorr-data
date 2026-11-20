"""Every GMC stage for one project, through the public Session API.

``build_project.py`` drives it: bank → thumbs → pairs → ASP → extraction → references →
corrections → inversion. Each stage stops the build on a failed pair, because the library only
logs per-pair errors.
"""

from __future__ import annotations

import json
import shutil
import urllib.parse
import urllib.request
from pathlib import Path

import geopandas as gpd
import geoutils as gu
from shapely.geometry import box
from study_site import Sensor

from geomulticorr.core.session import open_gmc_session
from geomulticorr.corrections import (
    CCFilter,
    MedianCentering,
    OutlierFilter,
    RampCorrection,
    SlopeMask,
    StableAreaMask,
    make_corrections,
)
from geomulticorr.data_sources import DEMClient, DEMFinder
from geomulticorr.inversion.pytio.config import TIOConfig
from geomulticorr.inversion.tio_inversion import TIOInversion
from geomulticorr.stats import stats as gmc_stats

CC_THRESHOLD = 0.5
MAX_SLOPE_DEG = 40
#: Wide enough for two years of the fastest ice measured in these sets.
OUTLIER_BOUNDS_M = (-500, 500)


def fetch_dem(sensor: Sensor) -> Path:
    """Copernicus GLO-30 over the pzone, no account needed."""
    from pyproj import Transformer

    to_ll = Transformer.from_crs(sensor.site.epsg, 4326, always_xy=True)
    lonlat = to_ll.transform_bounds(*sensor.aoi_bounds)
    aoi = gpd.GeoDataFrame(geometry=[box(*lonlat)], crs=4326)
    return DEMFinder(DEMClient(), aoi, provider="cop30").download_dem(
        output_dir=sensor.work_dir.parent / f"{sensor.project}_dem", show_progress=False
    )


def create_project(sensor: Sensor):
    """A fresh project whose thumbs GMC itself makes from the image bank."""
    site = sensor.site
    project = sensor.work_dir
    if project.exists():
        shutil.rmtree(project)
    project.parent.mkdir(parents=True, exist_ok=True)
    session = open_gmc_session(project, epsg_code=site.epsg)
    session.insert_pzone(
        box(*sensor.aoi_bounds), site.pz_name, site.pz_shortname, crs=site.epsg
    )
    session.map_georasters_bank(sensor.bank_dir)
    written = session.sieve_bulk(
        target_resolution=sensor.resolution_m,
        selected_band=sensor.selected_band,
        pzone_filter=[site.pz_name],
        print_summary=False,
    )
    if not written.get(site.pz_name):
        raise RuntimeError(f"sieve_bulk wrote no thumb for {site.pz_name}")
    _require_full_coverage(session.pz_dir(site.pz_name, "optical"))
    session.update_thumbs()
    session.validate_all_thumbs()
    return session


def add_references(session, pz_name: str, moving_areas: Path, dem: Path) -> None:
    """Reference grid, DEM and moving areas, on the grid of the extracted maps.

    ``from_pair`` rather than ``resolution``: the grid derived from the pzone polygon can
    differ by a few columns from the pair grid, and ``SlopeMask`` then refuses every pair.
    """
    session.build_reference_grid(pz_name, from_pair=session.get_pairs()[0])
    session.build_reference_dem(pz_name, dem)
    session.load_moving_areas(pz_name, moving_areas)


def correlate(session, sensor: Sensor) -> None:
    """Same-sensor pairs, then ASP. Needs a local ASP install."""
    session.update_pairs(pz_name=sensor.site.pz_name, **sensor.pairing)
    session.prepare_pairs_correlation(
        print_summary=False, **sensor.correlation_kwargs()
    )
    session.launch_pairs_correlation(print_summary=False)


#: A thumb may have this fraction of empty pixels at most.
MAX_EMPTY_FRACTION = 0.01


def _require_full_coverage(optical: Path) -> None:
    """Stop when a thumb is partly empty: a scene footprint can claim an AOI its pixels miss.

    Landsat 196/028 did exactly that at Pierre Brune: the footprint covered the AOI, and the
    image data stopped short of its east side.
    """
    import numpy as np
    import rasterio

    for thumb in sorted(optical.glob("*.tif")):
        with rasterio.open(thumb) as src:
            data = src.read(1)
            empty = (
                ~np.isfinite(data)
                if data.dtype.kind == "f"
                else np.zeros(data.shape, bool)
            )
            if src.nodata is not None:
                empty |= data == src.nodata
        if empty.mean() > MAX_EMPTY_FRACTION:
            raise RuntimeError(
                f"{thumb.name}: {empty.mean():.0%} of the AOI is empty; choose another "
                "scene or a smaller AOI"
            )


def _require_all_ok(stage: str, results: dict) -> None:
    """Per-pair errors are only logged by the library; a dataset must not hide one."""
    failed = sorted(key for key, ok in (results or {}).items() if not ok)
    if not results or failed:
        raise RuntimeError(f"{stage}: failed pairs {failed or 'all (no result)'}")


def extract(session) -> None:
    results = session.extract_pairs_raw_displacements(
        save_plot=False, print_summary=False
    )
    _require_all_ok("extract", results)


def quality_pipeline():
    """What is removed from the maps themselves: bad pixels, never the moving ice."""
    return CCFilter(CC_THRESHOLD) + OutlierFilter(OUTLIER_BOUNDS_M)


def correct(session, pz_name: str) -> None:
    """Fit on stable ground only, and keep the moving ice in the corrected maps.

    Deliberately not ``apply_pairs_corrections``: it uses one pipeline both to choose the
    fit pixels and to mask its output, so ``StableAreaMask`` there erases the glacier (0 %
    of the ice survived on Mer de Glace). Files and stats are named as that method names them.
    """
    dem = session.get_pzones(pz_name)[0].get_dem()
    stable = (
        quality_pipeline()
        + SlopeMask(max_slope=MAX_SLOPE_DEG)
        + StableAreaMask(session.get_moving_areas(pz_name))
    )
    corrections = MedianCentering() + RampCorrection()
    pairs = session.get_pairs()
    for pair in pairs:
        x_raw = gu.Raster(str(pair.pa_ew_path), nodata=0)
        y_raw = gu.Raster(str(pair.pa_ns_path), nodata=0)
        cc = gu.Raster(str(pair.pa_cc_path))
        x_stable = stable.compute(x_raw, cc=cc, dem=dem)
        y_stable = stable.compute(y_raw, cc=cc, dem=dem)
        x_kept, y_kept = quality_pipeline().apply(x_raw, y_raw, cc=cc)
        x_corr, y_corr = make_corrections(
            x_kept, y_kept, corrections, x_stable=x_stable, y_stable=y_stable
        )
        x_corr.save(str(pair.pa_ew_corr_path))
        y_corr.save(str(pair.pa_ns_corr_path))
        pair.save_correction_masks(x_stable, y_stable, reference=x_raw)
        gmc_stats.save_corrected_stats(pair, x_corr, y_corr, "RampCorrection")
        gmc_stats.save_final_corrected_stats(pair, "RampCorrection")
    session.sync_corrected_stats(pairs=pairs, n_corrections=len(corrections.steps))


def invert(session, name: str = "reference") -> TIOInversion:
    """In-process pyTIO, so the stored result needs no Fortran binaries to reproduce."""
    inv = TIOInversion(
        session,
        session.get_pairs(),
        inversion_name=name,
        filter_pipeline=quality_pipeline(),
        tio_binaries_dir=None,
    )
    inv.prepare_inversion(
        weight_mode="quality_spatial",
        solver=TIOConfig(),
        print_summary=False,
    )
    inv.launch(mode="python")
    return inv


def run_all(sensor: Sensor):
    """Every stage in ``build/work/<project>``. Stops after extraction without an outline."""
    session = create_project(sensor)
    correlate(session, sensor)
    extract(session)
    outline = sensor.site.moving_areas
    if not outline.exists():
        print(f"STOPPED after extraction: no moving-area outline at {outline}")
        return session
    add_references(session, sensor.site.pz_name, outline, fetch_dem(sensor))
    correct(session, sensor.site.pz_name)
    invert(session)
    return session
