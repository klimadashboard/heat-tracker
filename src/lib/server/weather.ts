/**
 * Weather data layer — database read queries for the heat tracker.
 *
 * Data is written externally by scripts/fetch-dwd.py (DWD ICON-EU GRIB2),
 * run hourly via Coolify's cron task runner. This module provides only read
 * access to that data.
 *
 * Two heat indicators are stored per grid cell:
 *   - temperature          (2m air temperature, °C)
 *   - apparent_temperature (Steadman 1994 heat index — wind + humidity)
 */

import { getDb } from './db.js';

export type Indicator = 'temperature' | 'apparent_temperature';

const VALID_INDICATORS: Indicator[] = ['temperature', 'apparent_temperature'];

/** Validate and return the SQL column name for an indicator. */
export function indicatorColumn(indicator: string | null | undefined): string {
	if (indicator && (VALID_INDICATORS as string[]).includes(indicator)) {
		return indicator as string;
	}
	return 'temperature';
}

// ---------------------------------------------------------------------------
// Read queries
// ---------------------------------------------------------------------------

/** Get the most recent snapshot */
export function getLatestSnapshot() {
	const db = getDb();
	return db.prepare(`
		SELECT id, timestamp, total_affected as totalAffected, total_population as totalPopulation,
		       threshold_celsius as thresholdCelsius, is_forecast as isForecast,
		       model_run_time as modelRunTime
		FROM snapshots ORDER BY id DESC LIMIT 1
	`).get() as any || null;
}

/**
 * Subquery that selects the "best" snapshot per valid timestamp in a range.
 * For any timestamp that has both an analysis (is_forecast=0) and one or more
 * forecast rows, we keep only the analysis — it supersedes the forecast once
 * the model run that covers that time has been ingested.  For future timestamps
 * that only have forecast rows we keep those.
 *
 * This prevents a past timestamp from having its temperature inflated/deflated
 * by an older forecast that was later superseded by actual observations.
 */
function bestSnapshotsSubquery(): string {
	return `
		SELECT id FROM snapshots s
		WHERE s.timestamp >= ? AND s.timestamp <= ?
		  AND (
		    s.is_forecast = 0
		    OR NOT EXISTS (
		      SELECT 1 FROM snapshots s2
		      WHERE s2.timestamp = s.timestamp AND s2.is_forecast = 0
		    )
		  )
	`;
}

/** Get the available time range in the database */
export function getAvailableRange(): { from: string; to: string; hasForecast: boolean } | null {
	const db = getDb();
	const row = db.prepare(`
		SELECT MIN(timestamp) as from_ts, MAX(timestamp) as to_ts,
		       MAX(is_forecast) as hasForecast
		FROM snapshots
	`).get() as any;
	if (!row?.from_ts) return null;
	return { from: row.from_ts, to: row.to_ts, hasForecast: row.hasForecast === 1 };
}

/** Get grid data for a single snapshot */
export function getGridData(snapshotId: number) {
	const db = getDb();
	return db.prepare(`
		SELECT lat, lon, country, population,
		       temperature, apparent_temperature as apparentTemperature,
		       is_affected as isAffected
		FROM grid_data WHERE snapshot_id = ? AND country != 'TR'
	`).all(snapshotId);
}

/** Get country aggregates for a single snapshot */
export function getCountryAggregates(snapshotId: number) {
	const db = getDb();
	return db.prepare(`
		SELECT country, population, affected,
		       max_temperature as maxTemperature,
		       max_apparent_temperature as maxApparentTemperature,
		       avg_temperature as avgTemperature,
		       avg_anomaly_c as avgAnomalyC,
		       pop_above_avg as popAboveAvg
		FROM country_aggregates
		WHERE snapshot_id = ? AND country != 'TR'
		ORDER BY affected DESC, max_apparent_temperature DESC
	`).all(snapshotId);
}

/**
 * Get grid data aggregated over a date range.
 * Returns peak values for each indicator per cell.
 * isAffected is computed dynamically from the selected indicator.
 */
