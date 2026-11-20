"""A study site, read from ``sites/<name>/site.yaml``: one image bank and one project per sensor.

Everything a build needs comes from that file, so adding a site is a matter of writing one
YAML file and a moving-area polygon next to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pyproj import Transformer

REPO_ROOT = Path(__file__).resolve().parents[1]
SITES_DIR = REPO_ROOT / "sites"
BUILD_DIR = REPO_ROOT / "build"
BANK_DIR = BUILD_DIR / "bank"
WORK_DIR = BUILD_DIR / "work"
DATASETS_DIR = BUILD_DIR / "datasets"
RELEASE_DIR = BUILD_DIR / "release"
REGISTRY_PATH = REPO_ROOT / "registry.json"

#: Bumped by hand when any asset changes; matches GeoMultiCorr's ``examples.DATA_VERSION``.
DATA_VERSION = "v2"
#: One asset must stay small enough for a quick CI download (CI caches it). With corrections
#: and inversion stored, the 3 m SPOT project is the largest, at 205 MB.
MAX_ASSET_MB = 210

COPERNICUS_DEM_CREDIT = (
    "Reference DEM: Copernicus DEM GLO-30, produced using Copernicus WorldDEM-30 "
    "© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018, provided under "
    "COPERNICUS by the European Union and ESA; all rights reserved."
)


@dataclass(frozen=True)
class Sensor:
    """One sensor of a site: where its images come from and how its project is built."""

    key: str
    site: "Site"
    project: str
    source: dict
    aoi_km: float
    resolution_m: float
    selected_band: int
    pairing: dict
    correlation: dict
    licence: str
    attribution: str
    results_only: bool = False

    @property
    def bank_dir(self) -> Path:
        return BANK_DIR / self.site.name / self.key

    @property
    def bank_asset(self) -> str:
        return f"bank_{self.site.name}_{self.key}"

    @property
    def work_dir(self) -> Path:
        return WORK_DIR / self.project

    def square_bounds(self, size_km: float) -> tuple[float, float, float, float]:
        """A square of *size_km* centred on the site, snapped to the thumb resolution."""
        res = self.resolution_m
        half = size_km * 1000 / 2
        x, y = self.site.center_xy
        left = round((x - half) / res) * res
        bottom = round((y - half) / res) * res
        side = round(size_km * 1000 / res) * res
        return left, bottom, left + side, bottom + side

    @property
    def aoi_bounds(self) -> tuple[float, float, float, float]:
        return self.square_bounds(self.aoi_km)

    @property
    def credits(self) -> tuple[str, ...]:
        return (COPERNICUS_DEM_CREDIT, self.site.moving_areas_credit)

    def correlation_kwargs(self) -> dict:
        """YAML lists become the tuples ``prepare_pairs_correlation`` expects."""
        return {
            k: tuple(v) if isinstance(v, list) else v
            for k, v in self.correlation.items()
        }


@dataclass(frozen=True)
class Site:
    name: str
    pz_name: str
    pz_shortname: str
    center_lonlat: tuple[float, float]
    epsg: int
    moving_areas: Path
    moving_areas_credit: str
    sensors: dict[str, Sensor] = field(default_factory=dict, compare=False)

    @property
    def center_xy(self) -> tuple[float, float]:
        to_xy = Transformer.from_crs(4326, self.epsg, always_xy=True)
        return to_xy.transform(*self.center_lonlat)


def load_site(name: str) -> Site:
    folder = SITES_DIR / name
    raw = yaml.safe_load((folder / "site.yaml").read_text())
    site = Site(
        name=raw["name"],
        pz_name=raw["pz_name"],
        pz_shortname=raw["pz_shortname"],
        center_lonlat=tuple(raw["center_lonlat"]),
        epsg=int(raw["epsg"]),
        moving_areas=folder / raw["moving_areas"],
        moving_areas_credit=raw["moving_areas_credit"],
    )
    if "_" in site.pz_name:
        raise ValueError(f"{name}: pz_name {site.pz_name!r} must not contain '_'")
    for key, spec in raw["sensors"].items():
        site.sensors[key] = Sensor(key=key, site=site, **spec)
    return site


def load_sensor(site_name: str, sensor_key: str) -> Sensor:
    site = load_site(site_name)
    if sensor_key not in site.sensors:
        raise ValueError(
            f"{site_name}: no sensor {sensor_key!r}; have {list(site.sensors)}"
        )
    return site.sensors[sensor_key]
