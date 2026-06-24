import Database from 'better-sqlite3';
import { join } from 'path';

const DB_PATH = join(process.cwd(), 'data', 'heat-tracker.db');

let db: Database.Database;

export function getDb(): Database.Database {
	if (!db) {
		db = new Database(DB_PATH);
		db.pragma('journal_mode = WAL');
		db.pragma('synchronous = NORMAL');
		initializeDb(db);
	}
	return db;
}

function initializeDb(db: Database.Database) {
	db.exec(`
		CREATE TABLE IF NOT EXISTS snapshots (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			timestamp TEXT NOT NULL,
			fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
			total_affected INTEGER NOT NULL DEFAULT 0,
			total_population INTEGER NOT NULL DEFAULT 0,
			threshold_celsius REAL NOT NULL DEFAULT 35.0,
			is_forecast INTEGER NOT NULL DEFAULT 0,
			model_run_time TEXT
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
			FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
		);

		CREATE INDEX IF NOT EXISTS idx_grid_snapshot ON grid_data(snapshot_id);
		CREATE INDEX IF NOT EXISTS idx_grid_country ON grid_data(snapshot_id, country);

		CREATE TABLE IF NOT EXISTS indoor_temps (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			temperature REAL NOT NULL,
			location_type TEXT NOT NULL DEFAULT 'home',
			submitted_at INTEGER NOT NULL
		);
		CREATE INDEX IF NOT EXISTS idx_indoor_temps_time ON indoor_temps(submitted_at);

		CREATE TABLE IF NOT EXISTS country_aggregates (
			snapshot_id INTEGER NOT NULL,
			country TEXT NOT NULL,
			population INTEGER NOT NULL,
			affected INTEGER NOT NULL DEFAULT 0,
			max_temperature REAL,
			max_apparent_temperature REAL,
			avg_temperature REAL,
			PRIMARY KEY (snapshot_id, country),
			FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
		);
	`);

	// Migrate existing databases — add new columns if they don't exist yet.
	// better-sqlite3 doesn't support IF NOT EXISTS on ALTER TABLE, so we
	// catch the "duplicate column name" error silently.
	const migrations: [string, string, string][] = [
		['snapshots',          'is_forecast',    'INTEGER NOT NULL DEFAULT 0'],
		['snapshots',          'model_run_time', 'TEXT'],
		// Added by fetch-dwd.py; mirrored here so fresh deploys are consistent.
		['grid_data',          'anomaly_c',           'REAL'],
		['country_aggregates', 'avg_anomaly_c',        'REAL'],
		['country_aggregates', 'pop_above_avg',        'INTEGER NOT NULL DEFAULT 0'],
	];

	for (const [table, col, typedef] of migrations) {
		try {
			db.exec(`ALTER TABLE ${table} ADD COLUMN ${col} ${typedef}`);
		} catch {
			// Column already exists — expected after the first run
		}
	}
}