export function getGridDataForRange(
	from: string,
	to: string,
	indicator: string,
	threshold: number
) {
	const col = indicatorColumn(indicator);
	const db = getDb();
	return db.prepare(`
		SELECT lat, lon, country, population,
		       MAX(temperature) as temperature,
		       MAX(apparent_temperature) as apparentTemperature,
		       MIN(temperature) as minTemperature,
		       MIN(apparent_temperature) as minApparentTemperature,
		       CASE WHEN MAX(${col}) >= ? THEN 1 ELSE 0 END as isAffected
		FROM grid_data
		WHERE snapshot_id IN (${bestSnapshotsSubquery()}) AND country != 'TR'
		GROUP BY lat, lon
	`).all(threshold, from, to);
}

/** Period summary (total affected + snapshot metadata) for a date range */
export function getPeriodSummaryForRange(
	from: string,
	to: string,
	indicator: string,
	threshold: number
): {
	totalAffected: number;
	totalPopulation: number;
	snapshotCount: number;
	oldestTimestamp: string | null;
	newestTimestamp: string | null;
	hasForecast: boolean;
	latestModelRunTime: string | null;
} | null {
	const col = indicatorColumn(indicator);
	const db = getDb();

	const stats = db.prepare(`
		SELECT COUNT(DISTINCT id) as snapshotCount,
		       MIN(timestamp) as oldestTimestamp,
		       MAX(timestamp) as newestTimestamp,
		       MAX(total_population) as totalPopulation,
		       MAX(is_forecast) as hasForecast,
		       MAX(model_run_time) as latestModelRunTime
		FROM snapshots
		WHERE timestamp >= ? AND timestamp <= ?
	`).get(from, to) as any;

	if (!stats || stats.snapshotCount === 0) return null;

	const affected = db.prepare(`
		SELECT SUM(population) as totalAffected
		FROM (
			SELECT lat, lon, population,
			       CASE WHEN MAX(${col}) >= ? THEN 1 ELSE 0 END as wasAffected
			FROM grid_data
			WHERE snapshot_id IN (${bestSnapshotsSubquery()}) AND country != 'TR'
			GROUP BY lat, lon
		)
		WHERE wasAffected = 1
	`).get(threshold, from, to) as any;

	return {
		totalAffected: affected?.totalAffected ?? 0,
		totalPopulation: stats.totalPopulation ?? 0,
		snapshotCount: stats.snapshotCount,
		oldestTimestamp: stats.oldestTimestamp,
		newestTimestamp: stats.newestTimestamp,
		hasForecast: stats.hasForecast === 1,
		latestModelRunTime: stats.latestModelRunTime ?? null
	};
}

/** Country aggregates for a date range with dynamic indicator */
export function getCountryAggregatesForRange(
	from: string,
	to: string,
	indicator: string,
	threshold: number
) {
	const col = indicatorColumn(indicator);
	const db = getDb();
	return db.prepare(`
		SELECT country,
		       SUM(cellPop) as population,
		       SUM(CASE WHEN wasAffected = 1 THEN cellPop ELSE 0 END) as affected,
		       MAX(maxTemp) as maxTemperature,
		       MAX(maxAppTemp) as maxApparentTemperature,
		       AVG(avgTemp) as avgTemperature,
		       AVG(cellAnomaly) as avgAnomalyC
		FROM (
			SELECT country, population as cellPop,
			       CASE WHEN MAX(${col}) >= ? THEN 1 ELSE 0 END as wasAffected,
			       MAX(temperature) as maxTemp,
			       MAX(apparent_temperature) as maxAppTemp,
			       AVG(temperature) as avgTemp,
			       AVG(anomaly_c) as cellAnomaly
			FROM grid_data
			WHERE snapshot_id IN (${bestSnapshotsSubquery()}) AND country != 'TR'
			GROUP BY lat, lon
		)
		GROUP BY country
		ORDER BY affected DESC, maxApparentTemperature DESC
	`).all(threshold, from, to);
}
