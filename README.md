# geomulticorr-data

Real data for testing and teaching [GeoMultiCorr](https://github.com/rgdyn-toolbox/GeoMultiCorr).

One **study site** at a time, and per site, **one GMC project per sensor**, built exactly as a
user builds one:

1. an **image bank**: raw images and their metadata, what `Session.map_georasters_bank` scans;
2. **`MS_<Site>_<Sensor>`**: a complete GMC project (geodatabase, QGIS mapset, pzone folders),
   made from that bank by GMC itself: `map_georasters_bank` → `sieve_bulk` → same-sensor pairs →
   ASP → extraction → corrections → pyTIO inversion.

Each project stores the output of every stage, so a test can start anywhere without ASP and
compare with a known result. Each is one tarball on the GitHub releases of this repository.

## Using the data

GeoMultiCorr downloads, verifies and installs an asset on first use:

```python
from geomulticorr import examples
from geomulticorr.core.session import open_gmc_session

bank = examples.get_bank("PierreBrune", "sentinel2")            # for map_georasters_bank
root = examples.get_project("MS_PierreBrune_Sentinel2", "work") # a writable copy
session = open_gmc_session(root)
```

`get_project` copies the project out of the cache, `~/.cache/geomulticorr/data/<version>/` or
`$GMC_DATA_DIR`, and rewrites the paths its geodatabase stores. A GMC geodatabase holds absolute
paths, so the shipped ones are placeholders: `@GMC_PROJECT@` for the project and `@GMC_BANK@` for
the image bank.

## Site: Pierre Brune rock glacier (Vanoise, 45.3793 N, 6.8506 E)

All pairs are **same-sensor**, joining consecutive dates in both directions (`forward-backward`).
Every project is in EPSG:2154 with pzone `PierreBrune`, centred on the rock glacier.

| Project | Bank (source) | Dates | AOI | Thumbs |
|---|---|---|---|---|
| `MS_PierreBrune_Sentinel2` | Sentinel-2 L2A B08, tile T31TGL R108 (UTM 31N), cropped to 12 km | 2018-08-28, 2020-08-27, 2022-09-01, 2025-09-07 | 8 km | 10 m |
| `MS_PierreBrune_Landsat` | Landsat 8 L1 B8, path/row 195/029 (UTM 32N), cropped to 13.5 km, MTL/ANG kept | 2015-08-30, 2018-09-23, 2021-08-14, 2025-08-09 | 9 km | 15 m |

**Motion signal.** Measured on the stored corrected maps and inverted series:

| Project | Fastest point (inverted series, 30 m radius) | Moving areas vs stable ground (corrected maps, median per forward pair) |
|---|---|---|
| Landsat | 19 m over 2015–2025, **1.9 m/yr**, towards the north-west | 0.8–1.0 × (no contrast) |
| Sentinel-2 | 10.5 m over 2018–2025, **1.5 m/yr**, towards the north-west | 0.7–1.1 × (no contrast) |

- **Both sensors see the rock glacier move north-west** at its fastest point (`rock_glacier`,
  EPSG:2154 x 1001120, y 6483170), while the `stable` point (x 1001932, y 6482162) moves 0–0.4 m.
- **The moving areas as a whole do not stand out** from stable ground: the outlines hold large slow
  parts, and at 10–15 m that contrast is lost. Test motion at the fastest point.
- **Whole-scene offsets:** the raw fields carry offsets between dates, up to about 7 m on
  Sentinel-2 2020→2022. `MedianCentering` removes them.

`python scripts/plot_site.py PierreBrune sentinel2` draws each sensor's series at the two points
(`build/figures/PierreBrune_time_series.png`).

### What a bank looks like

One band cropped to the bank extent, with the product metadata (Landsat keeps its MTL/ANG files).

## Building

Needs `gmc_env` and ASP. Everything comes from `sites/<Site>/site.yaml`.

```bash
python scripts/fetch_bank.py PierreBrune sentinel2 [path/to/.env]  # bank (M2M needs USGS_M2M_*)
python scripts/build_project.py PierreBrune sentinel2               # bank → MS_PierreBrune_Sentinel2
python scripts/export.py PierreBrune sentinel2                      # relocatable copies in build/datasets/
python scripts/package_release.py bank_PierreBrune_sentinel2 MS_PierreBrune_Sentinel2
python scripts/plot_site.py PierreBrune                             # time series of every sensor
```

Without `sites/<Site>/moving_areas.gpkg`, `build_project.py` stops after extraction: the
corrections are fitted on the stable ground outside that outline.

**Adding a site:** write `sites/<NewSite>/site.yaml`, adding a fetcher only for a new kind of
source, and a moving-area polygon, then run the same four commands.

Choices carried by the scripts, each learnt on real data:

- **Corrections:** fitted on stable ground with `make_corrections`, not `apply_pairs_corrections`.
  That method uses one pipeline both to choose the fit pixels and to mask its output, so
  `StableAreaMask` erases the moving ground.
- **Inversion:** filtered for quality only.
- **Searching:** explicit `corr_search` boxes.
- **Failures:** every stage stops on a failed pair.
- **Coverage:** a thumb more than 1 % empty stops the build. Landsat path 196/028's footprint
  claimed the AOI while its pixels did not reach the east side.

`scripts/check_dataset.py` refuses to package an asset that has any of these:

- an absolute path, in a text file or inside a geodatabase;
- a symlink;
- a credential-shaped string or a signed URL;
- an image-bearing ASP output in a results-only project;
- a cross-sensor pair;
- a file whose checksum does not match the manifest;
- a size over the limit.

`pytest tests/` tests that guard.

## Licences

The scripts are under the AGPL-3.0 (`LICENSE.md`). **That does not cover the data.**

Each asset has its own `LICENSE.txt`:
- the **source images** keep their providers' terms: Copernicus Sentinel data (free and open, with attribution) and USGS Landsat (public domain);
- the **reference DEM** is Copernicus DEM GLO-30, redistributed with its credit line;
- the **products made with GeoMultiCorr** are licensed
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

Release v2 holds the Sentinel-2 and Landsat banks and projects.

