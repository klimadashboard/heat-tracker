// Seeds the indoor_temps table with 100 deterministic simulated entries.
// Run once: node scripts/seed-indoor-temps.js
import Database from 'better-sqlite3';
import { join } from 'path';
import { fileURLToPath } from 'url';
import { dirname } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DB_PATH = join(__dirname, '..', 'data', 'heat-tracker.db');

function lcg(seed) {
	let s = seed;
	return () => { s = (s * 1664525 + 1013904223) & 0xffffffff; return (s >>> 0) / 0xffffffff; };
}

const rng = lcg(42);
const now = Math.floor(Date.now() / 1000);

const entries = Array.from({ length: 100 }, () => {
	const u = Math.max(0.0001, rng()), v = rng();
	const raw = 28 + 4 * Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
	return {
		temperature: Math.max(20, Math.min(40, Math.round(raw * 2) / 2)),
		submitted_at: now - Math.floor(rng() * 86400),
	};
});

const db = new Database(DB_PATH);
db.pragma('journal_mode = WAL');

const insert = db.prepare(`INSERT INTO indoor_temps (temperature, location_type, submitted_at) VALUES (?, 'other', ?)`);
const insertMany = db.transaction((rows) => {
	for (const r of rows) insert.run(r.temperature, r.submitted_at);
});

insertMany(entries);
console.log(`Inserted ${entries.length} entries into indoor_temps.`);
db.close();
