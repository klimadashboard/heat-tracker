<script lang="ts">
	import { onMount, onDestroy } from "svelte";
	import { get } from "svelte/store";
	import { goto } from "$app/navigation";
	import maplibregl from "maplibre-gl";
	import {
		gridData,
		selectedCountry,
		mapView,
		thresholds,
		DEFAULT_THRESHOLDS,
		mapInstance,
		isMapResizing,
		selectedDate,
	} from "$lib/stores/data.js";
	import type { GridGeoJSON, MapView, ThresholdView } from "$lib/stores/data.js";
	import { COUNTRY_NAMES, CODE_TO_SLUG } from "$lib/countries.js";
	import {
		ANOMALY_STOPS,
		TEMP_STOPS,
		FEELS_STOPS,
		NULL_COLOR,
		VIEW_META,
		cssGradient,
		colorForView,
	} from "$lib/utils/scales.js";

	let mapContainer: HTMLDivElement;
	let map: maplibregl.Map | null = null;
	let resizeObserver: ResizeObserver | null = null;
	let popup: maplibregl.Popup | null = null;
	let hoveredCell: { lat: number; lon: number } | null = null;
	// Removes the container-level mouseleave listener on destroy.
	let clearHoverDom: (() => void) | null = null;
	// Active view + thresholds, kept in sync via explicit store subscriptions in
	// onMount. We read these locals rather than `$mapView`/`$thresholds`.
	let currentView: MapView = "difference";
	let currentThresholds: Record<ThresholdView, number> = { ...DEFAULT_THRESHOLDS };

	// GeoJSON property name for each view's primary value
	function viewProp(view: MapView): string {
		return view === "difference"
			? "anomalyC"
			: view === "apparent_temperature"
				? "apparentTemperature"
				: "temperature";
	}

	// Cells flagged "affected" — only meaningful for the absolute-temperature
	// views. The difference view has no threshold, so nothing is flagged.
	function affectedExpr(view: MapView): maplibregl.ExpressionSpecification {
		if (view === "difference") return ["boolean", false] as any;
		const prop = viewProp(view);
		const threshold = currentThresholds[view as ThresholdView];
		return [">=", ["coalesce", ["get", prop], -999], threshold] as any;
	}

	function flatten(stops: [number, string][]): (number | string)[] {
		const out: (number | string)[] = [];
		for (const [v, c] of stops) out.push(v, c);
		return out;
	}

	function buildColorExpr(view: MapView): maplibregl.ExpressionSpecification {
		const prop = viewProp(view);
		const get = ["get", prop];
		const stops =
			view === "difference" ? ANOMALY_STOPS : view === "apparent_temperature" ? FEELS_STOPS : TEMP_STOPS;
		const scale = ["interpolate", ["linear"], get, ...flatten(stops)];
		return ["case", ["==", get, null], NULL_COLOR, scale] as any;
	}

	// Opacity is population-scaled so dense places read stronger; dots keep the
	// same opacity at all zoom levels (no fading on zoom-in).
	function buildOpacityExpr(
		view: MapView,
		country: string | null = null,
	): maplibregl.ExpressionSpecification {
		const popRamp = (lo: number, hi: number) =>
			["interpolate", ["linear"], ["get", "population"],
				100, lo, 10000, lo + (hi - lo) * 0.3, 100000, lo + (hi - lo) * 0.6,
				1000000, lo + (hi - lo) * 0.85, 5000000, hi] as any;

		if (country) {
			const base = view === "difference"
				? popRamp(0.4, 0.95)
				: ["case", affectedExpr(view), 0.95, popRamp(0.4, 0.9)];
			return ["case", ["==", ["get", "country"], country], base, 0.16] as any;
		}

		if (view === "difference") {
			return popRamp(0.5, 0.95) as any;
		}

		return ["case", affectedExpr(view), 0.92, popRamp(0.5, 0.95)] as any;
	}

	const EMPTY_FC = { type: "FeatureCollection", features: [] } as any;

	// Cache of region (NUTS) names fetched on demand per cell, keyed by lon,lat.
	// Region is no longer embedded in the grid GeoJSON (it bloated the payload
	// and slowed hover); it's fetched from /api/region when a tooltip opens.
	const regionCache = new Map<string, { region?: string; regionBroad?: string }>();

	// Dot radius by zoom × population. Low-zoom (3–5) radii are deliberately a bit
	// larger so the Europe-wide initial view reads as full and bright rather than
	// sparse sub-pixel specks (which only "filled in" after zooming a step).
	const RADIUS_EXPR: maplibregl.ExpressionSpecification = [
		"interpolate", ["exponential", 1.5], ["zoom"],
		3, ["interpolate", ["linear"], ["get", "population"], 0, 1.1, 1000, 1.3, 10000, 1.6, 100000, 2.0, 1000000, 2.6, 6000000, 3.4],
		5, ["interpolate", ["linear"], ["get", "population"], 0, 1.9, 1000, 2.2, 10000, 2.7, 100000, 3.5, 500000, 4.6, 2000000, 6.2, 6000000, 8.0],
		7, ["interpolate", ["linear"], ["get", "population"], 0, 2.4, 1000, 3.2, 10000, 4.4, 100000, 7.0, 500000, 9.8, 2000000, 13.8, 6000000, 18],
		9, ["interpolate", ["linear"], ["get", "population"], 0, 4.5, 10000, 8, 100000, 12, 500000, 17, 2000000, 24, 6000000, 33],
	] as any;

	function initMap() {
		map = new maplibregl.Map({
			container: mapContainer,
			canvasContextAttributes: { preserveDrawingBuffer: true },
			style: {
				version: 8,
				name: "Heat Tracker",
				sources: {
					"carto-base": {
						type: "raster",
						tiles: ["https://basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}@2x.png"],
						tileSize: 256,
						attribution:
							'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>',
					},
					// NB: the label source/glyphs are added later in addLabelLayers,
					// NOT declared here. Declaring the vector source up front makes the
					// whole map's `load` event (and thus the heat dots) block on the
					// label TileJSON — labels are a non-essential overlay, so they load
					// progressively instead.
				},
				layers: [
					{ id: "background", type: "background", paint: { "background-color": "#08080e" } },
					{
						id: "carto-base",
						type: "raster",
						source: "carto-base",
						paint: { "raster-opacity": ["interpolate", ["linear"], ["zoom"], 3, 0.75, 6, 0.88, 9, 0.96] as any },
					},
				],
			},
			center: [15, 50],
			zoom: 3.8,
			minZoom: 3,
			maxZoom: 9,
			scrollZoom: false,
		});

		mapInstance.set(map);

		const scrollZoomKey = "heat-tracker-scroll-zoom";
		let scrollZoomOn = localStorage.getItem(scrollZoomKey) === "1";
		if (scrollZoomOn) map.scrollZoom.enable();

		class ScrollZoomControl implements maplibregl.IControl {
			_container!: HTMLDivElement;
			_btn!: HTMLButtonElement;
			_map!: maplibregl.Map;
			onAdd(m: maplibregl.Map) {
				this._map = m;
				this._container = document.createElement("div");
				this._container.className = "maplibregl-ctrl maplibregl-ctrl-group";
				this._btn = document.createElement("button");
				this._btn.title = "Toggle scroll to zoom";
				this._btn.style.cssText = "width:29px;height:29px;display:flex;align-items:center;justify-content:center;cursor:pointer;";
				this._render();
				this._btn.addEventListener("click", () => {
					scrollZoomOn = !scrollZoomOn;
					localStorage.setItem(scrollZoomKey, scrollZoomOn ? "1" : "0");
					scrollZoomOn ? this._map.scrollZoom.enable() : this._map.scrollZoom.disable();
					this._render();
				});
				this._container.appendChild(this._btn);
				return this._container;
			}
			onRemove() { this._container.remove(); }
			_render() {
				this._btn.style.opacity = scrollZoomOn ? "1" : "0.45";
				this._btn.innerHTML = `<svg width="13" height="18" viewBox="0 0 13 18" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" style="color:#ccc">
					<rect x="1" y="1" width="11" height="16" rx="5.5"/>
					<line x1="6.5" y1="4.5" x2="6.5" y2="8.5"/>
					<polyline points="4.5,6 6.5,4 8.5,6"/>
					<polyline points="4.5,7 6.5,9 8.5,7"/>
				</svg>`;
			}
		}

		map.addControl(new maplibregl.NavigationControl(), "bottom-left");
		map.addControl(new ScrollZoomControl(), "bottom-left");

		// MapLibre only auto-resizes on window resize, not container resize.
		// During the hero/map drag we skip resize calls (would flash black) and
		// call resize once when the drag ends instead.
		if (typeof ResizeObserver !== "undefined") {
			resizeObserver = new ResizeObserver(() => {
				if (get(isMapResizing)) return;
				map?.resize();
			});
			resizeObserver.observe(mapContainer);
		}

		map.on("load", () => {
			styleReady = true;
			renderGrid();
		});
	}

	// Render the grid once BOTH the style has loaded and data has arrived,
	// regardless of which happens first. The previous `$:` guard called the
	// non-reactive `map.isStyleLoaded()`, so when data landed mid-style-load it
	// ran once, did nothing, and never re-fired — leaving the map empty. The
	// vector label source made the style slower to load, which exposed this.
	let styleReady = false;
	function renderGrid() {
		const gj = get(gridData);
		if (map && styleReady && gj) addGridLayer(gj);
	}

	// Curated English-only label layers drawn from CARTO's vector tiles, added
	// on top of the heat dots. Deliberately sparse: country + state + major
	// cities, plus a single English ocean/sea label per body of water (the old
	// raster overlay repeated local-language sea names dozens of times).
	function addLabelLayers() {
		if (!map || map.getLayer("lbl-country")) return;

		// Add the label source + glyphs lazily (see the style comment): this keeps
		// the core map independent of CARTO's vector TileJSON.
		if (!map.getSource("carto-labels")) {
			map.setGlyphs("https://tiles.basemaps.cartocdn.com/fonts/{fontstack}/{range}.pbf");
			map.addSource("carto-labels", {
				type: "vector",
				url: "https://tiles.basemaps.cartocdn.com/vector/carto.streets/v1/tiles.json",
			});
		}

		const nameEn = ["coalesce", ["get", "name_en"], ["get", "name"]] as any;
		const halo = { "text-halo-color": "#0a0a12", "text-halo-width": 1.2, "text-halo-blur": 0.4 };

		map.addLayer({
			id: "lbl-water",
			type: "symbol",
			source: "carto-labels",
			"source-layer": "water_name",
			filter: ["all",
				["==", ["geometry-type"], "Point"],
				["in", ["get", "class"], ["literal", ["ocean", "sea"]]],
			] as any,
			layout: {
				"text-field": nameEn,
				"text-font": ["Montserrat Medium Italic"],
				"text-size": ["interpolate", ["linear"], ["zoom"], 3, 11, 6, 13] as any,
				"text-letter-spacing": 0.18,
				"text-max-width": 6,
				"text-transform": "uppercase",
			},
			paint: { "text-color": "rgba(120,140,170,0.65)", ...halo },
		});

		map.addLayer({
			id: "lbl-state",
			type: "symbol",
			source: "carto-labels",
			"source-layer": "place",
			minzoom: 5,
			filter: ["==", ["get", "class"], "state"] as any,
			layout: {
				"text-field": nameEn,
				"text-font": ["Montserrat Medium"],
				"text-transform": "uppercase",
				"text-letter-spacing": 0.05,
				"text-size": ["interpolate", ["linear"], ["zoom"], 5, 10, 8, 12] as any,
				"text-max-width": 8,
			},
			paint: { "text-color": "rgba(150,160,180,0.7)", ...halo },
		});

		map.addLayer({
			id: "lbl-city",
			type: "symbol",
			source: "carto-labels",
			"source-layer": "place",
			minzoom: 4,
			filter: ["in", ["get", "class"], ["literal", ["city", "town"]]] as any,
			layout: {
				"text-field": nameEn,
				"text-font": ["Montserrat Medium"],
				"text-size": ["interpolate", ["linear"], ["zoom"], 4, 10, 7, 12, 9, 14] as any,
				"text-max-width": 8,
				"text-variable-anchor": ["center", "top", "bottom", "left", "right"],
				"text-justify": "auto",
				"text-padding": 4,
			},
			paint: { "text-color": "rgba(208,214,228,0.92)", ...halo },
		});

		map.addLayer({
			id: "lbl-country",
			type: "symbol",
			source: "carto-labels",
			"source-layer": "place",
			filter: ["==", ["get", "class"], "country"] as any,
			layout: {
				"text-field": nameEn,
				"text-font": ["Montserrat Medium"],
				"text-transform": "uppercase",
				"text-letter-spacing": 0.06,
				"text-size": ["interpolate", ["linear"], ["zoom"], 3, 10, 6, 13, 8, 15] as any,
				"text-max-width": 7,
			},
			paint: { "text-color": "rgba(192,202,218,0.88)", ...halo },
		});
	}

	function addGridLayer(geojson: GridGeoJSON) {
		if (!map) return;
		const view = currentView;

		const existingSource = map.getSource("grid") as maplibregl.GeoJSONSource | undefined;
		if (existingSource && map.getLayer("grid-circles")) {
			existingSource.setData(geojson as any);
			applyViewPaint();
			return;
		}

		if (map.getLayer("grid-circles")) map.removeLayer("grid-circles");
		if (map.getSource("grid")) map.removeSource("grid");

		// tolerance: 0 disables geometry simplification so no dots are dropped or
		// merged at low zoom; buffer keeps dots near tile edges from being clipped.
		map.addSource("grid", { type: "geojson", data: geojson as any, tolerance: 0, buffer: 64 });

		map.addLayer({
			id: "grid-circles",
			type: "circle",
			source: "grid",
			paint: {
				"circle-radius": RADIUS_EXPR,
				"circle-color": buildColorExpr(view),
				"circle-opacity": buildOpacityExpr(view, $selectedCountry),
				"circle-stroke-color": ["case", affectedExpr(view), "rgba(255,255,255,0.18)", "rgba(255,255,255,0.02)"] as any,
				"circle-stroke-width": ["case", affectedExpr(view), 0.22, 0.1] as any,
			},
		});

		addLabelLayers();

		// Highlight the hovered dot via a tiny single-feature source instead of a
		// second copy of the full 174k-feature layer — that roughly halves the
		// number of circles MapLibre renders and removes a per-hover filter scan.
		if (!map.getSource("hover-pt")) {
			map.addSource("hover-pt", { type: "geojson", data: EMPTY_FC });
		}
		if (!map.getLayer("grid-circles-hover")) {
			map.addLayer({
				id: "grid-circles-hover",
				type: "circle",
				source: "hover-pt",
				paint: {
					"circle-radius": RADIUS_EXPR,
					"circle-color": "transparent",
					"circle-stroke-width": 1.5,
					"circle-stroke-color": "rgba(255,255,255,0.75)",
					"circle-opacity": 1,
				},
			});
		}

		setupInteractions();

		// MapLibre sometimes leaves the circle layer only partially painted at the
		// initial (low) zoom — dots look sparse/dim until the user zooms a step,
		// which forces a re-tile. Re-setting the source data on the next frame (and
		// nudging a repaint) triggers that same re-tile immediately, so the first
		// Europe-wide view renders complete and bright without any interaction.
		requestAnimationFrame(() => {
			const src = map?.getSource("grid") as maplibregl.GeoJSONSource | undefined;
			if (src) {
				src.setData(geojson as any);
				map?.triggerRepaint();
			}
		});

		// If the page loaded on a country route, the selection was set before the
		// data arrived — fit to it now that the grid (and its bounds) exist.
		maybeFitCountry();
	}

	// Re-apply paint properties for the active view without rebuilding the layer.
	function applyViewPaint() {
		if (!map || !map.getLayer("grid-circles")) return;
		const view = currentView;
		map.setPaintProperty("grid-circles", "circle-color", buildColorExpr(view));
		map.setPaintProperty("grid-circles", "circle-opacity", buildOpacityExpr(view, $selectedCountry));
		map.setPaintProperty("grid-circles", "circle-stroke-color",
			["case", affectedExpr(view), "rgba(255,255,255,0.18)", "rgba(255,255,255,0.02)"] as any);
		map.setPaintProperty("grid-circles", "circle-stroke-width",
			["case", affectedExpr(view), 0.22, 0.1] as any);
	}

	// ── Fit the map to the selected country ──────────────────────────────────
	// The default Europe-wide view (must match initMap's center/zoom).
	const EUROPE_CENTER: [number, number] = [15, 50];
	const EUROPE_ZOOM = 3.8;
	// Track what we last fitted to, so periodic data refreshes (which re-run
	// addGridLayer) don't yank a panned map back, and we only animate on change.
	let lastFitCountry: string | null = null;

	function fitToCountry(country: string | null) {
		if (!map) return;
		if (!country) {
			map.flyTo({ center: EUROPE_CENTER, zoom: EUROPE_ZOOM, duration: 700 });
			return;
		}
		const gj = get(gridData);
		if (!gj) return;
		let minLon = Infinity, minLat = Infinity, maxLon = -Infinity, maxLat = -Infinity, n = 0;
		for (const f of gj.features) {
			if (f.properties.country !== country) continue;
			const [lon, lat] = f.geometry.coordinates;
			if (lon < minLon) minLon = lon;
			if (lon > maxLon) maxLon = lon;
			if (lat < minLat) minLat = lat;
			if (lat > maxLat) maxLat = lat;
			n++;
		}
		if (!n) return;
		// Padding leaves breathing room around the country; maxZoom stops small
		// countries (e.g. Luxembourg) from zooming in uncomfortably far.
		map.fitBounds([[minLon, minLat], [maxLon, maxLat]], {
			padding: { top: 64, bottom: 56, left: 56, right: 56 },
			maxZoom: 7,
			duration: 700,
		});
	}

	// Fit only when the selection actually changes and the layer is ready.
	function maybeFitCountry() {
		if (!map || !map.getLayer("grid-circles")) return;
		const country = get(selectedCountry);
		if (country === lastFitCountry) return;
		lastFitCountry = country;
		fitToCountry(country);
	}

	// ── Tooltip + hover/click interactions ──────────────────────────────────

	function setupInteractions() {
		if (!map) return;

		const fmt = (v: any, unit = "°C") =>
			v !== null && v !== undefined ? `${Number(v).toFixed(1)}${unit}` : "–";
		const fmtSigned = (v: any) =>
			v !== null && v !== undefined ? `${Number(v) >= 0 ? "+" : ""}${Number(v).toFixed(1)}°C` : "–";
		const fmtRange = (min: any, max: any, unit = "°C") => {
			if (min == null || max == null) return fmt(max, unit);
			return `${Number(min).toFixed(1)}° – ${Number(max).toFixed(1)}${unit}`;
		};

		const row = (label: string, value: string, active: boolean) =>
			`<div style="display:flex;justify-content:space-between;gap:14px;">
				<span style="color:${active ? "#f4f4f8" : "#bcbcc6"}">${label}</span>
				<span style="${active ? "font-weight:700;color:#fff" : "color:#d8d8e0"}">${value}</span>
			</div>`;

		const hoverSource = () => map!.getSource("hover-pt") as maplibregl.GeoJSONSource | undefined;

		// Indicator chart for the active view: today's range (or single value) of
		// the variable along its colour gradient, with the threshold as a line.
		const BAR: Record<string, { stops: [number, string][]; dMin: number; dMax: number; prop: string; minProp: string | null }> = {
			difference:           { stops: ANOMALY_STOPS, dMin: -12, dMax: 12, prop: "anomalyC",            minProp: null },
			temperature:          { stops: TEMP_STOPS,    dMin: -15, dMax: 45, prop: "temperature",         minProp: "minTemperature" },
			apparent_temperature: { stops: FEELS_STOPS,   dMin: -15, dMax: 45, prop: "apparentTemperature", minProp: "minApparentTemperature" },
		};

		const buildBar = (props: Record<string, any>, view: string): string => {
			const cfg = BAR[view];
			if (!cfg) return "";
			const max = props[cfg.prop];
			if (max == null) return "";
			const min = cfg.minProp != null ? props[cfg.minProp] : null;
			const span = cfg.dMax - cfg.dMin;
			const pct = (v: number) => Math.max(0, Math.min(100, ((v - cfg.dMin) / span) * 100));
			const grad = cssGradient(cfg.stops.filter(([v]) => Number.isFinite(v)) as [number, string][]);
			const r = pct(Number(max));
			const thr = view !== "difference" ? currentThresholds[view as ThresholdView] : null;

			const fill = min != null
				? `<div style="position:absolute;inset:0;background:${grad};clip-path:inset(0 ${(100 - r).toFixed(1)}% 0 ${pct(Number(min)).toFixed(1)}% round 2px)"></div>`
				: `<div style="position:absolute;top:50%;left:${r.toFixed(1)}%;width:9px;height:9px;border-radius:50%;transform:translate(-50%,-50%);background:${colorForView(view as MapView, Number(max))};border:1.5px solid rgba(0,0,0,0.55)"></div>`;

			const thrLine = thr != null
				? `<div style="position:relative;height:8px;margin-top:-8px;pointer-events:none"><div style="position:absolute;top:0;bottom:0;left:${pct(thr).toFixed(1)}%;width:1.5px;background:rgba(255,255,255,0.75);transform:translateX(-50%)"></div></div>`
				: "";

			const label = min != null
				? `${Number(min).toFixed(1)}° – ${Number(max).toFixed(1)}°`
				: view === "difference"
					? `${Number(max) >= 0 ? "+" : ""}${Number(max).toFixed(1)}°C`
					: `${Number(max).toFixed(1)}°`;

			return `<div style="margin-top:9px;padding-top:7px;border-top:1px solid rgba(255,255,255,0.08)">
				<div style="position:relative;height:6px;border-radius:3px;overflow:hidden;background:rgba(255,255,255,0.05)">
					<div style="position:absolute;inset:0;background:${grad};opacity:0.18"></div>
					${fill}
				</div>
				${thrLine}
				<div style="display:flex;justify-content:space-between;margin-top:4px;font-size:11px;color:#9c9ca8">
					<span>${cfg.dMin}°</span>
					<span style="color:#d8d8e0">${label}${thr != null ? ` · threshold ${thr}°` : ""}</span>
					<span>${cfg.dMax}°</span>
				</div>
			</div>`;
		};

		const clearHover = () => {
			if (popup) { popup.remove(); popup = null; }
			hoverSource()?.setData(EMPTY_FC);
			map!.getCanvas().style.cursor = "";
			hoveredCell = null;
		};

		// Map European country codes to IANA timezone names for local-time display.
		const COUNTRY_TZ: Record<string, string> = {
			AL: 'Europe/Tirane',    AT: 'Europe/Vienna',    BA: 'Europe/Sarajevo',
			BE: 'Europe/Brussels',  BG: 'Europe/Sofia',     BY: 'Europe/Minsk',
			CH: 'Europe/Zurich',    CY: 'Asia/Nicosia',     CZ: 'Europe/Prague',
			DE: 'Europe/Berlin',    DK: 'Europe/Copenhagen',EE: 'Europe/Tallinn',
			ES: 'Europe/Madrid',    FI: 'Europe/Helsinki',  FR: 'Europe/Paris',
			GB: 'Europe/London',    GR: 'Europe/Athens',    HR: 'Europe/Zagreb',
			HU: 'Europe/Budapest',  IE: 'Europe/Dublin',    IS: 'Atlantic/Reykjavik',
			IT: 'Europe/Rome',      LI: 'Europe/Vaduz',     LT: 'Europe/Vilnius',
			LU: 'Europe/Luxembourg',LV: 'Europe/Riga',      MC: 'Europe/Monaco',
			MD: 'Europe/Chisinau',  ME: 'Europe/Podgorica', MK: 'Europe/Skopje',
			MT: 'Europe/Malta',     NL: 'Europe/Amsterdam', NO: 'Europe/Oslo',
			PL: 'Europe/Warsaw',    PT: 'Europe/Lisbon',    RO: 'Europe/Bucharest',
			RS: 'Europe/Belgrade',  SE: 'Europe/Stockholm', SI: 'Europe/Ljubljana',
			SK: 'Europe/Bratislava',UA: 'Europe/Kyiv',      XK: 'Europe/Belgrade',
		};

		function peakLocalTime(utcHM: string, country: string): string {
			const tz = COUNTRY_TZ[country];
			const [h, m] = utcHM.split(':').map(Number);
			const d = new Date();
			d.setUTCHours(h, m, 0, 0);
			if (tz) {
				return d.toLocaleTimeString('en', { timeZone: tz, hour: '2-digit', minute: '2-digit', hour12: false });
			}
			return utcHM + ' UTC';
		}

		// Build the tooltip HTML. `region` is filled in once /api/region resolves.
		const buildHTML = (
			props: Record<string, any>,
			view: string,
			region: { region?: string; regionBroad?: string } | undefined,
		) => {
			const hasRange = props.minTemperature !== null && props.minTemperature !== undefined;
			const tempVal = hasRange ? fmtRange(props.minTemperature, props.temperature) : fmt(props.temperature);
			const feelsVal = hasRange ? fmtRange(props.minApparentTemperature, props.apparentTemperature) : fmt(props.apparentTemperature);
			const diffVal = fmtSigned(props.anomalyC);
			const rows = [
				row(VIEW_META.difference.label, diffVal, view === "difference"),
				row(VIEW_META.temperature.label, tempVal, view === "temperature"),
				row(VIEW_META.apparent_temperature.label, feelsVal, view === "apparent_temperature"),
			].join("");
			const countryName = COUNTRY_NAMES[props.country] || props.country;
			let regionLine = "";
			if (region?.region) {
				const broad = region.regionBroad && region.regionBroad !== region.region ? ` · ${region.regionBroad}` : "";
				regionLine = `<div style="color:#b6b6c4;font-size:12px;line-height:1.3;margin-top:1px;">${region.region}${broad}</div>`;
			}
			return `
				<div style="font-family:Barlow,system-ui,sans-serif;font-size:14px;line-height:1.6;min-width:215px;">
					<div style="margin-bottom:6px;">
						<strong style="color:#f2f2f6;font-size:15px;">${countryName}</strong>
						${regionLine}
					</div>
					<div style="font-size:11px;text-transform:uppercase;letter-spacing:0.07em;color:#9c9ca8;margin-bottom:4px;">
						${{ yesterday: 'Yesterday', today: 'Today', tomorrow: 'Tomorrow' }[get(selectedDate)]}${hasRange ? " (range)" : ""}
					</div>
					${rows}
					${buildBar(props, view)}
					<div style="margin-top:8px;padding-top:5px;border-top:1px solid rgba(255,255,255,0.08);color:#aaaab4;font-size:12px;display:flex;justify-content:space-between;align-items:center;">
						<span>${Number(props.population).toLocaleString("en")} residents</span>
						${props.peakHour ? `<span>Peak ${peakLocalTime(props.peakHour, props.country)}</span>` : ""}
					</div>
				</div>`;
		};

		// Fetch the region for a cell once, cache it, and patch the open tooltip
		// if it's still showing that same cell when the response arrives.
		const fetchRegion = (lon: number, lat: number, props: Record<string, any>) => {
			const key = `${lon},${lat}`;
			if (regionCache.has(key)) return;
			fetch(`/api/region?lon=${lon}&lat=${lat}`)
				.then((r) => (r.ok ? r.json() : {}))
				.then((region) => {
					regionCache.set(key, region || {});
					if (popup && hoveredCell && hoveredCell.lon === lon && hoveredCell.lat === lat) {
						popup.setHTML(buildHTML(props, currentView, region));
					}
				})
				.catch(() => regionCache.set(key, {}));
		};

		const handleMove = (e: maplibregl.MapMouseEvent) => {
			if (!map) return;
			const px = e.point;
			const R = 13;
			const features = map.queryRenderedFeatures(
				[[px.x - R, px.y - R], [px.x + R, px.y + R]],
				{ layers: ["grid-circles"] },
			);
			if (!features.length) { clearHover(); return; }

			let best: (typeof features)[0] | null = null;
			let bestDist = Infinity;
			for (const f of features) {
				const geom = f.geometry as any;
				if (geom.type !== "Point") continue;
				const [lon, lat] = geom.coordinates as [number, number];
				const pt = map.project([lon, lat]);
				const d = Math.hypot(pt.x - px.x, pt.y - px.y);
				if (d < bestDist) { bestDist = d; best = f; }
			}
			if (!best || bestDist > 14) { clearHover(); return; }

			const props = best.properties as Record<string, any>;
			const [gLon, gLat] = (best.geometry as any).coordinates as [number, number];
			// Region lookup key: prefer the tile-safe lat/lon baked into the
			// feature props. MapLibre re-encodes geometry coordinates through its
			// tile pipeline, so `gLon`/`gLat` drift by ~0.001° and miss the
			// /api/region key (which is keyed to the population grid at 3dp).
			const lon = props.lon ?? Math.round(gLon * 1000) / 1000;
			const lat = props.lat ?? Math.round(gLat * 1000) / 1000;
			if (hoveredCell && hoveredCell.lat === lat && hoveredCell.lon === lon) return;
			hoveredCell = { lat, lon };

			map.getCanvas().style.cursor = "pointer";
			hoverSource()?.setData({
				type: "FeatureCollection",
				features: [{ type: "Feature", geometry: best.geometry as any, properties: { population: props.population } }],
			} as any);

			const region = regionCache.get(`${lon},${lat}`);
			const html = buildHTML(props, currentView, region);
			if (popup) popup.remove();
			popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10 })
				.setLngLat(e.lngLat)
				.setHTML(html)
				.addTo(map!);

			if (region === undefined) fetchRegion(lon, lat, props);
		};

		// Throttle to at most one query per animation frame — mousemove fires far
		// more often than that, and queryRenderedFeatures over 174k features isn't
		// free.
		let rafPending = false;
		let lastEvent: maplibregl.MapMouseEvent | null = null;
		map.on("mousemove", (e) => {
			lastEvent = e;
			if (rafPending) return;
			rafPending = true;
			requestAnimationFrame(() => { rafPending = false; if (lastEvent) handleMove(lastEvent); });
		});

		// `mouseout` is the map-level event for the pointer leaving the canvas
		// (`mouseleave` only fires when bound to a specific layer). A DOM-level
		// listener on the container is a belt-and-braces backup that also fires
		// when the pointer moves onto an overlay sitting above the map (the view
		// switch, legend, zoom controls) rather than off the map entirely.
		map.on("mouseout", () => clearHover());
		mapContainer.addEventListener("mouseleave", clearHover);
		clearHoverDom = () => mapContainer.removeEventListener("mouseleave", clearHover);

		map.on("click", (e) => {
			const buffer = 6;
			const bbox: [maplibregl.PointLike, maplibregl.PointLike] = [
				[e.point.x - buffer, e.point.y - buffer],
				[e.point.x + buffer, e.point.y + buffer],
			];
			const features = map!.queryRenderedFeatures(bbox, { layers: ["grid-circles"] });
			if (features.length === 0) return;
			const country = features[0].properties.country;
			if ($selectedCountry === country) {
				goto("/", { replaceState: false });
			} else {
				const slug = CODE_TO_SLUG[country];
				if (slug) goto(`/${slug}`, { replaceState: false });
			}
		});
	}

	// Re-render when grid data changes (renderGrid no-ops until the style is
	// ready; the load handler calls it again once it is). When gridData is
	// cleared at the start of loadData(), blank the map immediately so it stays
	// in sync with the headline skeleton.
	$: if ($gridData) {
		renderGrid();
	} else if (map && map.getSource('grid')) {
		(map.getSource('grid') as maplibregl.GeoJSONSource).setData(EMPTY_FC);
	}

	// Dim non-selected country (reacts to $selectedCountry; uses the locally
	// tracked currentView, which is kept in sync by the subscription below).
	$: if (map && map.getLayer("grid-circles")) {
		map.setPaintProperty("grid-circles", "circle-opacity", buildOpacityExpr(currentView, $selectedCountry));
	}

	// Recolour on view/threshold change via explicit store subscriptions. (A `$:`
	// block referencing `$mapView` did not re-fire reliably here.) applyViewPaint
	// sets colour, opacity and stroke for the active view + threshold.
	let unsubs: Array<() => void> = [];

	onMount(() => {
		initMap();
		let didFirstResizingSub = false;
		unsubs.push(
			mapView.subscribe((v) => {
				currentView = v;
				if (map && map.getLayer("grid-circles")) applyViewPaint();
			}),
			thresholds.subscribe((t) => {
				currentThresholds = t;
				if (map && map.getLayer("grid-circles")) applyViewPaint();
			}),
			// Zoom/centre to the selected country (from the dropdown or a map
			// click). Fires immediately on subscribe with the current value, but
			// maybeFitCountry no-ops until the grid layer exists, after which
			// addGridLayer's own call handles the initial fit.
			selectedCountry.subscribe(() => maybeFitCountry()),
			// When the hero/map drag ends, resize the map once to fit the new size.
			isMapResizing.subscribe((resizing) => {
				if (!didFirstResizingSub) { didFirstResizingSub = true; return; }
				if (!resizing) requestAnimationFrame(() => map?.resize());
			}),
		);
	});
	onDestroy(() => {
		unsubs.forEach((u) => u());
		clearHoverDom?.();
		resizeObserver?.disconnect();
		mapInstance.set(null);
		if (popup) popup.remove();
		if (map) map.remove();
	});
