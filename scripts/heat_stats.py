"""
heat_stats.py — population-weighted heat exposure & climatology-anomaly stats
for an arbitrary time window, queried directly against heat-tracker.db.

Extracted from write_current_json() in fetch-dwd.py so the same query logic
(peak-exposure population, per-country/Europe rollups, 1961-1990 anomaly with
diurnal-coverage guard) can be reused for arbitrary custom windows — not just
the fixed yesterday/today/tomorrow/last7d/next3d presets — by press-stats.py.

See write_current_json()'s docstring in fetch-dwd.py for the full methodology
note (why "affected" stays a peak/Tmax-style metric while anomaly/popAboveAvg
use daily means).
"""

import datetime as dt
import json
import logging

import numpy as np


def load_climatology(path):
    """
    Load the per-cell x day-of-year climatology produced by build-climatology.py.

    Returns a dict {doy_mean, doy_p90, reference_period, percentile} or None if
    the file is absent, so callers can run unchanged with anomaly indicators
    simply staying null.
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


def build_cell_idx_lookup(pop_grid):
    """(round(lat,4), round(lon,4)) -> index into pop_grid / climatology arrays."""
    return {
        (round(c['lat'], 4), round(c['lon'], 4)): i
        for i, c in enumerate(pop_grid)
    }


def compute_period_stats(conn, from_ts, to_ts, threshold,
                          climatology=None, pop_grid=None, cell_idx_lookup=None):
    """
    Compute population-weighted heat exposure + climatology-anomaly stats for
    an arbitrary [from_ts, to_ts] ISO-8601 UTC window.

    `conn` must be a sqlite3.Connection with row_factory = sqlite3.Row, opened
    against heat-tracker.db. `pop_grid` is the parsed population-grid.json
    (only needed if `climatology` is given). `cell_idx_lookup` can be
    precomputed once via build_cell_idx_lookup() and reused across many calls
    (e.g. one per day/threshold) to avoid rebuilding it every time.

    Returns None if there is no snapshot data at all in the window.
    """
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
        return None

    # Subquery: prefer analysis (is_forecast=0) over forecast for same timestamp.
    best_sq = (
        'SELECT id FROM snapshots s'
        ' WHERE s.timestamp >= ? AND s.timestamp <= ?'
        '   AND (s.is_forecast = 0 OR NOT EXISTS ('
        '     SELECT 1 FROM snapshots s2'
        '     WHERE s2.timestamp = s.timestamp AND s2.is_forecast = 0'
        '   ))'
    )

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

    # Diurnal-coverage guard — see write_current_json() in fetch-dwd.py for the
    # full rationale. Require >=20 distinct hours per covered UTC day, else
    # suppress the daily-mean-based anomaly/popAboveAvg headlines.
    coverage = conn.execute(
        '''SELECT COUNT(DISTINCT timestamp)               AS n_hours,
                  COUNT(DISTINCT substr(timestamp, 1, 10)) AS n_days
           FROM snapshots WHERE timestamp >= ? AND timestamp <= ?''',
        (from_ts, to_ts)
    ).fetchone()
    n_hours = coverage['n_hours'] or 0
    n_days  = max(1, coverage['n_days'] or 1)
    coverage_ok = n_hours >= 20 * n_days
    if not coverage_ok:
        logging.warning(
            f'compute_period_stats: thin diurnal coverage for {from_ts}..{to_ts} '
            f'({n_hours} hours over {n_days} day(s); need >={20 * n_days}) — '
            f'suppressing climatology headlines to avoid a warm-biased anomaly.'
        )

    clim_mean_window = None
    clim_p90_window = None
    if climatology is not None and pop_grid is not None and coverage_ok:
        from_dt = dt.datetime.fromisoformat(from_ts.replace('Z', '+00:00'))
        to_dt   = dt.datetime.fromisoformat(to_ts.replace('Z', '+00:00'))
        day = from_dt.date()
        doys_in_window = set()
        while day <= to_dt.date():
            doys_in_window.add(day.timetuple().tm_yday - 1)  # 0-indexed
            day += dt.timedelta(days=1)
        doys_arr = np.array(sorted(doys_in_window))
        clim_mean_window = np.nanmean(climatology['doy_mean'][:, doys_arr], axis=1)
        clim_p90_window  = np.nanmean(climatology['doy_p90'][:,  doys_arr], axis=1)
        if cell_idx_lookup is None:
            cell_idx_lookup = build_cell_idx_lookup(pop_grid)

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

    country_agg = {}
    europe_anom_sum = 0.0
    europe_anom_count = 0
    europe_pop_above_avg = 0
    for r in country_rows:
        c = r['country']
        pop = r['population']
        daily_mean = r['avg_temp']

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
        if r['max_temp'] is not None:
            ca['maxTemp']    = max(ca['maxTemp'], r['max_temp']) if ca['maxTemp'] is not None else r['max_temp']
            ca['tempSum']   += r['avg_temp'] if r['avg_temp'] is not None else r['max_temp']
            ca['tempCount'] += 1
        if r['max_app_temp'] is not None:
            ca['maxAppTemp'] = max(ca['maxAppTemp'], r['max_app_temp']) if ca['maxAppTemp'] is not None else r['max_app_temp']

    mean_anomaly_c = europe_anom_sum / europe_anom_count if europe_anom_count > 0 else None
    pop_above_avg = europe_pop_above_avg

    sorted_countries = sorted(
        country_agg.items(),
        key=lambda kv: (-kv[1]['affected'], -(kv[1]['maxAppTemp'] or -999))
    )
    countries_out = [
        {
            'country': country,
            'population': ca['population'],
            'affected': ca['affected'],
            'maxTemperature': round(ca['maxTemp'], 1) if ca['maxTemp'] is not None else None,
            'maxApparentTemperature': round(ca['maxAppTemp'], 1) if ca['maxAppTemp'] is not None else None,
            'avgTemperature': round(ca['tempSum'] / ca['tempCount'], 1) if ca['tempCount'] else None,
            'avgAnomalyC': round(ca['anomSum'] / ca['anomCount'], 2) if ca['anomCount'] else None,
            'popAboveAvg': int(ca['popAboveAvg']),
        }
        for country, ca in sorted_countries
    ]

    return {
        'snapshot': {
            'timestamp':        stats['newest_timestamp'],
            'totalAffected':    affected_row['total_affected'] or 0,
            'totalPopulation':  stats['total_population'] or 0,
            'thresholdCelsius': threshold,
            'snapshotCount':    stats['snapshot_count'],
            'oldestTimestamp':  stats['oldest_timestamp'],
            'hasForecast':      bool(stats['has_forecast']),
            'modelRunTime':     stats['latest_model_run_time'],
            'fetchedAt':        (stats['latest_fetched_at'].replace(' ', 'T') + 'Z')
                                if stats['latest_fetched_at'] else None,
            'meanAnomalyC':     round(mean_anomaly_c, 2) if mean_anomaly_c is not None else None,
            'popAboveAvg':      int(pop_above_avg),
            'referencePeriod':  climatology['reference_period'] if climatology else '1961-1990',
        },
        'countries': countries_out,
        'indicator': 'temperature',
        'from': from_ts,
        'to': to_ts,
    }
