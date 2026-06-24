#!/usr/bin/env python3
"""
fetch-dwd.py — Download DWD ICON-EU GRIB2 data and store in heat-tracker SQLite DB.

Replaces the Open-Meteo API with raw data from DWD's open data server.
Downloads temperature, humidity and wind for the current analysis run plus
optional forecast hours, computes two heat indicators (air temperature and
Steadman apparent temperature), and writes snapshots to the same SQLite
schema used by the SvelteKit app.

Usage:
    python scripts/fetch-dwd.py [options]

Options:
    --threshold CELSIUS    Heat threshold in °C (default: HEAT_THRESHOLD env or 30)
    --forecast-hours N     Forecast hours to download 0–72, hourly (default: 72)
                           Use 0 for analysis only (current conditions).
    --db PATH              SQLite database path (default: data/heat-tracker.db)
    --grid PATH            Population grid JSON (default: data/population-grid.json)
    --verbose              Enable debug logging

Dependencies:
    pip install cfgrib xarray scipy requests python-dotenv
    brew install eccodes   # macOS
    apt-get install libeccodes-dev  # Debian/Ubuntu

ICON-EU domain: 23.5°W–45.0°E, 29.5°N–70.5°N
Iceland cells west of 23.5°W receive NULL values (out of domain).
"""

import argparse
import bz2
import gzip
import json
import logging
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests
from scipy.interpolate import RegularGridInterpolator

try:
    import cfgrib
    import xarray as xr
    HAS_CFGRIB = True
except ImportError:
    HAS_CFGRIB = False

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DWD_BASE = 'https://opendata.dwd.de/weather/nwp/icon-eu/grib'
RUN_HOURS = [0, 3, 6, 9, 12, 15, 18, 21]
PUBLISH_LAG_HOURS = 3  # files available ~3h after nominal run time

VARIABLES = {
    't_2m':      {'dir': 't_2m',      'file': 'T_2M'},
    'relhum_2m': {'dir': 'relhum_2m', 'file': 'RELHUM_2M'},
    'u_10m':     {'dir': 'u_10m',     'file': 'U_10M'},
    'v_10m':     {'dir': 'v_10m',     'file': 'V_10M'},
}

# SQLite schema — must stay in sync with src/lib/server/db.ts
# New columns are added via ALTER TABLE migrations so existing databases are
# upgraded automatically (see run_migrations below).
CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    total_affected INTEGER NOT NULL DEFAULT 0,
    total_population INTEGER NOT NULL DEFAULT 0,
    threshold_celsius REAL NOT NULL DEFAULT 35.0,
    is_forecast INTEGER NOT NULL DEFAULT 0,
    model_run_time TEXT,
    -- Climatology-based headline indicators (vs. 1961-1990 E-OBS reference).
    -- Populated only when build-climatology.py has produced a climatology file
    -- and it is loaded at startup. NULL when climatology is unavailable.
    mean_anomaly_c REAL,            -- area-mean temperature anomaly, °C (per-snapshot)
    -- DEPRECATED, always 0. "Uncommonly hot" exposure is a daily-mean property
    -- (daily mean > climatological p90 of daily means) computed authoritatively
    -- per day in write_current_json → current-*.json. A per-snapshot count would
    -- over-count, so this column is not populated and must not be served.
    pop_above_avg INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS grid_data (
    snapshot_id INTEGER NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    country TEXT NOT NULL,
    population INTEGER NOT NULL,
    temperature REAL,
    apparent_temperature REAL,
    is_affected INTEGER NOT NULL DEFAULT 0,
    anomaly_c REAL,                            -- per-snapshot temperature − climatology daily mean; AVG'd per day for the anomaly map
    is_above_avg INTEGER NOT NULL DEFAULT 0,   -- DEPRECATED, always 0 (see snapshots.pop_above_avg note)
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
);

