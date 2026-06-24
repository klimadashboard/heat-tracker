import { json } from '@sveltejs/kit';
import { getDb } from '$lib/server/db';
import type { RequestHandler } from './$types';

// One submission per IP per 5 minutes. In-memory only — resets on restart,
// which is fine: we just want to blunt automated spam on public launch.
const lastSubmit = new Map<string, number>();
const RATE_LIMIT_MS = 5 * 60 * 1000;

export const GET: RequestHandler = () => {
	const db = getDb();
	const since = Math.floor(Date.now() / 1000) - 86400;
	const rows = db.prepare(`
		SELECT temperature, location_type, submitted_at
		FROM indoor_temps
		WHERE submitted_at >= ?
		ORDER BY submitted_at ASC
	`).all(since);
	return json(rows);
};

export const POST: RequestHandler = async ({ request, getClientAddress }) => {
	const ip = getClientAddress();
	const now = Date.now();
	const last = lastSubmit.get(ip) ?? 0;
	if (now - last < RATE_LIMIT_MS) {
		return json({ error: 'Too many requests' }, { status: 429 });
	}

	const body = await request.json().catch(() => null);
	if (!body) return json({ error: 'Invalid JSON' }, { status: 400 });

	const temp = Number(body.temperature);
	if (isNaN(temp) || temp < 10 || temp > 55) {
		return json({ error: 'Temperature must be between 10 and 55°C' }, { status: 400 });
	}

	const validTypes = ['home', 'office', 'other'];
	const locType = validTypes.includes(body.location_type) ? body.location_type : 'other';

	const db = getDb();
	db.prepare(`
		INSERT INTO indoor_temps (temperature, location_type, submitted_at)
		VALUES (?, ?, ?)
	`).run(temp, locType, Math.floor(now / 1000));

	lastSubmit.set(ip, now);
	return json({ ok: true });
};
