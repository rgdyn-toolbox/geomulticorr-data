# Publishing a data release

Run these by hand. Nothing in this repository pushes or publishes on its own.

## 1. What may be published

| Asset | Status |
|---|---|
| `bank_PierreBrune_sentinel2`, `MS_PierreBrune_Sentinel2` | Copernicus Sentinel data: free and open, with attribution |
| `bank_PierreBrune_landsat`, `MS_PierreBrune_Landsat` | USGS Landsat: public domain |
| `bank_PierreBrune_spot`, `MS_PierreBrune_SPOT` | **Hold.** IGN France-Ortho SPOT 6 products: confirm their redistribution terms first |
| `bank_PierreBrune_planetscope`, `MS_PierreBrune_Planet` | **Hold.** Planet images (E&R licence). Wait for Planet's written answer. If only derived results are allowed, set `results_only: true` for `planetscope` in `sites/PierreBrune/site.yaml` and rerun `export.py`: it writes placeholder thumbs (name and grid, no image) and does not export the bank. |

Also:

- [ ] Write the credit of the rock-glacier outline in `sites/PierreBrune/site.yaml`
      (`moving_areas_credit`), then re-export.
- [ ] Open a few rasters from each tarball before publishing.

## 2. Create the repository

`build/` is gitignored: it holds the whole scenes, the work projects and the tarballs.

```bash
cd ~/05_Devs/geomulticorr-data
git init -b main
git add .
git status               # must list no file under build/
git commit -m "add: Pierre Brune site, image banks and per-sensor GMC projects"
gh repo create rgdyn-toolbox/geomulticorr-data --public --source=. --push
```

## 3. Create the release

```bash
python scripts/package_release.py \
  bank_PierreBrune_sentinel2 MS_PierreBrune_Sentinel2 \
  bank_PierreBrune_landsat MS_PierreBrune_Landsat
gh release create v2 --title "Data v2: Pierre Brune" \
  --notes "Image banks and GMC projects per sensor. See README.md." \
  build/release/bank_PierreBrune_sentinel2.tar.gz build/release/MS_PierreBrune_Sentinel2.tar.gz \
  build/release/bank_PierreBrune_landsat.tar.gz build/release/MS_PierreBrune_Landsat.tar.gz
```

Add SPOT or Planet later with `gh release upload v2 …` once allowed. Never replace an asset in
place: `examples.py` pins the checksums, so a change means a new version (`v3`).

## 4. Pin it in GeoMultiCorr

Copy each `sha256` from `registry.json` into `REGISTRY` in `geomulticorr/examples.py`. With
`GMC_REQUIRE_DATA=1` (CI), an asset **in the registry** must download, or the tests fail. One left
out of the registry is skipped.

```bash
GMC_DATA_DIR=$(mktemp -d) pytest tests/realdata -m "realdata and not asp"
```

Commit the filled `REGISTRY` only after this passes.