CREATE INDEX IF NOT EXISTS idx_grid_snapshot ON grid_data(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_grid_country ON grid_data(snapshot_id, country);

CREATE TABLE IF NOT EXISTS country_aggregates (
    snapshot_id INTEGER NOT NULL,
    country TEXT NOT NULL,
    population INTEGER NOT NULL,
    affected INTEGER NOT NULL DEFAULT 0,
    max_temperature REAL,
    max_apparent_temperature REAL,
    avg_temperature REAL,
    avg_anomaly_c REAL,                        -- area-mean anomaly per country
    pop_above_avg INTEGER NOT NULL DEFAULT 0,  -- people in cells above climatology p90
    PRIMARY KEY (snapshot_id, country),
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
);

-- Forecast accuracy log: one row per (valid_time, model_run_time) pair.
-- Written just before a forecast snapshot is overwritten by a fresher forecast
-- or by analysis. Joining with snapshots on valid_time WHERE is_forecast=0
-- gives forecast vs. actual for any lead time.
CREATE TABLE IF NOT EXISTS forecast_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    valid_time TEXT NOT NULL,
    model_run_time TEXT NOT NULL,
    lead_hours REAL NOT NULL,
    total_affected INTEGER,
    threshold_celsius REAL,
    superseded_at TEXT NOT NULL,
    UNIQUE(valid_time, model_run_time)
);
"""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_config():
    parser = argparse.ArgumentParser(
        description='Fetch DWD ICON-EU GRIB2 weather data for European Heat Tracker'
    )
    parser.add_argument(
        '--threshold', type=float,
        default=float(os.environ.get('HEAT_THRESHOLD', '30')),
        help='Heat threshold in °C (default: HEAT_THRESHOLD env or 30)'
    )
    parser.add_argument(
        '--forecast-hours', type=int, default=72,
        metavar='N',
        help='Hours of forecast to download [0–72, hourly] (default: 72; 0=analysis only)'
    )
    parser.add_argument(
        '--backfill-today', action='store_true',
        help=(
            'Before processing the latest run, fetch the +000h analysis step from '
            'every earlier completed run of today. Fills in analysis snapshots for '
            'past hours that were previously stored as forecasts.'
        )
    )
    parser.add_argument(
        '--db', type=str, default='data/heat-tracker.db',
        help='SQLite database path relative to project root (default: data/heat-tracker.db)'
    )
    parser.add_argument(
        '--grid', type=str, default='data/population-grid.json',
        help='Population grid JSON path (default: data/population-grid.json)'
    )
    parser.add_argument(
        '--climatology', type=str, default='data/climatology-1961-1990.npz',
        help='Climatology NPZ from build-climatology.py. If missing, the new '
             'anomaly/above-avg indicators are skipped silently and the legacy '
             'pipeline runs unchanged. (default: data/climatology-1961-1990.npz)'
    )
    parser.add_argument('--verbose', action='store_true', help='Enable debug logging')
    parser.add_argument(
        '--geojson-only', action='store_true',
        help='Skip the DWD download/fetch and only regenerate the pre-generated '
             'grid GeoJSON files from the existing database. Useful after changing '
             'the GeoJSON schema (e.g. adding region/anomaly fields).'
    )
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s %(levelname)s %(message)s',
        datefmt='%H:%M:%S'
    )

    args.forecast_hours = max(0, min(72, args.forecast_hours))
    return args


# ---------------------------------------------------------------------------
# Model run detection
# ---------------------------------------------------------------------------

def build_variable_url(run_date, run_hour, var_key, forecast_hour):
    """Construct the DWD open data URL for one variable/timestep."""
    v = VARIABLES[var_key]
    datestr = run_date.strftime('%Y%m%d')
    run_str = f'{run_hour:02d}'
    fhh = f'{forecast_hour:03d}'
    timestamp = f'{datestr}{run_str}'
    filename = (
        f'icon-eu_europe_regular-lat-lon_single-level_{timestamp}_{fhh}_{v["file"]}.grib2.bz2'
    )
    return f'{DWD_BASE}/{run_str}/{v["dir"]}/{filename}'


def probe_url(url, timeout=15):
    """Return True if URL exists (HTTP 200/301/302)."""
    try:
        r = requests.head(url, timeout=timeout, allow_redirects=True)
        return r.status_code == 200
    except Exception:
        return False


def determine_model_run():
    """
    Walk back through ICON-EU run hours to find the most recently published run.
    Verifies existence with a HEAD request on the t_2m file before returning.
    """
    utcnow = datetime.now(timezone.utc)
    for delta_days in range(3):
        day = utcnow.date() - timedelta(days=delta_days)
        for h in reversed(RUN_HOURS):
            candidate = datetime(day.year, day.month, day.day, h, tzinfo=timezone.utc)
            if candidate > utcnow:
                continue
            age_hours = (utcnow - candidate).total_seconds() / 3600
            if age_hours < PUBLISH_LAG_HOURS:
                logging.debug(f'Run {day} {h:02d}Z too recent ({age_hours:.1f}h < {PUBLISH_LAG_HOURS}h lag)')
                continue
            test_url = build_variable_url(day, h, 't_2m', 0)
            logging.debug(f'Probing {test_url}')
            if probe_url(test_url):
                logging.info(f'Using ICON-EU run {day} {h:02d}Z (age {age_hours:.1f}h)')
                return day, h
            else:
                logging.debug(f'Run {day} {h:02d}Z not yet available')

    raise RuntimeError(
        'Could not find a valid published ICON-EU run after 3 days of searching. '
        'Check DWD open data availability: https://opendata.dwd.de/weather/nwp/icon-eu/grib/'
    )


# ---------------------------------------------------------------------------
# Download and parse
# ---------------------------------------------------------------------------

def download_and_decompress(url, dest_path, retries=3):
    """Download a .bz2 GRIB2 file, decompress and save to dest_path."""
    for attempt in range(retries):
        try:
            logging.debug(f'  GET {url}')
            r = requests.get(url, timeout=180, stream=True)
            r.raise_for_status()
            compressed = r.content
            decompressed = bz2.decompress(compressed)
            with open(dest_path, 'wb') as f:
                f.write(decompressed)
            size_kb = len(decompressed) // 1024
            logging.debug(f'  → {size_kb} KB decompressed')
            return
        except Exception as e:
            if attempt < retries - 1:
                wait = 5 * (attempt + 1)
                logging.warning(f'  Attempt {attempt + 1}/{retries} failed: {e} — retrying in {wait}s')
                time.sleep(wait)
            else:
                raise RuntimeError(f'Failed to download after {retries} attempts: {url}') from e


def load_grib_variable(path):
    """
    Load a GRIB2 file and return {'lats', 'lons', 'values'}.

    Uses cfgrib.open_datasets() to handle DWD's non-standard GRIB parameter IDs.
    Handles: descending latitudes, [0,360] longitudes.
    """
    if not HAS_CFGRIB:
        raise RuntimeError('cfgrib not installed. Run: pip install cfgrib xarray')

    datasets = cfgrib.open_datasets(path, indexpath=None, decode_timedelta=False)
    if not datasets:
        raise ValueError(f'No datasets found in {path}')

    # Pick the dataset with the most data variables (usually just one)
    ds = max(datasets, key=lambda d: len(d.data_vars))
    var_name = list(ds.data_vars)[0]
    da = ds[var_name]

    if 'latitude' not in da.coords or 'longitude' not in da.coords:
        raise ValueError(f'Expected latitude/longitude coords in {path}, got: {list(da.coords)}')

    lats = da.coords['latitude'].values.copy()
    lons = da.coords['longitude'].values.copy()
    values = da.values.copy()

    # Ensure 2D (some files have extra time/level dim)
    if values.ndim > 2:
        values = values.reshape(values.shape[-2], values.shape[-1])

    # Fix descending latitudes (GRIB stores north→south, RegularGridInterpolator needs ascending)
    if lats[0] > lats[-1]:
        lats = lats[::-1]
        values = values[::-1, :]

    # Fix [0, 360] longitude convention → [-180, 180]
    if lons.max() > 180:
        shift = np.searchsorted(lons, 180.0)
        lons = np.concatenate([lons[shift:] - 360.0, lons[:shift]])
        values = np.concatenate([values[:, shift:], values[:, :shift]], axis=1)

    if not np.all(np.diff(lats) > 0):
        raise ValueError(f'Latitude still not monotonically increasing after processing {path}')
    if not np.all(np.diff(lons) > 0):
        raise ValueError(f'Longitude still not monotonically increasing after processing {path}')

    return {'lats': lats, 'lons': lons, 'values': values.astype(np.float32)}


def build_interpolator(grib_data):
    """Build a bilinear RegularGridInterpolator. NaN for out-of-domain points."""
    return RegularGridInterpolator(
        (grib_data['lats'], grib_data['lons']),
        grib_data['values'],
        method='linear',
        bounds_error=False,
        fill_value=np.nan
    )


def interpolate_to_grid(interp, pop_grid):
    """Interpolate to all ~175k population grid points. Returns float32 array."""
    points = np.array([[cell['lat'], cell['lon']] for cell in pop_grid], dtype=np.float32)
    return interp(points).astype(np.float32)


# ---------------------------------------------------------------------------
# Temperature indicators
# ---------------------------------------------------------------------------

def compute_apparent_temperature(t2m_c, rh_pct, u10, v10):
    """
    Steadman (1994) apparent temperature — same formula as Open-Meteo's apparent_temperature.

    AT = Ta + 0.33·e − 0.70·ws − 4.00
    where:
      Ta = 2m air temperature (°C)
      e  = vapour pressure (hPa) = (rh/100) × 6.105 × exp(17.27·Ta / (237.7 + Ta))
      ws = 10m wind speed (m/s)

    Reference: Steadman (1994), BAMS. Used by the Australian Bureau of Meteorology.
    """
    rh = np.clip(rh_pct, 0.0, 100.0)
    e = (rh / 100.0) * 6.105 * np.exp(17.27 * t2m_c / (237.7 + t2m_c))
    ws = np.sqrt(u10 ** 2 + v10 ** 2)
    return t2m_c + 0.33 * e - 0.70 * ws - 4.00


# ---------------------------------------------------------------------------
# Climatology (E-OBS 1961-1990 baseline)
# ---------------------------------------------------------------------------

def load_climatology(path):
    """
    Load the per-cell × day-of-year climatology produced by build-climatology.py.

    Returns a dict {doy_mean, doy_p90, reference_period} or None if the file is
    absent. Returning None lets the rest of the pipeline run unchanged — the new
    indicators just stay NULL in the database and the existing exposure flow is
    untouched. This makes the climatology a strictly additive feature.
    """
    if not path.exists():
        logging.info(f'Climatology file not found at {path} — anomaly/above-avg '
                     f'indicators will be NULL. Run scripts/build-climatology.py '
                     f'to populate them.')
        return None
    try:
        npz = np.load(path, allow_pickle=True)
        meta = json.loads(npz['meta'].item())
        clim = {
            'doy_mean': npz['doy_mean'].astype(np.float32),  # (n_cells, 366)
            'doy_p90':  npz['doy_p90'].astype(np.float32),
            'reference_period': meta.get('reference_period', 'unknown'),
            'percentile': meta.get('percentile', 90),
        }
        logging.info(f'Climatology loaded: {path.name} '
                     f'(ref {clim["reference_period"]}, p{clim["percentile"]:g}, '
                     f'{clim["doy_mean"].shape})')
        return clim
    except Exception as e:
        logging.warning(f'Failed to load climatology from {path}: {e}. '
                        f'Anomaly indicators will be NULL.')
        return None


def doy_index(valid_time):
    """Day-of-year (0-365) for climatology array lookup."""
    return valid_time.timetuple().tm_yday - 1


# ---------------------------------------------------------------------------
# Pre-generated grid GeoJSON
# ---------------------------------------------------------------------------

_NUTS_LOOKUP = None


def load_nuts_lookup():
    """
    Map (round(lat,3), round(lon,3)) -> {'region': <granular NUTS name>,
    'regionBroad': <NUTS-1 name>} for every tagged population-grid cell.

    Built once from data/population-grid.json (cell order) and
    data/nuts-by-cell.json (region tags in the same order, produced by
    scripts/build-nuts-grid.py). Returns {} if either file is missing, in which
    case the region fields are simply omitted from the GeoJSON and the frontend
    falls back to the country name alone.
    """
    global _NUTS_LOOKUP
    if _NUTS_LOOKUP is not None:
        return _NUTS_LOOKUP

    data_dir = Path(__file__).resolve().parent.parent / 'data'
    pg_path = data_dir / 'population-grid.json'
    nuts_path = data_dir / 'nuts-by-cell.json'
    lookup = {}
    if pg_path.exists() and nuts_path.exists():
        try:
            with open(pg_path) as f:
                pg = json.load(f)
            with open(nuts_path) as f:
                nuts = json.load(f)
            for cell, tag in zip(pg, nuts):
                if not tag:
                    continue
                region = tag.get('c') or tag.get('b') or tag.get('a')
                broad = tag.get('a')
                if not region:
                    continue
                entry = {'region': region}
                if broad and broad != region:
                    entry['regionBroad'] = broad
                lookup[(round(cell['lat'], 3), round(cell['lon'], 3))] = entry
            logging.info(f'NUTS lookup loaded: {len(lookup):,} cells tagged')
        except Exception as e:
            logging.warning(f'Failed to load NUTS lookup: {e}. Region omitted.')
    else:
        logging.info('NUTS lookup not found — region omitted from GeoJSON. '
                     'Run scripts/build-nuts-grid.py to enable it.')

    _NUTS_LOOKUP = lookup
    return _NUTS_LOOKUP


def write_geojson_with_gzip(output_path, geojson):
    """
    Write a GeoJSON object to `output_path` AND a pre-compressed `output_path.gz`,
    both atomically. The app serves the .gz directly with Content-Encoding: gzip,
    so the first visitor after a data refresh never waits for on-the-fly
    compression of the ~45 MB payload.
    """
    raw = json.dumps(geojson, separators=(',', ':'))

    tmp = str(output_path) + '.tmp'
    with open(tmp, 'w') as f:
        f.write(raw)
    os.replace(tmp, str(output_path))

    gz_path = str(output_path) + '.gz'
    gz_tmp = gz_path + '.tmp'
    with gzip.open(gz_tmp, 'wb', compresslevel=6) as f:
        f.write(raw.encode('utf-8'))
    os.replace(gz_tmp, gz_path)

    return os.path.getsize(str(output_path)), os.path.getsize(gz_path)


def write_grid_geojson(db_path, output_path):
    """
    Write today's grid data as a compact GeoJSON file with MIN/MAX per cell.

    Aggregates all non-forecast snapshots from today (UTC 00:00–23:59) so the
    tooltip can show a meaningful temperature range across the day. Falls back
    to the single latest snapshot when today has only one (or none yet).
    Written atomically via a temp file to avoid partial reads.
    """
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    today_from = dt.datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=dt.timezone.utc).isoformat()
    today_to   = dt.datetime(now.year, now.month, now.day, 23, 59, 59, tzinfo=dt.timezone.utc).isoformat()

    conn = sqlite3.connect(str(db_path))

    # For each of today's timestamps, prefer the analysis row (is_forecast=0) when
    # available; otherwise use the best forecast (e.g. from yesterday's run).
    # This gives a full-day temperature range from the very first hour, which
    # improves throughout the day as forecasts are replaced by analyses.
    snapshot_ids = [
        r[0] for r in conn.execute(
            '''SELECT id FROM snapshots s
               WHERE s.timestamp >= ? AND s.timestamp <= ?
                 AND (s.is_forecast = 0 OR NOT EXISTS (
                   SELECT 1 FROM snapshots s2
                   WHERE s2.timestamp = s.timestamp AND s2.is_forecast = 0
                 ))''',
            (today_from, today_to)
        ).fetchall()
    ]

    # Fall back to single latest snapshot when today's data isn't available yet
    if not snapshot_ids:
        row = conn.execute(
            'SELECT id FROM snapshots ORDER BY timestamp DESC LIMIT 1'
        ).fetchone()
        if not row:
            conn.close()
            logging.warning('No snapshots found — skipping grid GeoJSON generation')
            return
        snapshot_ids = [row[0]]

    placeholders = ','.join('?' * len(snapshot_ids))
    cells = conn.execute(
        f'''SELECT lat, lon, country, MAX(population) AS population,
                   MAX(temperature) AS temperature,
                   MAX(apparent_temperature) AS apparent_temperature,
                   MIN(temperature) AS min_temperature,
                   MIN(apparent_temperature) AS min_apparent_temperature,
                   AVG(anomaly_c) AS anomaly_c
            FROM grid_data
            WHERE snapshot_id IN ({placeholders}) AND country != 'TR'
            GROUP BY lat, lon''',
        snapshot_ids
    ).fetchall()

    # SQLite: with exactly one MAX() aggregate, bare columns come from the row
    # that produced that maximum — so peak_hour_utc reliably reflects when each
    # cell was hottest, not an arbitrary row.
    peak_rows = conn.execute(
        f'''SELECT gd.lat, gd.lon, MAX(gd.temperature) AS max_temp,
                   substr(s.timestamp, 12, 5) AS peak_hour_utc
            FROM grid_data gd
            JOIN snapshots s ON s.id = gd.snapshot_id
            WHERE gd.snapshot_id IN ({placeholders}) AND gd.country != 'TR'
            GROUP BY gd.lat, gd.lon''',
        snapshot_ids
    ).fetchall()
    peak_hour_by_cell = {(r[0], r[1]): r[3] for r in peak_rows}

    conn.close()

    # Only include min values when there are multiple snapshots (range is meaningful)
    has_range = len(snapshot_ids) > 1

    # NOTE: NUTS region names are deliberately NOT embedded per feature here.
    # With ~175k features the region strings bloated the payload and slowed the
    # client's queryRenderedFeatures (every hover copies feature properties).
    # The region for a single hovered cell is fetched on demand from /api/region.

    features = []
    for lat, lon, country, pop, temp, at, min_temp, min_at, anomaly in cells:
        props = {
            'country': country,
            'population': pop,
            # lat/lon stored at 3dp so /api/region lookup always matches the
            # population-grid.json keys regardless of MapLibre tile round-trip.
            'lat': round(lat, 3),
            'lon': round(lon, 3),
            'temperature':         round(temp,     1) if temp     is not None else None,
            'apparentTemperature': round(at,       1) if at       is not None else None,
            # Daily-mean temperature anomaly vs. the 1961-1990 climatology,
            # averaged across the day's snapshots so the diurnal cycle cancels.
            'anomalyC':            round(anomaly,  1) if anomaly  is not None else None,
            'peakHour':            peak_hour_by_cell.get((lat, lon)),
        }
        if has_range:
            props['minTemperature']         = round(min_temp,     1) if min_temp     is not None else None
            props['minApparentTemperature'] = round(min_at,       1) if min_at       is not None else None

        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [round(lon, 4), round(lat, 4)]},
            'properties': props,
        })

    geojson = {'type': 'FeatureCollection', 'features': features}
    size, gz_size = write_geojson_with_gzip(output_path, geojson)
    logging.info(
        f'Grid GeoJSON: {output_path} ({len(features):,} features, '
        f'{len(snapshot_ids)} snapshot(s), {size // 1024:,} KB, '
        f'{gz_size // 1024:,} KB gzipped)'
    )


def write_grid_geojson_for_range(db_path, output_path, days_back=None, days_forward=None):
    """
    Write a GeoJSON file aggregating peak indicator values per grid cell over a
    time window. Used for the last7d and next3d presets so those API responses
    can be served from disk without hitting the database.
    """
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    fmt = '%Y-%m-%dT%H:%M:%S.000Z'
    if days_back is not None:
        from_ts = (now - datetime.timedelta(days=days_back)).strftime(fmt)
        to_ts = now.strftime(fmt)
    elif days_forward is not None:
        from_ts = now.strftime(fmt)
        to_ts = (now + datetime.timedelta(days=days_forward)).strftime(fmt)
    else:
        logging.warning('write_grid_geojson_for_range: no window specified — skipping')
        return

    conn = sqlite3.connect(str(db_path))
    # Prefer analysis snapshots (is_forecast=0) over forecast snapshots for any
    # given timestamp — only fall back to forecast when no analysis exists yet
    # (i.e. future timestamps).  This prevents stale forecast values from
    # inflating/deflating the min-max range for past timestamps.
    rows = conn.execute(
        '''SELECT id FROM snapshots s
           WHERE s.timestamp >= ? AND s.timestamp <= ?
             AND (s.is_forecast = 0 OR NOT EXISTS (
               SELECT 1 FROM snapshots s2
               WHERE s2.timestamp = s.timestamp AND s2.is_forecast = 0
             ))''',
        (from_ts, to_ts)
    ).fetchall()
    if not rows:
        conn.close()
        logging.warning(f'No snapshots in range {from_ts} – {to_ts}, skipping {output_path}')
        return

    snapshot_ids = [r[0] for r in rows]
    placeholders = ','.join('?' * len(snapshot_ids))

    cells = conn.execute(
        f'''SELECT lat, lon, country, MAX(population) AS population,
                   MAX(temperature) AS temperature,
                   MAX(apparent_temperature) AS apparent_temperature,
                   MIN(temperature) AS min_temperature,
                   MIN(apparent_temperature) AS min_apparent_temperature,
                   AVG(anomaly_c) AS anomaly_c
            FROM grid_data
            WHERE snapshot_id IN ({placeholders}) AND country != 'TR'
            GROUP BY lat, lon''',
        snapshot_ids
    ).fetchall()
    conn.close()

    features = []
    for lat, lon, country, pop, temp, at, min_temp, min_at, anomaly in cells:
        props = {
            'country': country,
            'population': pop,
            'lat': round(lat, 3),
            'lon': round(lon, 3),
            'temperature':            round(temp,         1) if temp         is not None else None,
            'apparentTemperature':    round(at,           1) if at           is not None else None,
            'anomalyC':               round(anomaly,      1) if anomaly      is not None else None,
            'minTemperature':         round(min_temp,     1) if min_temp     is not None else None,
            'minApparentTemperature': round(min_at,       1) if min_at       is not None else None,
        }
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [round(lon, 4), round(lat, 4)]},
            'properties': props,
        })

    geojson = {'type': 'FeatureCollection', 'features': features}
    size, gz_size = write_geojson_with_gzip(output_path, geojson)
    logging.info(
        f'Range GeoJSON: {output_path} ({len(features):,} features, '
        f'{size // 1024:,} KB, {gz_size // 1024:,} KB gzipped)'
    )


def write_current_json(db_path, output_dir, threshold, climatology=None, pop_grid=None):
    """
    Pre-generate /api/current JSON responses for the today, last7d, and next3d
    presets and write them to data/current-{preset}.json.

    These files are served statically by the SvelteKit app (fast-path in
    src/routes/api/current/+server.ts) so that the first visitor after a data
    refresh is never waiting for a live DB query.

    When climatology + pop_grid are provided, the headline indicators
    (mean_anomaly_c, pop_above_avg) are computed from per-cell DAILY-MEAN
    temperatures across the window — apples-to-apples with the E-OBS daily
    Tmean climatology. This is more scientifically defensible than the
    per-snapshot anomaly stored in grid_data, which inherits the diurnal
    cycle bias (afternoon snapshot vs daily-mean climatology = systematic
    +3-5°C inflation in summer). For multi-day windows the climatology is
    averaged over the window's days-of-year before comparing.

    The third indicator ("X exposed above {threshold}°C") deliberately stays
    on per-snapshot peak exposure (totalAffected) — that's a Tmax-style
    question, and a cell that briefly peaks above 30°C is genuinely briefly
    exposed even if the daily mean is moderate.
    """
    import datetime as dt

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Available range
    row = conn.execute(
        'SELECT MIN(timestamp) as from_ts, MAX(timestamp) as to_ts, '
        'MAX(is_forecast) as has_forecast FROM snapshots'
    ).fetchone()
    available_range = None
    if row and row['from_ts']:
        available_range = {
            'from': row['from_ts'],
            'to': row['to_ts'],
            'hasForecast': bool(row['has_forecast']),
        }

    now = dt.datetime.now(dt.timezone.utc)
    presets = {
        'today': (
            dt.datetime(now.year, now.month, now.day, 0, 0, 0,
                        tzinfo=dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z'),
            dt.datetime(now.year, now.month, now.day, 23, 59, 59,
                        tzinfo=dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z'),
        ),
        'last7d': (
            (now - dt.timedelta(days=7)).strftime('%Y-%m-%dT%H:%M:%S.000Z'),
            now.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
        ),
        'next3d': (
            now.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
            (now + dt.timedelta(days=3)).strftime('%Y-%m-%dT%H:%M:%S.000Z'),
        ),
    }

    # Subquery: prefer analysis (is_forecast=0) over forecast for same timestamp.
    best_sq = (
        'SELECT id FROM snapshots s'
        ' WHERE s.timestamp >= ? AND s.timestamp <= ?'
        '   AND (s.is_forecast = 0 OR NOT EXISTS ('
        '     SELECT 1 FROM snapshots s2'
        '     WHERE s2.timestamp = s.timestamp AND s2.is_forecast = 0'
        '   ))'
    )

    for preset_name, (from_ts, to_ts) in presets.items():
        # Period summary
        stats = conn.execute(
            '''SELECT COUNT(DISTINCT id)       AS snapshot_count,
                      MIN(timestamp)           AS oldest_timestamp,
                      MAX(timestamp)           AS newest_timestamp,
                      MAX(total_population)    AS total_population,
                      MAX(is_forecast)         AS has_forecast,
                      MAX(model_run_time)      AS latest_model_run_time,
                      MAX(fetched_at)          AS latest_fetched_at
               FROM snapshots
               WHERE timestamp >= ? AND timestamp <= ?''',
            (from_ts, to_ts)
        ).fetchone()

        if not stats or not stats['snapshot_count']:
            logging.warning(f'write_current_json: no data for {preset_name} — skipping')
            continue

        # Total affected (cells above threshold at any point in the window)
        affected_row = conn.execute(
            f'''SELECT SUM(population) AS total_affected
                FROM (
                    SELECT lat, lon, population,
                           CASE WHEN MAX(temperature) >= ? THEN 1 ELSE 0 END AS was_affected
                    FROM grid_data
                    WHERE snapshot_id IN ({best_sq}) AND country != 'TR'
                    GROUP BY lat, lon
                )
                WHERE was_affected = 1''',
            (threshold, from_ts, to_ts)
        ).fetchone()

        # Diurnal-coverage guard for the climatology headlines. The anomaly and
        # "uncommonly hot" figures compare a per-cell DAILY MEAN (AVG of the
        # window's hourly snapshots) against the E-OBS daily-mean climatology.
        # That is only apples-to-apples if the window actually samples the full
        # diurnal cycle — a daytime-only partial day (e.g. obs from 06–18 UTC
        # with no forecast filling the night) yields a warm-biased "daily mean"
        # and an inflated anomaly. We require ≥20 distinct hours per covered UTC
        # day; below that we suppress the climatology headlines (meanAnomalyC →
        # null, popAboveAvg → 0) rather than publish a biased figure. The
        # threshold count is unaffected — it is an explicit any-hour-peak metric.
        coverage = conn.execute(
            '''SELECT COUNT(DISTINCT timestamp)              AS n_hours,
                      COUNT(DISTINCT substr(timestamp, 1, 10)) AS n_days
               FROM snapshots WHERE timestamp >= ? AND timestamp <= ?''',
            (from_ts, to_ts)
        ).fetchone()
        n_hours = coverage['n_hours'] or 0
        n_days  = max(1, coverage['n_days'] or 1)
        coverage_ok = n_hours >= 20 * n_days
        if not coverage_ok:
            logging.warning(
                f'write_current_json: {preset_name} has thin diurnal coverage '
                f'({n_hours} hours over {n_days} day(s); need ≥{20 * n_days}) — '
                f'suppressing climatology headlines to avoid a warm-biased anomaly.'
            )

        # Climatology window: average per-cell climatology over each day-of-year
        # that the window covers. For 'today' this is exactly one doy (so no
        # averaging); for 'last7d' it averages 8 doys, etc. Doing it this way
        # means the climatology is locally appropriate even when the window
        # straddles a season change (e.g. a window crossing 20-30 September).
        clim_mean_window = None
        clim_p90_window = None
        cell_idx_lookup = None
        if climatology is not None and pop_grid is not None and coverage_ok:
            from_dt = dt.datetime.fromisoformat(from_ts.replace('Z', '+00:00'))
            to_dt   = dt.datetime.fromisoformat(to_ts.replace('Z', '+00:00'))
            day = from_dt.date()
            doys_in_window = set()
            while day <= to_dt.date():
                doys_in_window.add(day.timetuple().tm_yday - 1)  # 0-indexed
                day += dt.timedelta(days=1)
            doys_arr = np.array(sorted(doys_in_window))
            # Window-averaged climatology — uses nanmean so single-doy NaNs
            # don't poison the whole window for a cell that's mostly in-domain.
            clim_mean_window = np.nanmean(climatology['doy_mean'][:, doys_arr], axis=1)
            clim_p90_window  = np.nanmean(climatology['doy_p90'][:,  doys_arr], axis=1)
            cell_idx_lookup = {
                (round(c['lat'], 4), round(c['lon'], 4)): i
                for i, c in enumerate(pop_grid)
            }

        # Country aggregates (mirrors getCountryAggregatesForRange in weather.ts).
        # avg_temp here is AVG(temperature) per cell across the window's snapshots
        # — i.e. the per-cell DAILY MEAN, which is what we use for the
        # climatology-anomaly headlines. max_temp / max_apparent_temperature
        # stay snapshot-peak (Tmax-style), feeding the "exposed above
        # threshold" indicator.
        country_rows = conn.execute(
            f'''SELECT country, lat, lon, MAX(population) AS population,
                       CASE WHEN MAX(temperature) >= ? THEN 1 ELSE 0 END AS was_affected,
                       MAX(temperature)               AS max_temp,
                       MAX(apparent_temperature)      AS max_app_temp,
                       AVG(temperature)               AS avg_temp
                FROM grid_data
                WHERE snapshot_id IN ({best_sq}) AND country != 'TR'
                GROUP BY country, lat, lon''',
            (threshold, from_ts, to_ts)
        ).fetchall()

        # Roll cell-level rows up to per-country AND Europe-wide aggregates in
        # a single Python pass. The climatology anomaly is computed per-cell
        # as (daily_mean − window_climatology_mean), and is_above_avg as
        # (daily_mean > window_climatology_p90) — both apples-to-apples now.
        country_agg = {}
        europe_anom_sum = 0.0
        europe_anom_count = 0
        europe_pop_above_avg = 0
        for r in country_rows:
            c = r['country']
            pop = r['population']
            daily_mean = r['avg_temp']  # per-cell daily mean (or window mean)

            # Climatology lookup for this cell (if available)
            cell_anomaly = None
            cell_above_avg = False
            if clim_mean_window is not None and daily_mean is not None:
                ci = cell_idx_lookup.get((round(r['lat'], 4), round(r['lon'], 4)))
                if ci is not None:
                    cm = clim_mean_window[ci]
                    cp = clim_p90_window[ci]
                    if np.isfinite(cm):
                        cell_anomaly = float(daily_mean - cm)
                    if np.isfinite(cp):
                        cell_above_avg = daily_mean > cp

            ca = country_agg.setdefault(c, {
                'population': 0, 'affected': 0,
                'maxTemp': None, 'maxAppTemp': None,
                'tempSum': 0.0, 'tempCount': 0,
                'popAboveAvg': 0,
                'anomSum': 0.0, 'anomCount': 0,
            })
            ca['population'] += pop
            if r['was_affected']:
                ca['affected'] += pop
            if cell_above_avg:
                ca['popAboveAvg'] += pop
                europe_pop_above_avg += pop
            if cell_anomaly is not None:
                ca['anomSum']   += cell_anomaly
                ca['anomCount'] += 1
                europe_anom_sum   += cell_anomaly
                europe_anom_count += 1
            if r['max_temp']     is not None:
                ca['maxTemp']    = max(ca['maxTemp'], r['max_temp'])    if ca['maxTemp']    is not None else r['max_temp']
                ca['tempSum']   += r['avg_temp'] if r['avg_temp'] is not None else r['max_temp']
                ca['tempCount'] += 1
            if r['max_app_temp'] is not None:
                ca['maxAppTemp'] = max(ca['maxAppTemp'], r['max_app_temp']) if ca['maxAppTemp'] is not None else r['max_app_temp']

        mean_anomaly_c = (
            europe_anom_sum / europe_anom_count if europe_anom_count > 0 else None
        )
        pop_above_avg = europe_pop_above_avg

        # Convert to the row-shape the JSON serializer below expects
        class _Row(dict):
            __getitem__ = dict.__getitem__
        sorted_countries = sorted(
            country_agg.items(),
            key=lambda kv: (-kv[1]['affected'], -(kv[1]['maxAppTemp'] or -999))
        )
        country_rows = [
            _Row({
                'country': country,
                'population': ca['population'],
                'affected': ca['affected'],
                'max_temperature': ca['maxTemp'],
                'max_apparent_temperature': ca['maxAppTemp'],
                'avg_temperature': ca['tempSum'] / ca['tempCount'] if ca['tempCount'] else None,
                'pop_above_avg': ca['popAboveAvg'],
                'avg_anomaly_c': (ca['anomSum'] / ca['anomCount']) if ca['anomCount'] else None,
            })
            for country, ca in sorted_countries
        ]

        result = {
            'snapshot': {
                'timestamp':        stats['newest_timestamp'],
                'totalAffected':    affected_row['total_affected'] or 0,
                'totalPopulation':  stats['total_population'] or 0,
                'thresholdCelsius': threshold,
                'snapshotCount':    stats['snapshot_count'],
                'oldestTimestamp':  stats['oldest_timestamp'],
                'hasForecast':      bool(stats['has_forecast']),
                'modelRunTime':     stats['latest_model_run_time'],
                # When our cron actually ingested this data. SQLite datetime('now')
                # is UTC in 'YYYY-MM-DD HH:MM:SS' form; normalise to ISO-with-Z so
                # the browser parses it as UTC, not local time.
                'fetchedAt':        (stats['latest_fetched_at'].replace(' ', 'T') + 'Z')
                                    if stats['latest_fetched_at'] else None,
                # Climatology-based headline indicators. Null/0 if the
                # climatology file was not present when this snapshot was
                # fetched — so the API can branch on the existence of
                # meanAnomalyC to decide whether to render the new headlines.
                'meanAnomalyC':     round(mean_anomaly_c, 2) if mean_anomaly_c is not None else None,
                'popAboveAvg':      int(pop_above_avg),
                'referencePeriod':  '1961-1990',
            },
            'countries': [
                {
                    'country':               r['country'],
                    'population':            r['population'],
                    'affected':              r['affected'] or 0,
                    'maxTemperature':        round(r['max_temperature'], 1)
                                             if r['max_temperature'] is not None else None,
                    'maxApparentTemperature': round(r['max_apparent_temperature'], 1)
                                             if r['max_apparent_temperature'] is not None else None,
                    'avgTemperature':        round(r['avg_temperature'], 1)
                                             if r['avg_temperature'] is not None else None,
                    'avgAnomalyC':           round(r['avg_anomaly_c'], 2)
                                             if r['avg_anomaly_c'] is not None else None,
                    'popAboveAvg':           int(r['pop_above_avg'] or 0),
                }
                for r in country_rows
            ],
            'indicator': 'temperature',
            'period':    preset_name,
            'from':      from_ts,
            'to':        to_ts,
            'availableRange': available_range,
        }

        output_path = Path(output_dir) / f'current-{preset_name}.json'
        tmp = str(output_path) + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(result, f, separators=(',', ':'))
        os.replace(tmp, str(output_path))
        size_kb = os.path.getsize(str(output_path)) // 1024
        logging.info(
            f'Current JSON: {output_path} '
            f'({len(result["countries"])} countries, {size_kb} KB)'
        )

    conn.close()


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def run_migrations(conn):
    """
    Ensure all tables exist and new columns are present.
    Safe to run on existing databases — uses CREATE IF NOT EXISTS and
    catches 'duplicate column' errors from ALTER TABLE.
    """
    conn.executescript(CREATE_TABLES_SQL)
    migrations = [
        ('snapshots',           'is_forecast',     'INTEGER NOT NULL DEFAULT 0'),
        ('snapshots',           'model_run_time',   'TEXT'),
        # Climatology-based indicators (E-OBS 1961-1990 baseline). All nullable;
        # populated only when a climatology file is available at fetch time.
        ('snapshots',           'mean_anomaly_c',   'REAL'),
        ('snapshots',           'pop_above_avg',    'INTEGER NOT NULL DEFAULT 0'),
        ('grid_data',           'anomaly_c',        'REAL'),
        ('grid_data',           'is_above_avg',     'INTEGER NOT NULL DEFAULT 0'),
        ('country_aggregates',  'avg_anomaly_c',    'REAL'),
        ('country_aggregates',  'pop_above_avg',    'INTEGER NOT NULL DEFAULT 0'),
    ]
    for table, col, typedef in migrations:
        try:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {col} {typedef}')
            conn.commit()
            logging.debug(f'Migration applied: {table}.{col} {typedef}')
        except sqlite3.OperationalError as e:
            if 'duplicate column' in str(e).lower():
                pass  # column already exists — expected on second run
            else:
                raise


def write_snapshot(db_path, pop_grid, t2m_c, apparent_temp,
                   valid_time, model_run_time, is_forecast, threshold,
                   climatology=None):
    """
    Write one snapshot to the SQLite database.

    is_affected is computed from temperature (the default indicator) so that
    total_affected in the snapshots table reflects temperature-based exposure.
    The API can recompute affected counts dynamically for other indicators.

    When `climatology` is provided (loaded by load_climatology), per-cell
    anomaly_c is computed and stored: t2m_c minus the per-cell climatological
    daily mean for valid_time's day-of-year. The anomaly map averages these
    across the day's snapshots (AVG(anomaly_c)) to recover the daily-mean
    anomaly. The is_above_avg / pop_above_avg columns are NOT populated here —
    "uncommonly hot" exposure is a daily-mean property computed per day in
    write_current_json (see the schema notes). Cells with NaN climatology
    (outside the E-OBS domain) keep anomaly_c=NULL.
    """
    now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    valid_iso = valid_time.strftime('%Y-%m-%dT%H:%M:%S.000Z')
    run_iso = model_run_time.strftime('%Y-%m-%dT%H:%M:%S.000Z')

    # Climatology lookup for this valid_time's doy (vectorised across all cells).
    # We store the per-snapshot anomaly (instantaneous t2m − climatological daily
    # mean); the map's "difference" view averages these across the day's snapshots
    # (AVG(anomaly_c)) to recover the daily-mean anomaly, which is correct.
    if climatology is not None:
        di = doy_index(valid_time)
        clim_mean = climatology['doy_mean'][:, di]   # (n_cells,)
        anomaly_arr = t2m_c - clim_mean
    else:
        anomaly_arr = np.full(len(pop_grid), np.nan, dtype=np.float32)

    # "Uncommonly hot" (population above the 1961-1990 p90) is a DAILY-MEAN
    # property: today's per-cell daily mean vs. the climatological p90 of daily
    # means. A single hourly snapshot cannot represent it — comparing an
    # instantaneous temperature against a percentile of *daily means* would badly
    # over-count (an afternoon peak clears the daily-mean p90 far more often than
    # a daily mean does). The authoritative figure is computed per day from
    # AVG(temperature) in write_current_json (served via current-*.json). These
    # per-snapshot is_above_avg / pop_above_avg columns are therefore left at 0
    # and must NOT be used as an exposure proxy.
    above_avg_arr = np.zeros(len(pop_grid), dtype=np.int8)

    conn = sqlite3.connect(str(db_path))
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    run_migrations(conn)

    # Deduplicate: if a snapshot for this exact valid_time already exists, delete
    # it (plus its grid_data and country_aggregates) before inserting fresh data.
    # The most recently fetched model run always supersedes older forecast rows for
    # the same valid timestamp, keeping exactly one row per unique moment in time.
    #
    # Before deleting a forecast snapshot, write its aggregate to forecast_log so
    # we can later compare any lead-time forecast against the eventual analysis.
    conn.row_factory = sqlite3.Row
    existing = conn.execute(
        '''SELECT id, is_forecast, total_affected, threshold_celsius, model_run_time
           FROM snapshots WHERE timestamp = ?''', (valid_iso,)
    ).fetchone()
    if existing:
        old_id = existing['id']
        if existing['is_forecast']:
            old_run = existing['model_run_time']
            valid_dt = datetime.fromisoformat(valid_iso.replace('Z', '+00:00'))
            run_dt   = datetime.fromisoformat(old_run.replace('Z', '+00:00'))
            lead_h   = (valid_dt - run_dt).total_seconds() / 3600
            conn.execute(
                '''INSERT OR IGNORE INTO forecast_log
                   (valid_time, model_run_time, lead_hours, total_affected, threshold_celsius, superseded_at)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (valid_iso, old_run, lead_h,
                 existing['total_affected'], existing['threshold_celsius'], now_iso)
            )
        conn.execute('DELETE FROM grid_data WHERE snapshot_id = ?', (old_id,))
        conn.execute('DELETE FROM country_aggregates WHERE snapshot_id = ?', (old_id,))
        conn.execute('DELETE FROM snapshots WHERE id = ?', (old_id,))
        conn.commit()
        logging.info(f'  Deduplicating: replaced snapshot #{old_id} for {valid_iso}')

    cur = conn.execute(
        '''INSERT INTO snapshots
           (timestamp, fetched_at, total_affected, total_population,
            threshold_celsius, is_forecast, model_run_time,
            mean_anomaly_c, pop_above_avg)
           VALUES (?, ?, 0, 0, ?, ?, ?, NULL, 0)''',
        (valid_iso, now_iso, threshold, 1 if is_forecast else 0, run_iso)
    )
    snapshot_id = cur.lastrowid

    country_stats = {}
    total_affected = 0
    total_population = 0
    # Europe-wide area-mean anomaly accumulator
    anom_sum = 0.0
    anom_count = 0
    total_above_avg = 0
    rows = []

    for i, cell in enumerate(pop_grid):
        t = None if np.isnan(t2m_c[i]) else float(t2m_c[i])
        at = None if np.isnan(apparent_temp[i]) else float(apparent_temp[i])
        anom = None if np.isnan(anomaly_arr[i]) else float(anomaly_arr[i])
        above = int(above_avg_arr[i])
        # is_affected uses temperature as the default heat indicator
        is_affected = 1 if (t is not None and t >= threshold) else 0

        rows.append((
            snapshot_id, cell['lat'], cell['lon'], cell['country'],
            cell['pop'], t, at, is_affected, anom, above
        ))

        total_population += cell['pop']
        if is_affected:
            total_affected += cell['pop']
        if above:
            total_above_avg += cell['pop']
        if anom is not None:
            anom_sum   += anom
            anom_count += 1

        cs = country_stats.setdefault(cell['country'], {
            'population': 0, 'affected': 0,
            'maxT': None, 'maxAT': None,
            'tempSum': 0.0, 'tempCount': 0,
            'anomSum': 0.0, 'anomCount': 0,
            'popAboveAvg': 0,
        })
        cs['population'] += cell['pop']
        if is_affected:
            cs['affected'] += cell['pop']
        if above:
            cs['popAboveAvg'] += cell['pop']
        if anom is not None:
            cs['anomSum']   += anom
            cs['anomCount'] += 1
        if t is not None:
            cs['maxT'] = max(cs['maxT'], t) if cs['maxT'] is not None else t
            cs['tempSum'] += t
            cs['tempCount'] += 1
        if at is not None:
            cs['maxAT'] = max(cs['maxAT'], at) if cs['maxAT'] is not None else at

    europe_mean_anomaly = (
        anom_sum / anom_count if anom_count > 0 else None
    )

    with conn:
        conn.executemany(
            '''INSERT INTO grid_data
               (snapshot_id, lat, lon, country, population,
                temperature, apparent_temperature, is_affected,
                anomaly_c, is_above_avg)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            rows
        )
        conn.execute(
            '''UPDATE snapshots
               SET total_affected=?, total_population=?,
                   mean_anomaly_c=?, pop_above_avg=?
               WHERE id=?''',
            (total_affected, total_population,
             europe_mean_anomaly, total_above_avg, snapshot_id)
        )
        conn.executemany(
            '''INSERT OR REPLACE INTO country_aggregates
               (snapshot_id, country, population, affected,
                max_temperature, max_apparent_temperature, avg_temperature,
                avg_anomaly_c, pop_above_avg)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            [
                (
                    snapshot_id, country, cs['population'], cs['affected'],
                    cs['maxT'], cs['maxAT'],
                    cs['tempSum'] / cs['tempCount'] if cs['tempCount'] > 0 else None,
                    cs['anomSum'] / cs['anomCount'] if cs['anomCount'] > 0 else None,
                    cs['popAboveAvg'],
                )
                for country, cs in country_stats.items()
            ]
        )

    conn.close()
    return snapshot_id, total_affected, total_population


# ---------------------------------------------------------------------------
# Per-run processing
# ---------------------------------------------------------------------------

def process_run(model_run_time, forecast_steps, pop_grid, climatology, db_path, threshold):
    """Download, interpolate and store one model run's forecast steps."""
    run_date = model_run_time.date()
    run_hour = model_run_time.hour
    snapshots_written = 0

    for fhour in forecast_steps:
        valid_time = model_run_time + timedelta(hours=fhour)
        is_forecast = fhour > 0
        label = f'+{fhour:03d}h ({"forecast" if is_forecast else "analysis"})'
        logging.info(f'--- {label}: valid {valid_time.strftime("%Y-%m-%d %H:%M")}Z ---')

        with tempfile.TemporaryDirectory() as tmpdir:
            grib_data = {}
            download_failed = False

            for var_key in VARIABLES:
                url = build_variable_url(run_date, run_hour, var_key, fhour)
                dest = os.path.join(tmpdir, f'{var_key}.grib2')
                logging.info(f'  Downloading {var_key}...')
                try:
                    download_and_decompress(url, dest)
                    grib_data[var_key] = load_grib_variable(dest)
                    logging.debug(f'  {var_key}: {grib_data[var_key]["values"].shape} grid')
                except Exception as e:
                    logging.error(f'  {var_key} FAILED: {e}')
                    if var_key == 't_2m':
                        logging.error('  t_2m is required — skipping this timestep')
                        download_failed = True
                        break
                    else:
                        logging.warning(f'  {var_key} unavailable — using fallback values')
                        grib_data[var_key] = None

            if download_failed:
                continue

            logging.info('  Interpolating to population grid...')

            interp_t = build_interpolator(grib_data['t_2m'])
            t2m_k = interpolate_to_grid(interp_t, pop_grid)
            t2m_c = t2m_k - 273.15  # CRITICAL: Kelvin → Celsius

            if grib_data.get('relhum_2m'):
                rh = np.clip(interpolate_to_grid(build_interpolator(grib_data['relhum_2m']), pop_grid), 0, 100)
            else:
                rh = np.full(len(pop_grid), 50.0, dtype=np.float32)

            if grib_data.get('u_10m'):
                u10 = interpolate_to_grid(build_interpolator(grib_data['u_10m']), pop_grid)
            else:
                u10 = np.zeros(len(pop_grid), dtype=np.float32)

            if grib_data.get('v_10m'):
                v10 = interpolate_to_grid(build_interpolator(grib_data['v_10m']), pop_grid)
            else:
                v10 = np.zeros(len(pop_grid), dtype=np.float32)

            logging.info('  Computing heat indicators...')
            apparent_temp = compute_apparent_temperature(t2m_c, rh, u10, v10)

            snapshot_id, total_affected, total_pop = write_snapshot(
                db_path, pop_grid, t2m_c, apparent_temp,
                valid_time, model_run_time, is_forecast, threshold,
                climatology=climatology
            )
            pct = (total_affected / total_pop * 100) if total_pop > 0 else 0
            logging.info(
                f'  → Snapshot #{snapshot_id}: {total_affected:,} / {total_pop:,} '
                f'above {threshold}°C ({pct:.1f}%)'
            )
            snapshots_written += 1

    return snapshots_written


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = load_config()

    # Resolve paths relative to project root (this script lives in scripts/)
    project_root = Path(__file__).parent.parent
    db_path = project_root / args.db
    grid_path = project_root / args.grid
    clim_path = project_root / args.climatology
    data_dir = project_root / 'data'

    # --geojson-only: regenerate the pre-generated grid GeoJSON files from the
    # existing database and exit. No DWD download, so cfgrib is not required.
    if args.geojson_only:
        if not db_path.exists():
            logging.error(f'Database not found at {db_path}')
            sys.exit(1)
        logging.info('Regenerating pre-generated files from existing database…')
        write_grid_geojson(db_path, data_dir / 'grid-latest.geojson')
        write_grid_geojson_for_range(db_path, data_dir / 'grid-last7d.geojson', days_back=7)
        write_grid_geojson_for_range(db_path, data_dir / 'grid-next3d.geojson', days_forward=3)
        # Also refresh current-*.json so the headline fast-path stays in sync with
        # the grid (otherwise a --geojson-only regen leaves stale headline figures).
        pop_grid = None
        if grid_path.exists():
            with open(grid_path) as f:
                pop_grid = json.load(f)
        climatology = load_climatology(clim_path)
        if climatology is not None and pop_grid is not None and \
                climatology['doy_mean'].shape[0] != len(pop_grid):
            climatology = None
        write_current_json(
            db_path, data_dir, args.threshold,
            climatology=climatology, pop_grid=pop_grid,
        )
        logging.info('Done.')
        return

    if not HAS_CFGRIB:
        print(
            'ERROR: cfgrib is not installed.\n'
            'Install dependencies:\n'
            '  pip install cfgrib xarray scipy requests python-dotenv\n'
            '  brew install eccodes  # macOS\n'
            '  apt-get install libeccodes-dev  # Debian/Ubuntu',
            file=sys.stderr
        )
        sys.exit(1)

    # Ensure data directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    logging.info(f'Database: {db_path}')
    logging.info(f'Grid:     {grid_path}')
    logging.info(f'Threshold: {args.threshold}°C | Forecast: {args.forecast_hours}h')

    with open(grid_path) as f:
        pop_grid = json.load(f)
    logging.info(f'Population grid loaded: {len(pop_grid):,} cells')

    # Optional climatology — silent no-op if file is absent. The climatology
    # array order MUST match population-grid.json order; build-climatology.py
    # constructs it that way.
    climatology = load_climatology(clim_path)
    if climatology is not None and climatology['doy_mean'].shape[0] != len(pop_grid):
        logging.error(
            f'Climatology cell count ({climatology["doy_mean"].shape[0]}) does '
            f'not match population grid ({len(pop_grid)}). Rebuild climatology '
            f'against the current grid: python scripts/build-climatology.py'
        )
        climatology = None

    run_date, run_hour = determine_model_run()
    model_run_time = datetime(run_date.year, run_date.month, run_date.day,
                              run_hour, tzinfo=timezone.utc)

    # Build list of forecast hours for the main run (hourly, capped at 72h).
    # DWD ICON-EU publishes hourly T_2M through +078h; we stop at 72h so every
    # step is within the guaranteed hourly range.
    forecast_steps = list(range(0, args.forecast_hours + 1))

    snapshots_written = 0

    # --backfill-today: for every completed earlier run of today, fetch the
    # analysis (+000h) plus the hourly fill-in steps up to the next run, so
    # the day's coverage is hourly rather than 3-hourly. DWD keeps forecast
    # files for ~48h so the fill-in steps are typically still available.
    if args.backfill_today:
        utcnow = datetime.now(timezone.utc)
        today = utcnow.date()
        for i, h in enumerate(RUN_HOURS):
            candidate = datetime(today.year, today.month, today.day, h, tzinfo=timezone.utc)
            if candidate >= model_run_time:
                break  # current run and future runs handled below
            age_hours = (utcnow - candidate).total_seconds() / 3600
            if age_hours < PUBLISH_LAG_HOURS:
                continue
            test_url = build_variable_url(today, h, 't_2m', 0)
            if not probe_url(test_url):
                logging.warning(f'Backfill: {today} {h:02d}Z +000h not available, skipping')
                continue
            # Fetch analysis + hourly fill-in up to (not including) next run.
            # e.g. 00Z run → steps [0, 1, 2] covers 00Z, 01Z, 02Z.
            next_h = RUN_HOURS[i + 1] if i + 1 < len(RUN_HOURS) else 24
            fill_steps = list(range(0, next_h - h))
            logging.info(f'Backfill: processing {today} {h:02d}Z ({len(fill_steps)} steps: +000h–+{fill_steps[-1]:03d}h)')
            n = process_run(candidate, fill_steps, pop_grid, climatology, db_path, args.threshold)
            snapshots_written += n

    logging.info(f'Processing {len(forecast_steps)} timesteps ({forecast_steps[0]}h–{forecast_steps[-1]}h)')
    snapshots_written += process_run(
        model_run_time, forecast_steps, pop_grid, climatology, db_path, args.threshold
    )

    logging.info(f'Complete. {snapshots_written} snapshots written.')

    if snapshots_written == 0:
        sys.exit(1)

    # Write pre-generated files for fast static serving by the app.
    # All six files are regenerated on every run so the very first visitor after
    # a data refresh is served instantly from disk — no DB query on first load.
    data_dir = project_root / 'data'
    write_grid_geojson(db_path, data_dir / 'grid-latest.geojson')
    write_grid_geojson_for_range(db_path, data_dir / 'grid-last7d.geojson', days_back=7)
    write_grid_geojson_for_range(db_path, data_dir / 'grid-next3d.geojson', days_forward=3)
    write_current_json(
        db_path, data_dir, args.threshold,
        climatology=climatology, pop_grid=pop_grid,
    )


if __name__ == '__main__':
    main()
