#!/usr/bin/env python3
"""
build-climatology.py — Build a per-cell × day-of-year temperature climatology
from E-OBS daily mean temperature, aligned to the European Heat Tracker's
population grid.

The output feeds two new headline indicators in fetch-dwd.py:

  • Temperature anomaly — how much hotter today is than the 1961-1990 average
    for this date, area- and population-weighted.
  • Above-average exposure — how many people are in cells where today's
    temperature exceeds the 90th percentile of 1961-1990 for this date.

Why E-OBS and 1961-1990?

  • E-OBS (ECA&D / Copernicus) is the de-facto European gridded daily station
    observation product. 0.1° (~10 km) matches ICON-EU's 6.5 km grid closely
    enough that bilinear interpolation onto our ~175k-cell population grid is
    essentially lossless.
  • 1961-1990 is the WMO reference for climate change assessment, used by
    DWD, MetOffice, EEA and IPCC. It conveys the warming signal more honestly
    than the operational 1991-2020 normal, which is itself already shifted.

Input
-----

You must download the E-OBS daily mean temperature (TG) file once, manually,
before running this script. Two options:

  Option A — Full ensemble mean (preferred, recommended by ECA&D):

    Visit:  https://surfobs.climate.copernicus.eu/dataaccess/access_eobs.php
    Choose: Daily / 0.1° / Ensemble mean / Mean temperature (TG)
    Click:  the most recent version (e.g. v30.0e, 1950-present)
    Save:   data/eobs-tg-0.1deg-v30.nc

  Option B — Direct HTTPS download (no registration; check filename for
  current version):

    curl -L -o data/eobs-tg-0.1deg-v30.nc \\
      'https://knmi-ecad-assets-prd.s3.amazonaws.com/ensembles/data/Grid_0.1deg_reg_ensemble/tg_ens_mean_0.1deg_reg_v30.0e.nc'

The file is ~5 GB. You only need to download it once. You can delete it after
this script has run — the climatology output is ~404 MB.

Output
------

data/climatology-1961-1990.npz with arrays:
  lats        float32 (N,)      — population-grid cell latitudes (matches
                                  data/population-grid.json order exactly)
  lons        float32 (N,)
  doy_mean    float32 (N, 366)  — per-cell, per-day-of-year mean Tmean over
                                  1961-1990, ±15-day smoothing window
  doy_p90     float32 (N, 366)  — same, but 90th percentile

Usage
-----
    python scripts/build-climatology.py [options]

    --eobs PATH          E-OBS NetCDF file (default: data/eobs-tg-0.1deg-v30.nc)
    --grid PATH          Population grid JSON (default: data/population-grid.json)
    --out PATH           Output NPZ (default: data/climatology-1961-1990.npz)
    --start YYYY         Climatology start year (default: 1961)
    --end YYYY           Climatology end year, inclusive (default: 1990)
    --window N           ±N-day smoothing window for doy stats (default: 15)
    --percentile P       Upper percentile to compute (default: 90)
    --verbose            Debug logging

Runtime: ~10-30 minutes on a laptop, depending on disk speed.

Dependencies:
    pip install xarray netCDF4 numpy scipy
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np

try:
    import xarray as xr
    HAS_XARRAY = True
except ImportError:
    HAS_XARRAY = False


def load_config():
    p = argparse.ArgumentParser(description='Build E-OBS climatology aligned to population grid')
    p.add_argument('--eobs', type=str, default='data/eobs-tg-0.1deg-v30.nc',
                   help='E-OBS daily mean temperature NetCDF file')
    p.add_argument('--grid', type=str, default='data/population-grid.json',
                   help='Population grid JSON (defines target cells)')
    p.add_argument('--out', type=str, default='data/climatology-1961-1990.npz',
                   help='Output NPZ path')
    p.add_argument('--start', type=int, default=1961, help='Climatology start year')
    p.add_argument('--end', type=int, default=1990, help='Climatology end year (inclusive)')
    p.add_argument('--window', type=int, default=15, help='±N-day smoothing window')
    p.add_argument('--percentile', type=float, default=90.0,
                   help='Upper percentile to compute (e.g. 90 or 95)')
    p.add_argument('--verbose', action='store_true')
    a = p.parse_args()

    level = logging.DEBUG if a.verbose else logging.INFO
    logging.basicConfig(level=level, format='%(asctime)s %(levelname)s %(message)s',
                        datefmt='%H:%M:%S')
    return a


def main():
    if not HAS_XARRAY:
        print('ERROR: xarray not installed.\n'
              '  pip install xarray netCDF4 numpy scipy', file=sys.stderr)
        sys.exit(1)

    args = load_config()
    project_root = Path(__file__).parent.parent
    eobs_path = project_root / args.eobs
    grid_path = project_root / args.grid
    out_path = project_root / args.out

    if not eobs_path.exists():
        print(f'ERROR: E-OBS file not found at {eobs_path}\n\n'
              f'Download it once from:\n'
              f'  https://surfobs.climate.copernicus.eu/dataaccess/access_eobs.php\n'
              f'  (Daily / 0.1° / Ensemble mean / Mean temperature TG)\n\n'
              f'Or direct:\n'
              f"  curl -L -o {eobs_path} \\\n"
              f"    'https://knmi-ecad-assets-prd.s3.amazonaws.com/ensembles/data/"
              f"Grid_0.1deg_reg_ensemble/tg_ens_mean_0.1deg_reg_v30.0e.nc'\n",
              file=sys.stderr)
        sys.exit(1)

    # ---- Load population grid -------------------------------------------------
    with open(grid_path) as f:
        pop_grid = json.load(f)
    cell_lats = np.array([c['lat'] for c in pop_grid], dtype=np.float32)
    cell_lons = np.array([c['lon'] for c in pop_grid], dtype=np.float32)
    n_cells = len(pop_grid)
    logging.info(f'Population grid: {n_cells:,} cells')

    # ---- Load E-OBS, slice to climatology window -----------------------------
    #
    # Memory strategy: the naive approach `tg.load()` on the 30-year slice
    # would pull ~14 GB into RAM (10957 days × 465 × 705 × 4 bytes), which
    # OOM-kills a 16 GB Mac. We instead stream year-by-year and process
    # the population grid in chunks. Peak memory stays around 1.5 GB:
    #
    #   - One year of E-OBS in memory at a time   (~480 MB)
    #   - One chunk × 30 years of per-cell series (~440 MB at chunk=10k)
    #   - Output doy_mean/doy_p90 arrays          (~500 MB combined)
    #
    # The trade-off is that each year's NetCDF slice is read once per chunk
    # (i.e. roughly 25× total for a 175k-cell grid), which adds runtime but
    # is reliable on commodity hardware.
    logging.info(f'Opening {eobs_path}...')
    ds = xr.open_dataset(eobs_path)

    var_name = next((v for v in ['tg', 'TG', 'tg_ens_mean'] if v in ds.data_vars), None)
    if var_name is None:
        raise RuntimeError(f'No tg/TG variable in {eobs_path}. Data vars: {list(ds.data_vars)}')
    tg = ds[var_name]

    # Slice to climatology period (lazy — no I/O yet)
    tg = tg.sel(time=slice(f'{args.start}-01-01', f'{args.end}-12-31'))
    times_all = tg.time.values.astype('datetime64[D]')
    n_days_total = len(times_all)
    logging.info(f'  Time slice: {times_all[0]} → {times_all[-1]} '
                 f'({n_days_total:,} days)')
    logging.info(f'  Spatial: {tg.shape[1:]} ({tg.latitude.values[0]:.2f}° → '
                 f'{tg.latitude.values[-1]:.2f}°, {tg.longitude.values[0]:.2f}° → '
                 f'{tg.longitude.values[-1]:.2f}°)')

    # Build a day-of-year array once for the full time slice (1..366)
    doy_all = ((times_all - times_all.astype('datetime64[Y]')).astype(int) + 1).astype(np.int32)
    n_doy = 366

    # Output arrays — these are the only large allocations we keep alive
    # for the whole run.
    doy_mean = np.full((n_cells, n_doy), np.nan, dtype=np.float32)
    doy_pct  = np.full((n_cells, n_doy), np.nan, dtype=np.float32)

    # Precompute (start_idx, end_idx) into the full time axis for each year,
    # so we can write each year's interpolated values into the chunk's series
    # buffer without scanning the time axis every iteration.
    years = list(range(args.start, args.end + 1))
    year_slices = {}
    for y in years:
        # Count days in this year by scanning the doy_all array
        year_mask = times_all.astype('datetime64[Y]').astype(int) + 1970 == y
        idxs = np.where(year_mask)[0]
        if len(idxs) == 0:
            continue
        year_slices[y] = (int(idxs[0]), int(idxs[-1]) + 1)
    logging.info(f'  Found {len(year_slices)} years in slice '
                 f'(first: {min(year_slices)}, last: {max(year_slices)})')

    # ---- Streaming interpolation, chunked over cells, resumable --------------
    #
    # Each chunk's per-cell × per-doy stats are written to disk as it
    # completes, in data/.climatology-chunks/. On restart, chunks whose
    # output file already exists are skipped — which means a laptop going
    # to sleep mid-run no longer loses hours of work; just re-invoke and
    # it picks up from the last completed chunk.
    CHUNK = 10_000  # cells per chunk; tune for memory if needed
    n_chunks = (n_cells + CHUNK - 1) // CHUNK
    chunks_dir = out_path.parent / '.climatology-chunks'
    chunks_dir.mkdir(parents=True, exist_ok=True)

    # Tag the per-chunk files with parameters so resuming after a parameter
    # change (window/percentile/period) doesn't accidentally re-use stale chunks.
    chunk_tag = f'{args.start}-{args.end}_w{args.window}_p{int(args.percentile)}'

    logging.info(f'Processing {n_cells:,} cells in {n_chunks} chunks of '
                 f'{CHUNK:,} (peak memory ~1.5 GB)...')
    logging.info(f'  Resume cache: {chunks_dir} (tag: {chunk_tag})')

    for ci, c0 in enumerate(range(0, n_cells, CHUNK)):
        c1 = min(c0 + CHUNK, n_cells)
        chunk_file = chunks_dir / f'chunk-{ci:03d}_{chunk_tag}.npz'

        if chunk_file.exists():
            # Resume: load previously-computed stats for this chunk
            cached = np.load(chunk_file)
            doy_mean[c0:c1] = cached['mean']
            doy_pct[c0:c1]  = cached['p90']
            n_valid = np.isfinite(doy_mean[c0:c1]).all(axis=1).sum()
            logging.info(f'  chunk {ci + 1:2d}/{n_chunks}  cells {c0:6d}-{c1:6d}  '
                         f'RESUMED ({n_valid:5d}/{c1 - c0} fully valid)')
            continue

        chunk_lats = xr.DataArray(cell_lats[c0:c1], dims='cell')
        chunk_lons = xr.DataArray(cell_lons[c0:c1], dims='cell')

        # Per-chunk series: all days for this chunk's cells
        chunk_series = np.full((n_days_total, c1 - c0), np.nan, dtype=np.float32)

        for y, (i0, i1) in year_slices.items():
            year_tg = tg.isel(time=slice(i0, i1)).load()  # ~480 MB
            year_interp = year_tg.interp(
                latitude=chunk_lats, longitude=chunk_lons, method='linear'
            ).values.astype(np.float32)
            chunk_series[i0:i1, :] = year_interp
            del year_tg, year_interp

        # Per-doy statistics for this chunk
        chunk_mean = np.full((c1 - c0, n_doy), np.nan, dtype=np.float32)
        chunk_p90  = np.full((c1 - c0, n_doy), np.nan, dtype=np.float32)
        for target_doy in range(1, n_doy + 1):
            # Circular ±window in doy space
            diff = np.abs(doy_all - target_doy)
            diff = np.minimum(diff, n_doy - diff)
            mask = diff <= args.window
            if not mask.any():
                continue
            window_vals = chunk_series[mask, :]  # (n_samples, chunk_size)
            chunk_mean[:, target_doy - 1] = np.nanmean(window_vals, axis=0)
            chunk_p90[:, target_doy - 1]  = np.nanpercentile(
                window_vals, args.percentile, axis=0
            )

        doy_mean[c0:c1] = chunk_mean
        doy_pct[c0:c1]  = chunk_p90
        del chunk_series

        # Persist this chunk's results immediately so a sleep / crash doesn't
        # lose progress. Atomic via temp + rename — the .tmp.npz suffix is
        # required because np.savez_compressed auto-appends .npz when the
        # path doesn't already end in it (had us writing to chunk.npz.tmp.npz
        # while trying to rename chunk.npz.tmp — silently broken).
        tmp = chunk_file.with_suffix('.tmp.npz')
        np.savez_compressed(tmp, mean=chunk_mean, p90=chunk_p90)
        tmp.rename(chunk_file)

        n_valid = np.isfinite(chunk_mean).all(axis=1).sum()
        logging.info(f'  chunk {ci + 1:2d}/{n_chunks}  cells {c0:6d}-{c1:6d}  '
                     f'({n_valid:5d}/{c1 - c0} fully valid)  → {chunk_file.name}')
        sys.stdout.flush()  # make tail -f reliable

    # ---- Save -----------------------------------------------------------------
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out_path,
        lats=cell_lats,
        lons=cell_lons,
        doy_mean=doy_mean,
        doy_p90=doy_pct,  # named p90 for consistency even if percentile differs
        meta=np.array(
            json.dumps({
                'reference_period': f'{args.start}-{args.end}',
                'dataset': 'E-OBS daily mean temperature (TG)',
                'source_file': str(eobs_path.name),
                'window_days': args.window,
                'percentile': args.percentile,
                'cells': n_cells,
                'doys': n_doy,
            }),
            dtype=object
        )
    )
    size_mb = out_path.stat().st_size / 1e6
    logging.info(f'Wrote {out_path} ({size_mb:.1f} MB)')

    # Quick sanity check
    n_valid = np.isfinite(doy_mean).all(axis=1).sum()
    logging.info(f'Cells with full 366-day climatology: {n_valid:,}/{n_cells:,} '
                 f'({100 * n_valid / n_cells:.1f}%)')
    logging.info(f'  (Cells outside E-OBS domain — e.g. west of ~25°W in Iceland '
                 f'— will have NaN climatologies and be skipped in aggregates.)')


if __name__ == '__main__':
    main()