</script>

<div class="absolute top-0 left-0 w-full h-full" bind:this={mapContainer}></div>

<style>
	:global(.maplibregl-ctrl-top-left) { top: 12px; left: 12px; }
	:global(.maplibregl-ctrl-bottom-left) { bottom: 12px; left: 12px; }

	:global(.maplibregl-ctrl-group) {
		background: rgba(10, 10, 18, 0.82) !important;
		backdrop-filter: blur(8px);
		border: 1px solid rgba(255, 255, 255, 0.08) !important;
		border-radius: 8px !important;
	}
	:global(.maplibregl-ctrl-group button) {
		background-color: transparent !important;
		border-color: rgba(255, 255, 255, 0.06) !important;
	}
	:global(.maplibregl-ctrl-group button + button) {
		border-top: 1px solid rgba(255, 255, 255, 0.06) !important;
	}
	:global(.maplibregl-ctrl-group button .maplibregl-ctrl-icon) { filter: invert(1) brightness(0.7); }
	:global(.maplibregl-ctrl-group button:hover .maplibregl-ctrl-icon) { filter: invert(1) brightness(1); }

	:global(.maplibregl-ctrl-attrib) {
		background: rgba(10, 10, 18, 0.7) !important;
		font-size: 0.65rem !important;
		border-radius: 4px;
	}
	:global(.maplibregl-ctrl-attrib a) { color: #808090 !important; }

	:global(.maplibregl-popup-content) {
		background: rgba(12, 12, 20, 0.94) !important;
		color: #e8e8e8 !important;
		border: 1px solid rgba(255, 255, 255, 0.1) !important;
		border-radius: 8px !important;
		padding: 10px 14px !important;
		box-shadow: 0 4px 24px rgba(0, 0, 0, 0.6) !important;
		backdrop-filter: blur(12px);
	}
	:global(.maplibregl-popup-tip) { border-top-color: rgba(12, 12, 20, 0.94) !important; }
</style>
