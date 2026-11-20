"""Fill a site's image bank: raw images plus their metadata, as GMC will read them.

The bank is what ``Session.map_georasters_bank`` scans. It covers more than the project AOI,
so ``sieve_bulk`` has to crop, reproject and merge, exactly as on a real archive.

Usage: ``python scripts/fetch_bank.py <site> <sensor> [path/to/.env]`` (the ``.env`` holds the
credentials of sources that need an account, such as USGS M2M).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import rasterio
from pyproj import Transformer
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds
from shapely.geometry import box
from study_site import BUILD_DIR, Sensor, load_sensor

#: Optional credentials file, from the command line.
ENV_FILE = None

#: Whole scenes are staged here and never published; only their crops reach the bank.
DOWNLOAD_DIR = BUILD_DIR / "downloads"

#: Keyed by ``source.kind`` in site.yaml.
FETCHERS = {}


def fetcher(kind):
    def register(func):
        FETCHERS[kind] = func
        return func

    return register


@fetcher("planet_orders")
def fetch_planet_orders(sensor: Sensor) -> None:
    """Copy one item per order, both tiles and every metadata file, as Planet delivered them.

    The layout is kept: ``<order>/manifest.json`` and ``<order>/files/<item>_*``. Only the
    chosen item of each order is copied, so the order manifest also lists items left out.
    GeoMultiCorr finds the sensor through the order folder name (``Planet_…``, ``…psorthotile…``)
    or the ``<item>_metadata.json`` sidecar, since neither the file name nor ``files`` names it.
    """
    root = Path(sensor.source["root"])
    for order, item_prefix in sensor.source["orders"].items():
        source = root / order / "files"
        target = sensor.bank_dir / order / "files"
        target.mkdir(parents=True, exist_ok=True)
        files = sorted(source.glob(f"{item_prefix}*"))
        if not any(f.name.endswith("_SR_clip.tif") for f in files):
            raise FileNotFoundError(f"no item {item_prefix}* in {source}")
        for path in files:
            shutil.copy2(path, target / path.name)
        shutil.copy2(root / order / "manifest.json", target.parent / "manifest.json")


@fetcher("spot_dimap")
def fetch_spot_dimap(sensor: Sensor) -> None:
    """Crop each whole SPOT scene to the bank extent, keeping its name and DIMAP tree.

    The scenes are 3-5 GB each. The image is replaced by a crop with the same file name, and
    the XML metadata is copied as is, so it still describes the full scene: ``CROPPED.txt``
    says so. ``.aux.xml`` files are left out, since their statistics are those of the full scene.
    """
    root = Path(sensor.source["root"])
    bounds = sensor.square_bounds(sensor.source["bank_km"])
    for year in sensor.source["years"]:
        products = sorted((root / str(year)).iterdir())
        if len(products) != 1:
            raise RuntimeError(f"expected one SPOT product for {year}: {products}")
        product = products[0]
        target = sensor.bank_dir / str(year) / product.name
        for path in sorted(product.rglob("*")):
            if not path.is_file() or path.name.lower().endswith(".aux.xml"):
                continue
            out = target / path.relative_to(product)
            out.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix.lower() in (".tif", ".jp2"):
                _crop(
                    path,
                    out.with_suffix(".tif") if path.suffix.lower() == ".jp2" else out,
                    bounds,
                    sensor.site.epsg,
                )
            else:
                shutil.copy2(path, out)
        (target / "CROPPED.txt").write_text(
            f"Image cropped to {bounds} (EPSG:{sensor.site.epsg}) for the GeoMultiCorr test "
            "data. The DIMAP and ISO metadata are the originals and describe the full scene.\n"
        )


@fetcher("mpc")
def fetch_mpc(sensor: Sensor) -> None:
    """Planetary Computer, no account: one band cropped to the bank extent, plus metadata."""
    from geomulticorr.data_sources import MPC_ImageCollectionCatalog, MPC_ImageFinder

    src = sensor.source
    years = sorted(int(d[:4]) for d in src["dates"])
    finder = MPC_ImageFinder(
        MPC_ImageCollectionCatalog.default(),
        aoi=box(*_bank_lonlat(sensor)),
        year_range=(years[0], years[-1]),
        cloud_max=100,
    )
    scenes = finder.search_metadata([src["collection"]])
    for date, scene in _one_scene_per_date(scenes, src).items():
        finder.bulk_download(
            scene,
            output_dir=sensor.bank_dir / date,
            mode="crop",
            bands_override=[src["band"]],
            download_metadata=True,
            show_progress=False,
        )


@fetcher("m2m")
def fetch_m2m(sensor: Sensor) -> None:
    """USGS M2M (USGS_M2M_* from the environment or .env): one band plus MTL/ANG.

    The band is a whole scene, so it is downloaded to ``build/downloads/`` and only its crop
    to the bank extent goes into the bank, under its original name.
    """
    import geopandas as gpd
    from dotenv import load_dotenv

    from geomulticorr.data_sources import M2MClient, M2MImageFinder

    load_dotenv(ENV_FILE) if ENV_FILE else load_dotenv()
    src = sensor.source
    years = sorted(int(d[:4]) for d in src["dates"])
    client = M2MClient()
    client.login()
    aoi = gpd.GeoDataFrame(geometry=[box(*_bank_lonlat(sensor))], crs=4326)
    finder = M2MImageFinder(
        client, aoi=aoi, year_range=(years[0], years[-1]), cloud_max=100
    )
    # L8_L1 and L9_L1 are one M2M dataset: one key returns both missions.
    scenes = finder.search_metadata([src["collection"]]).drop_duplicates("image_id")
    bounds = sensor.square_bounds(src["bank_km"])
    for date, scene in _one_scene_per_date(scenes, src).items():
        staging = DOWNLOAD_DIR / sensor.project / date
        band_suffix = f"_{src['band']}.TIF"
        if not any(
            p.name.upper().endswith(band_suffix) for p in staging.rglob("*")
        ) or not any("_MTL" in p.name.upper() for p in staging.rglob("*")):
            finder.download_images(
                scene,
                output_dir=staging,
                bands=[src["band"]],
                download_metadata=True,
                show_progress=False,
                keep_original_names=True,
                max_poll_attempts=60,
            )
        target = sensor.bank_dir / date
        target.mkdir(parents=True, exist_ok=True)
        for path in sorted(p for p in staging.rglob("*") if p.is_file()):
            if path.name.upper().endswith(band_suffix):
                _crop(path, target / path.name, bounds, sensor.site.epsg)
            elif path.suffix.lower() in (".txt", ".xml") and not path.name.endswith(
                ".aux.xml"
            ):
                shutil.copy2(path, target / path.name)


def _bank_lonlat(sensor: Sensor) -> tuple[float, float, float, float]:
    to_ll = Transformer.from_crs(sensor.site.epsg, 4326, always_xy=True)
    return to_ll.transform_bounds(*sensor.square_bounds(sensor.source["bank_km"]))


def _one_scene_per_date(scenes, src: dict) -> dict:
    """The scene of each wanted date whose name holds every ``match`` token (latest processing)."""
    name = "display_id" if "display_id" in scenes else "image_id"
    keep = scenes[name].astype(str)
    mask = keep.str.len() > 0
    for token in src.get("match", []):
        mask &= keep.str.contains(token, regex=False)
    picked = scenes[mask].copy()
    picked["date"] = picked["acquisition_date"].astype(str).str[:10]
    out = {}
    for date in src["dates"]:
        same_day = picked[picked["date"] == date].sort_values(name)
        if same_day.empty:
            raise RuntimeError(f"no scene matching {src.get('match')} on {date}")
        out[date] = same_day.tail(1)
    return out


def _crop(source: Path, target: Path, bounds, bounds_epsg: int) -> None:
    with rasterio.open(source) as src:
        native = transform_bounds(f"EPSG:{bounds_epsg}", src.crs, *bounds)
        window = from_bounds(*native, transform=src.transform)
        window = window.round_offsets().round_lengths()
        data = src.read(window=window)
        profile = src.profile | {
            "driver": "GTiff",
            "width": window.width,
            "height": window.height,
            "transform": src.window_transform(window),
            "compress": "deflate",
            "predictor": 2,
            "tiled": True,
            "blockxsize": 512,
            "blockysize": 512,
        }
    with rasterio.open(target, "w", **profile) as dst:
        dst.write(data)


def fetch(sensor: Sensor) -> Path:
    kind = sensor.source["kind"]
    if kind not in FETCHERS:
        raise ValueError(f"unknown source kind {kind!r}; known: {sorted(FETCHERS)}")
    FETCHERS[kind](sensor)
    return sensor.bank_dir


def main(argv: list[str]) -> None:
    global ENV_FILE
    if len(argv) not in (3, 4):
        raise SystemExit(__doc__)
    if len(argv) == 4:
        ENV_FILE = argv[3]
    print(fetch(load_sensor(argv[1], argv[2])))


if __name__ == "__main__":
    main(sys.argv)
