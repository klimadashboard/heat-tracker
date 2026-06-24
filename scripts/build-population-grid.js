#!/usr/bin/env node
/**
 * Build a 0.125° population grid for Europe using ACTUAL country polygons.
 *
 * 1. Download Natural Earth 50m admin-0 countries (GeoJSON from GitHub)
 * 2. For each 0.125° grid cell center, test which country polygon it falls in
 * 3. Only create cells that fall inside a country polygon (no ocean dots)
 * 4. Distribute country population across its cells (uniform for now)
 * 5. Output as compact JSON
 */

import { writeFileSync, existsSync, mkdirSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import * as turf from "@turf/turf";

const __dirname = dirname(fileURLToPath(import.meta.url));
const DATA_DIR = join(__dirname, "..", "data");

// Europe bounding box
const LAT_MIN = 34;
const LAT_MAX = 72;
const LON_MIN = -25; // wider to catch Iceland/Portugal
const LON_MAX = 45;
const STEP = 0.125; // 0.125° grid (~14km) — captures urban density patterns

// ISO A2 codes for European countries we want to include
const EUROPE_CODES = new Set([
	"AL",
	"AD",
	"AT",
	"BY",
	"BE",
	"BA",
	"BG",
	"HR",
	"CY",
	"CZ",
	"DK",
	"EE",
	"FI",
	"FR",
	"DE",
	"GR",
	"HU",
	"IS",
	"IE",
	"IT",
	"XK",
	"LV",
	"LT",
	"LU",
	"MK",
	"MT",
	"MD",
	"ME",
	"NL",
	"NO",
	"PL",
	"PT",
	"RO",
	"RS",
	"SK",
	"SI",
	"ES",
	"SE",
	"CH",
	"UA",
	"GB",
	// Turkey — we'll handle separately (European part only)
	"TR",
]);

// European country populations (2024 estimates, thousands)
const COUNTRY_POPULATIONS = {
	"AL": 2793,
	"AD": 80,
	"AT": 9105,
	"BY": 9200,
	"BE": 11686,
	"BA": 3210,
	"BG": 6520,
	"HR": 3862,
	"CY": 1260,
	"CZ": 10900,
	"DK": 5933,
	"EE": 1366,
	"FI": 5563,
	"FR": 68170,
	"DE": 84483,
	"GR": 10341,
	"HU": 9597,
	"IS": 383,
	"IE": 5194,
	"IT": 58762,
	"XK": 1770,
	"LV": 1842,
	"LT": 2860,
	"LU": 672,
	"MK": 1836,
	"MT": 542,
	"MD": 2598,
	"ME": 616,
	"NL": 17944,
	"NO": 5500,
	"PL": 36753,
	"PT": 10379,
	"RO": 19003,
	"RS": 6647,
	"SK": 5460,
	"SI": 2120,
	"ES": 48197,
	"SE": 10551,
	"CH": 8921,
	"TR": 12000, // European part only (East Thrace)
	"UA": 36744,
	"GB": 67886,
};

const COUNTRY_NAMES = {
	"AL": "Albania",
	"AD": "Andorra",
	"AT": "Austria",
	"BY": "Belarus",
	"BE": "Belgium",
	"BA": "Bosnia & Herz.",
	"BG": "Bulgaria",
	"HR": "Croatia",
	"CY": "Cyprus",
	"CZ": "Czechia",
	"DK": "Denmark",
	"EE": "Estonia",
	"FI": "Finland",
	"FR": "France",
	"DE": "Germany",
	"GR": "Greece",
	"HU": "Hungary",
	"IS": "Iceland",
	"IE": "Ireland",
	"IT": "Italy",
	"XK": "Kosovo",
	"LV": "Latvia",
	"LT": "Lithuania",
	"LU": "Luxembourg",
	"MK": "N. Macedonia",
	"MT": "Malta",
	"MD": "Moldova",
	"ME": "Montenegro",
	"NL": "Netherlands",
	"NO": "Norway",
	"PL": "Poland",
	"PT": "Portugal",
	"RO": "Romania",
	"RS": "Serbia",
	"SK": "Slovakia",
	"SI": "Slovenia",
	"ES": "Spain",
	"SE": "Sweden",
	"CH": "Switzerland",
	"TR": "Turkey",
	"UA": "Ukraine",
	"GB": "United Kingdom",
};

// Turkey: European part (East Thrace) is north of ~40°N and west of ~29.5°E.
const TURKEY_EUROPE_LON_MAX = 29.5;
const TURKEY_EUROPE_LAT_MIN = 39.8;

// Crimea — Natural Earth 50m marks it as disputed, so it falls out of Ukraine's
// polygon. We use a coastline polygon to assign Crimean cells to Ukraine without
// including sea cells from the surrounding Black Sea and Sea of Azov.
const CRIMEA_POLY = turf.polygon([
	[
		// Perekop isthmus (north connection to mainland), clockwise
		[33.55, 46.15],
		[33.8, 46.1],
		[34.1, 46.1],
		[34.5, 46.0],
		[34.85, 45.85],
		// East coast — Arabat Spit junction, south along coast
		[35.1, 45.65],
		[35.35, 45.5],
		[35.55, 45.4],
		// Kerch Peninsula
		[35.85, 45.4],
		[36.15, 45.4],
		[36.45, 45.35],
		[36.65, 45.2], // Kerch tip
		// South coast of Kerch heading west
		[36.45, 45.0],
		[36.15, 44.9],
		[35.8, 44.8],
		[35.4, 44.65],
		[35.0, 44.5],
		[34.6, 44.4],
		[34.2, 44.35], // Yalta area
		[33.8, 44.38],
		[33.5, 44.5], // Sevastopol
		[33.2, 44.55],
		// West coast heading north
		[32.85, 44.7],
		[32.55, 44.9], // Cape Tarkhankut
		[32.45, 45.15],
		[32.5, 45.35],
		[32.7, 45.55],
		// NW coast back to Perekop
		[33.0, 45.75],
		[33.2, 45.95],
		[33.55, 46.15], // close ring
	],
]);

function isInCrimea(lat, lon) {
	return turf.booleanPointInPolygon(turf.point([lon, lat]), CRIMEA_POLY);
}

async function downloadCountries() {
	// Natural Earth 50m admin-0 countries — GeoJSON from GitHub
	const url =
		"https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_countries.geojson";

	console.log("Downloading Natural Earth 50m country boundaries...");
	const res = await fetch(url);
	if (!res.ok) throw new Error(`Download failed: ${res.status}`);
	const geojson = await res.json();
	console.log(`  ${geojson.features.length} countries loaded`);
	return geojson;
}

function getCountryCode(feature) {
	// Natural Earth uses various fields for ISO codes
	const props = feature.properties;
	// Try ISO_A2 first, then ISO_A2_EH (which handles -99 edge cases)
	let code = props.ISO_A2;
	if (!code || code === "-99") code = props.ISO_A2_EH;
	if (!code || code === "-99") {
		// Special cases
		if (props.NAME === "Kosovo") return "XK";
		if (props.NAME === "France") return "FR";
		if (props.NAME === "Norway") return "NO";
		return null;
	}
	return code;
}

function buildGrid(countriesGeoJSON) {
	console.log("\nBuilding 0.125° population grid...");

	// Extract European country polygons
	const countryPolygons = [];
	for (const feature of countriesGeoJSON.features) {
		const code = getCountryCode(feature);
		if (!code || !EUROPE_CODES.has(code)) continue;

		countryPolygons.push({
			code,
			feature,
		});
	}
	console.log(`  Found ${countryPolygons.length} European country polygons`);

	// For each grid cell, find which country it belongs to
	const cells = [];
	const countryCellCounts = {};
	let totalPoints = 0;
	let landPoints = 0;

	const latSteps = Math.ceil((LAT_MAX - LAT_MIN) / STEP);
	const lonSteps = Math.ceil((LON_MAX - LON_MIN) / STEP);
	const totalGridPoints = latSteps * lonSteps;
	console.log(
		`  Grid: ${latSteps}x${lonSteps} = ${totalGridPoints} points to test`,
	);

	let lastPct = 0;
	for (let lat = LAT_MIN; lat < LAT_MAX; lat += STEP) {
		for (let lon = LON_MIN; lon < LON_MAX; lon += STEP) {
			totalPoints++;
			const centerLat = Math.round((lat + STEP / 2) * 1000) / 1000;
			const centerLon = Math.round((lon + STEP / 2) * 1000) / 1000;
			const point = turf.point([centerLon, centerLat]);

			let matchedCountry = null;
			for (const { code, feature } of countryPolygons) {
				try {
					if (turf.booleanPointInPolygon(point, feature)) {
						// Turkey: only European part (East Thrace)
						if (
							code === "TR" &&
							(centerLon > TURKEY_EUROPE_LON_MAX ||
								centerLat < TURKEY_EUROPE_LAT_MIN)
						)
							continue;
						matchedCountry = code;
						break;
					}
				} catch {
					// Skip invalid geometries
				}
			}
			// Crimea fallback
			if (!matchedCountry && isInCrimea(centerLat, centerLon)) {
				matchedCountry = "UA";
			}

			if (matchedCountry) {
				landPoints++;
				cells.push({ lat: centerLat, lon: centerLon, country: matchedCountry });
				countryCellCounts[matchedCountry] =
					(countryCellCounts[matchedCountry] || 0) + 1;
			}
		}

		// Progress
		const pct = Math.round(((lat - LAT_MIN) / (LAT_MAX - LAT_MIN)) * 100);
		if (pct >= lastPct + 5) {
			process.stdout.write(`\r  Processing: ${pct}%`);
			lastPct = pct;
		}
	}
	process.stdout.write(`\r  Processing: 100%\n`);

	console.log(`  Total grid points: ${totalPoints}`);
	console.log(`  Land points (inside countries): ${landPoints}`);
	console.log(`  Ocean/outside points skipped: ${totalPoints - landPoints}`);

	// Distribute population across each country's cells
	const grid = cells.map((cell) => {
		const countryPop = (COUNTRY_POPULATIONS[cell.country] || 0) * 1000;
		const numCells = countryCellCounts[cell.country];
		const popPerCell = Math.round(countryPop / numCells);

		return {
			lat: cell.lat,
			lon: cell.lon,
			country: cell.country,
			pop: popPerCell,
		};
	});

	// Verification
	console.log("\nCountry verification:");
	console.log("  Code  Cells    Pop/Cell     Total Pop    Expected Pop");
	console.log("  " + "─".repeat(62));

	const countryTotals = {};
	for (const cell of grid) {
		countryTotals[cell.country] = (countryTotals[cell.country] || 0) + cell.pop;
	}

	const sorted = Object.entries(countryTotals).sort((a, b) => b[1] - a[1]);
	let grandTotal = 0;
	for (const [code, pop] of sorted) {
		const expected = (COUNTRY_POPULATIONS[code] || 0) * 1000;
		const ncells = countryCellCounts[code];
		const perCell = Math.round(pop / ncells);
		const diff = Math.abs(pop - expected);
		const mark = diff > 1000 ? " ~" : " =";
		console.log(
			`  ${code.padEnd(4)} ${String(ncells).padStart(5)}  ${perCell.toLocaleString().padStart(10)}/cell  ${(pop / 1e6).toFixed(2).padStart(8)}M  ${(expected / 1e6).toFixed(2).padStart(8)}M${mark}`,
		);
		grandTotal += pop;
	}
	console.log(
		`\n  Grand total: ${(grandTotal / 1e6).toFixed(1)}M people across ${sorted.length} countries`,
	);

	return grid;
}

async function main() {
	console.log("European Heat Tracker — Population Grid Builder");
	console.log("=".repeat(50));

	const countries = await downloadCountries();
	const grid = buildGrid(countries);

	if (!existsSync(DATA_DIR)) mkdirSync(DATA_DIR, { recursive: true });

	const outputPath = join(DATA_DIR, "population-grid.json");
	writeFileSync(outputPath, JSON.stringify(grid));
	console.log(`\nSaved ${grid.length} grid cells to ${outputPath}`);
	console.log(
		`File size: ${(Buffer.byteLength(JSON.stringify(grid)) / 1024).toFixed(0)} KB`,
	);

	writeFileSync(
		join(DATA_DIR, "countries.json"),
		JSON.stringify(COUNTRY_NAMES, null, 2),
	);
	console.log("Saved country names lookup");
	console.log("\nDone");
}

main().catch((err) => {
	console.error("Fatal:", err);
	process.exit(1);
});
