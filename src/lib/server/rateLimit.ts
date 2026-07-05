/**
 * Minimal per-key request throttle — in-memory only, resets on restart.
 * Same pattern as the cooldown in /api/indoor-temps, generalised for reuse.
 *
 * Intended for expensive, uncached code paths (e.g. the custom date-range
 * grid/current DB queries), not the whole app — the pre-generated preset
 * responses are cheap file reads and shouldn't be throttled.
 */

const lastSeen = new Map<string, number>();

/** Returns true if `key` may proceed, false if it's within `minIntervalMs`
 *  of its last allowed request. Records the timestamp on every call so a
 *  client that keeps retrying doesn't get a free pass once the window
 *  passes only to reset it again immediately. */
export function allowRequest(key: string, minIntervalMs: number): boolean {
	const now = Date.now();
	const last = lastSeen.get(key) ?? 0;
	if (now - last < minIntervalMs) return false;
	lastSeen.set(key, now);
	return true;
}
