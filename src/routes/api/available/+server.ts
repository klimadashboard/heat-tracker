import { json } from '@sveltejs/kit';
import { getAvailableRange } from '$lib/server/weather.js';
import type { RequestHandler } from './$types';

/** Returns the min/max timestamps available in the database.
 *  Used by the date picker to show valid range bounds. */
export const GET: RequestHandler = async () => {
	const range = getAvailableRange();
	if (!range) {
		return json({ error: 'No data available yet.' }, { status: 404 });
	}
	return json(range);
};
