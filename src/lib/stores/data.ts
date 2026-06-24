import { writable, derived, get } from 'svelte/store';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Snapshot {
	timestamp: string;
	/** People in cells reaching ≥30 °C air temperature today */
	totalAffected: number;
	totalPopulation: number;
	modelRunTime?: string | null;
	/** When our cron last ingested this data (ISO UTC). */
	fetchedAt?: string | null;
	hasForecast?: boolean;
	// Climatology indicators vs. the E-OBS 1961–1990 reference. Present when the
	// climatology pipeline has run (scripts/build-climatology.py + fetch-dwd.py).
	meanAnomalyC?: number | null;
	/** People where today's temperature exceeds the 1961–1990 90th percentile. null = clim data unavailable (not the same as 0). */
	popAboveAvg?: number | null;
	referencePeriod?: string;
}

export interface CountryData {
	country: string;
	population: number;
	affected: number;
	maxTemperature: number | null;
	maxApparentTemperature: number | null;
	avgTemperature: number | null;
	avgAnomalyC?: number | null;
	popAboveAvg?: number | null;
}

export interface GridFeature {
	type: 'Feature';
	geometry: { type: 'Point'; coordinates: [number, number] };
	properties: {
		country: string;
		population: number;
		/** Cell centre at 3dp — used as the /api/region lookup key (tile-safe). */
		lat?: number;
		lon?: number;
		temperature: number | null;
		apparentTemperature: number | null;
		anomalyC?: number | null;
		region?: string;
		regionBroad?: string;
		minTemperature?: number | null;
		minApparentTemperature?: number | null;
	};
}

export interface GridGeoJSON {
	type: 'FeatureCollection';
	features: GridFeature[];
}

/** The three map views. `difference` colours by anomaly vs. the historic average. */
export type MapView = 'difference' | 'temperature' | 'apparent_temperature';

// ---------------------------------------------------------------------------
// Stores
// ---------------------------------------------------------------------------

export const snapshot = writable<Snapshot | null>(null);
export const countries = writable<CountryData[]>([]);
export const gridData = writable<GridGeoJSON | null>(null);
/** Selected country code, or null for all of Europe */
export const selectedCountry = writable<string | null>(null);
export const isLoading = writable(true);
export const error = writable<string | null>(null);

/** The active map view. Defaults to the new difference-from-average view. */
export const mapView = writable<MapView>('difference');

export type ThresholdView = 'temperature' | 'apparent_temperature';

/** Default heat thresholds per view, used to flag "affected" cells on the map. */
export const DEFAULT_THRESHOLDS: Record<ThresholdView, number> = {
	temperature: 30,
	apparent_temperature: 30
};

/**
 * Active heat thresholds per view, adjustable via the map settings cog. They
 * drive the "affected" highlighting on the absolute-temperature views and the
 * threshold marker in the tooltip range bar. The `difference` view has no
 * threshold. Headline figures are independent of this.
 */
export const thresholds = writable<Record<ThresholdView, number>>({ ...DEFAULT_THRESHOLDS });

/**
 * The global heat threshold (°C). Drives the "people at X°C or more" headline
 * (re-fetches data) and the temperature/feels-like map highlights simultaneously.
 * Reflected in the URL as ?threshold=N when non-default. Use setHeadlineThreshold
 * to update both this store and the linked map thresholds atomically.
 */
export const headlineThreshold = writable<number>(30);

/** Update the headline threshold and sync the linked map view thresholds. */
export function setHeadlineThreshold(v: number) {
	const clamped = Math.max(25, Math.min(45, Math.round(v)));
	headlineThreshold.set(clamped);
	thresholds.update((t) => ({
		...t,
		temperature: clamped,
		apparent_temperature: clamped,
	}));
}

/** The active MapLibre map instance, set by HeatMap.svelte after init. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const mapInstance = writable<any>(null);

export type SelectedDate = 'yesterday' | 'today' | 'tomorrow';
export const selectedDate = writable<SelectedDate>('today');

/**
 * UTC 00:00–23:59 bounds for a calendar day relative to today, where "today"
 * is the user's LOCAL calendar date (not UTC). This ensures "Today" always
 * matches what the user sees on their clock, even around UTC midnight.
 */
