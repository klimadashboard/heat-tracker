/**
 * Colour scales and view metadata shared between the map (MapLibre paint
 * expressions), the floating legend, and the per-country mini visualisations.
 *
 * Keeping the stops in one place means the dropdown bars, the legend and the
 * map dots always agree on what a colour means.
 */

import type { MapView } from '$lib/stores/data.js';

export const VIEW_META: Record<MapView, { label: string; short: string; unit: string }> = {
	difference:           { label: 'Difference from average', short: 'Difference', unit: '°C' },
	temperature:          { label: 'Temperature',             short: 'Temp',       unit: '°C' },
	apparent_temperature: { label: 'Feels like',              short: 'Feels like', unit: '°C' }
};

export const VIEW_ORDER: MapView[] = ['difference', 'temperature', 'apparent_temperature'];

// ── Colour stops (value °C → hex) ──────────────────────────────────────────
// Diverging blue→neutral→red for the anomaly view; sequential warm ramps for
// the absolute-temperature views.

export const ANOMALY_STOPS: [number, string][] = [
	[-12, '#313695'],
	[-8,  '#4575b4'],
	[-4,  '#74add1'],
	[-1.5, '#abd9e9'],
	[0,   '#ececf2'],
	[1.5, '#fee090'],
	[4,   '#fdae61'],
	[8,   '#f46d43'],
	[12,  '#a50026']
];

export const TEMP_STOPS: [number, string][] = [
	[-15, '#1e4d9e'],
	[-5,  '#3d80c0'],
	[0,   '#5aaed8'],
	[5,   '#7ec8e3'],
	[10,  '#98d4a0'],
	[15,  '#b5d86c'],
	[20,  '#dcc43c'],
	[25,  '#f0a020'],
	[30,  '#e87020'],
	[35,  '#d43d1a'],
	[40,  '#b01515'],
	[45,  '#7a0000']
];

export const FEELS_STOPS: [number, string][] = [
	[-15, '#2d3d80'],
	[-5,  '#4060a0'],
	[0,   '#5088b8'],
	[5,   '#70aab8'],
	[10,  '#90bc90'],
	[15,  '#c0cc50'],
	[20,  '#e8c030'],
	[25,  '#f09018'],
	[30,  '#e86010'],
	[35,  '#d02a00'],
	[40,  '#a00000'],
	[45,  '#6a0000']
];

export const NULL_COLOR = 'rgba(80, 80, 96, 0.45)';

/** Build a CSS `linear-gradient(...)` string from value→colour stops. */
export function cssGradient(stops: [number, string][]): string {
	const finite = stops.filter(([v]) => Number.isFinite(v));
	const min = finite[0][0];
	const max = finite[finite.length - 1][0];
	const span = max - min || 1;
	const parts = finite.map(([v, c]) => `${c} ${(((v - min) / span) * 100).toFixed(1)}%`);
	return `linear-gradient(90deg, ${parts.join(', ')})`;
}

function hexToRgb(hex: string): [number, number, number] {
	const h = hex.replace('#', '');
	return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

/** Interpolate a colour from continuous value→colour stops. */
export function interpColor(stops: [number, string][], v: number): string {
	if (v <= stops[0][0]) return stops[0][1];
	const last = stops[stops.length - 1];
	if (v >= last[0]) return last[1];
	for (let i = 0; i < stops.length - 1; i++) {
		const [v0, c0] = stops[i];
		const [v1, c1] = stops[i + 1];
		if (v >= v0 && v <= v1) {
			const t = (v - v0) / (v1 - v0);
			const a = hexToRgb(c0);
			const b = hexToRgb(c1);
			const m = a.map((x, j) => Math.round(x + (b[j] - x) * t));
			return `rgb(${m[0]}, ${m[1]}, ${m[2]})`;
		}
	}
	return last[1];
}

/** Colour for a single value under the given view. */
export function colorForView(view: MapView, v: number | null | undefined): string {
	if (v == null) return NULL_COLOR;
	switch (view) {
		case 'difference':           return interpColor(ANOMALY_STOPS, v);
		case 'temperature':          return interpColor(TEMP_STOPS, v);
		case 'apparent_temperature': return interpColor(FEELS_STOPS, v);
	}
}

/** Format a value with a leading + for the diverging anomaly view. */
export function formatViewValue(view: MapView, v: number | null): string {
	if (v == null) return '–';
	const unit = VIEW_META[view].unit;
	if (view === 'difference') return `${v >= 0 ? '+' : ''}${v.toFixed(1)}${unit}`;
	return `${v.toFixed(1)}${unit}`;
}
