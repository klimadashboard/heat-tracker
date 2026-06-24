import { json } from '@sveltejs/kit';
import { existsSync, readFileSync } from 'fs';
import { join } from 'path';
import type { RequestHandler } from './$types';

// Region (NUTS) name for a single grid cell, looked up on demand when a map
// tooltip opens. Kept out of the grid GeoJSON so that ~175k features don't each
// carry region strings (which bloated the payload and slowed hover queries).
//
// data/nuts-by-cell.json is an array aligned to data/population-grid.json:
// element i is { a?: NUTS-1 name, b?: NUTS-2 name, c?: NUTS-3 name } or null.
// We build a coordinate -> region lookup once and cache it in memory.

interface RegionEntry {
	region: string;
	regionBroad?: string;
}

let lookup: Map<string, RegionEntry> | null = null;

function key(lat: number, lon: number): string {
	return `${lat.toFixed(3)},${lon.toFixed(3)}`;
}

function getLookup(): Map<string, RegionEntry> {
	if (lookup) return lookup;
	lookup = new Map();
	try {
		const dir = join(process.cwd(), 'data');
		const pgPath = join(dir, 'population-grid.json');
		const nutsPath = join(dir, 'nuts-by-cell.json');
		if (existsSync(pgPath) && existsSync(nutsPath)) {
			const pg = JSON.parse(readFileSync(pgPath, 'utf-8')) as { lat: number; lon: number }[];
			const nuts = JSON.parse(readFileSync(nutsPath, 'utf-8')) as ({ a?: string; b?: string; c?: string } | null)[];
			for (let i = 0; i < pg.length; i++) {
				const tag = nuts[i];
				if (!tag) continue;
				const region = tag.c || tag.b || tag.a;
				if (!region) continue;
				const entry: RegionEntry = { region };
				if (tag.a && tag.a !== region) entry.regionBroad = tag.a;
				lookup.set(key(pg[i].lat, pg[i].lon), entry);
			}
		}
	} catch {
		// Leave the (possibly partial) lookup; missing region just yields {}.
	}
	return lookup;
}

export const GET: RequestHandler = ({ url }) => {
	const lat = Number(url.searchParams.get('lat'));
	const lon = Number(url.searchParams.get('lon'));
	if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
		return json({}, { headers: { 'Cache-Control': 'public, max-age=86400' } });
	}
	const entry = getLookup().get(key(lat, lon)) ?? {};
	// Region tags are static for a given grid, so cache aggressively.
	return json(entry, { headers: { 'Cache-Control': 'public, max-age=604800' } });
};