export function utcDayBounds(offsetDays: number): { from: string; to: string } {
	const now = new Date();
	// Local calendar date + offset
	const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + offsetDays);
	return {
		from: new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate(), 0, 0, 0)).toISOString(),
		to:   new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate(), 23, 59, 59)).toISOString(),
	};
}

/** True while the user is dragging the hero/map resize handle. */
export const isMapResizing = writable(false);

export const selectedCountryData = derived(
	[countries, selectedCountry],
	([$countries, $selectedCountry]) => {
		if (!$selectedCountry) return null;
		return $countries.find((c) => c.country === $selectedCountry) || null;
	}
);


/**
 * People count at the current headline threshold, derived instantly from the
 * already-loaded grid features. `temperature` in each feature is the running
 * daily max (updated every ~3 h), so this closely tracks the server count
 * (within ~1-2%). Scoped to the selected country when one is active.
 */
export const clientAffected = derived(
	[gridData, headlineThreshold, selectedCountry],
	([$gridData, $headlineThreshold, $selectedCountry]) => {
		if (!$gridData) return null;
		return $gridData.features.reduce((sum, f) => {
			const p = f.properties;
			if ($selectedCountry && p.country !== $selectedCountry) return sum;
			if ((p.temperature ?? -Infinity) >= $headlineThreshold) {
				return sum + (p.population ?? 0);
			}
			return sum;
		}, 0);
	}
);

// ---------------------------------------------------------------------------
// Data loading — today only.
//
// Both endpoints are served from pre-generated static files for the default
// (temperature / 30 °C / today) request, so the headline figures and the grid
// load instantly at the default threshold. At threshold=35 they hit the live
// API. The map view only re-colours the already-loaded grid client-side.
// ---------------------------------------------------------------------------

export async function loadData() {
	isLoading.set(true);
	snapshot.set(null);
	countries.set([]);
	gridData.set(null);
	error.set(null);

	try {
		const thr = get(headlineThreshold);
		const day = get(selectedDate);

		let currentParams: string;
		let gridParams: string;
		if (day === 'today') {
			currentParams = `?preset=today&indicator=temperature&threshold=${thr}`;
			gridParams    = `?preset=today&indicator=temperature&threshold=${thr}`;
		} else if (day === 'tomorrow') {
			// clim_preset=tomorrow overlays climatology headlines from the pre-generated
			// tomorrow file (a single UTC day, so the diurnal-coverage guard reliably
			// passes). from/to constrains the range query to just tomorrow's UTC day.
			const { from, to } = utcDayBounds(1);
			currentParams = `?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}&clim_preset=tomorrow&indicator=temperature&threshold=${thr}`;
			gridParams    = `?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}&indicator=temperature&threshold=${thr}`;
		} else {
			// clim_preset=yesterday overlays climatology headlines from the pre-generated
			// yesterday file — without it the DB range path has no popAboveAvg.
			const { from, to } = utcDayBounds(-1);
			currentParams = `?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}&clim_preset=yesterday&indicator=temperature&threshold=${thr}`;
			gridParams    = `?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}&indicator=temperature&threshold=${thr}`;
		}

		const [currentRes, gridRes] = await Promise.all([
			fetch(`/api/current${currentParams}`),
			fetch(`/api/grid${gridParams}`)
		]);

		if (gridRes.ok) {
			// /api/grid returns the GeoJSON FeatureCollection directly.
			gridData.set(await gridRes.json());
		}

		if (!currentRes.ok) {
			const errData = await currentRes.json().catch(() => ({}));
			throw new Error(errData.error || 'Failed to load data');
		}

		const currentData = await currentRes.json();
		snapshot.set(currentData.snapshot);
		countries.set(currentData.countries);
	} catch (err) {
		error.set(err instanceof Error ? err.message : 'Unknown error');
	} finally {
		isLoading.set(false);
	}
}
