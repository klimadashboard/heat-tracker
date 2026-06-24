#!/usr/bin/env node
/**
 * Extract population from GHS-POP R2023A GeoTIFF tiles onto a 0.125° grid
 * covering Europe, then assign countries using Natural Earth 50m boundaries.
 *
 * 1. Reads each downloaded GeoTIFF tile using the `geotiff` npm package
 * 2. For each point in a 0.125° grid (lat 34-72, lon -25 to 45), sums the
 *    population from all 30-arcsecond raster cells that fall within that
 *    0.125° cell
 * 3. Downloads Natural Earth 50m admin-0 countries and assigns each grid
 *    cell a country code using turf.js point-in-polygon
 * 4. Only includes points with population > 0
 * 5. Outputs [{lat, lon, country, pop}, ...]
 */

import { readFileSync, writeFileSync, existsSync, mkdirSync, readdirSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { fromArrayBuffer } from 'geotiff';
import * as turf from '@turf/turf';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DATA_DIR = join(__dirname, '..', 'data');
const TILES_DIR = join(DATA_DIR, 'ghspop-tiles');

// Europe bounding box
const LAT_MIN = 34;
const LAT_MAX = 72;
const LON_MIN = -25;
const LON_MAX = 45;
const STEP = 0.0625;

// European country ISO A2 codes (same as build-population-grid.js)
const EUROPE_CODES = new Set([
  'AL', 'AD', 'AT', 'BY', 'BE', 'BA', 'BG', 'HR', 'CY', 'CZ',
  'DK', 'EE', 'FI', 'FR', 'DE', 'GR', 'HU', 'IS', 'IE', 'IT',
  'XK', 'LV', 'LT', 'LU', 'MK', 'MT', 'MD', 'ME', 'NL', 'NO',
  'PL', 'PT', 'RO', 'RS', 'SK', 'SI', 'ES', 'SE', 'CH', 'UA', 'GB',
  'TR',
]);

// Turkey: European part (East Thrace) is north of ~40°N and west of ~29.5°E.
// Without the lat check, the western Aegean coast of Asian Turkey leaks in.
const TURKEY_EUROPE_LON_MAX = 29.5;
const TURKEY_EUROPE_LAT_MIN = 39.8;

// Crimea — Natural Earth 50m marks it as disputed, so it falls out of Ukraine's
// polygon. We use a coastline polygon to assign Crimean cells to Ukraine without
// including sea cells from the surrounding Black Sea and Sea of Azov.
const CRIMEA_POLY = turf.polygon([[
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
  [36.65, 45.2],   // Kerch tip
  // South coast of Kerch heading west
  [36.45, 45.0],
  [36.15, 44.9],
  [35.8, 44.8],
  [35.4, 44.65],
  [35.0, 44.5],
  [34.6, 44.4],
  [34.2, 44.35],   // Yalta area
  [33.8, 44.38],
  [33.5, 44.5],    // Sevastopol
  [33.2, 44.55],
  // West coast heading north
  [32.85, 44.7],
  [32.55, 44.9],   // Cape Tarkhankut
  [32.45, 45.15],
  [32.5, 45.35],
  [32.7, 45.55],
  // NW coast back to Perekop
  [33.0, 45.75],
  [33.2, 45.95],
  [33.55, 46.15],  // close ring
]]);

function isInCrimea(lat, lon) {
  return turf.booleanPointInPolygon(turf.point([lon, lat]), CRIMEA_POLY);
}

// ─── GeoTIFF tile handling ──────────────────────────────────────────────

/**
 * Load a GeoTIFF tile and return its image + georef info.
 */
async function loadTile(filepath) {
  const buf = readFileSync(filepath);
  const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
  const tiff = await fromArrayBuffer(ab);
  const image = await tiff.getImage();

  const bbox = image.getBoundingBox(); // [west, south, east, north]
  const width = image.getWidth();
  const height = image.getHeight();
  const [sx, sy] = image.getResolution(); // [xRes, yRes] — yRes is negative

  // Origin = upper-left corner
  const origin = image.getOrigin(); // [x, y]

  return { image, bbox, width, height, sx, sy: Math.abs(sy), originX: origin[0], originY: origin[1] };
}

/**
 * For a loaded tile, aggregate population within a 0.125° cell centered at
 * (cellCenterLat, cellCenterLon). We sum all 30-arcsec raster pixels whose
 * centres fall inside the 0.125° cell.
 *
 * Rather than reading each pixel individually (slow), we pre-load raster
 * data per tile and index into it.
 */
function samplePopulation(rasterData, tileInfo, cellLat, cellLon) {
  const { width, height, sx, sy, originX, originY } = tileInfo;

  // Cell bounds (0.125° cell, cellLat/cellLon is the center)
  const halfStep = STEP / 2;
  const cellWest = cellLon - halfStep;
  const cellEast = cellLon + halfStep;
  const cellSouth = cellLat - halfStep;
  const cellNorth = cellLat + halfStep;

  // Convert geographic bounds to pixel indices using pixel-centre half-open intervals
  // [west, east) × [south, north) — each pixel belongs to exactly one cell, no boundary double-counting.
  //
  // Pixel k centre lon = originX + (k + 0.5) * sx
  //   include if centre >= cellWest  →  k >= ceil((cellWest - originX)/sx - 0.5)
  //   include if centre <  cellEast  →  k <= ceil((cellEast  - originX)/sx - 0.5) - 1
  //
  // Pixel row k centre lat = originY - (k + 0.5) * sy  (rows increase southward)
  //   include if centre <  cellNorth →  k >= floor((originY - cellNorth)/sy - 0.5) + 1
  //   include if centre >= cellSouth →  k <= floor((originY - cellSouth)/sy - 0.5)
  const colMin = Math.max(0,          Math.ceil( (cellWest  - originX) / sx - 0.5));
  const colMax = Math.min(width  - 1, Math.ceil( (cellEast  - originX) / sx - 0.5) - 1);
  const rowMin = Math.max(0,          Math.floor((originY - cellNorth)  / sy - 0.5) + 1);
  const rowMax = Math.min(height - 1, Math.floor((originY - cellSouth)  / sy - 0.5));

  if (colMin > colMax || rowMin > rowMax) return 0;

  let sum = 0;
  for (let row = rowMin; row <= rowMax; row++) {
    for (let col = colMin; col <= colMax; col++) {
      const val = rasterData[row * width + col];
      // GHS-POP uses float values; nodata is typically a very negative number
      if (val > 0) {
        sum += val;
      }
    }
  }
  return sum;
}

// ─── Country assignment (from build-population-grid.js) ─────────────────

async function downloadCountries() {
  const url = 'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_countries.geojson';
  console.log('Downloading Natural Earth 50m country boundaries...');
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Download failed: ${res.status}`);
  const geojson = await res.json();
  console.log(`  ${geojson.features.length} countries loaded`);
  return geojson;
}

function getCountryCode(feature) {
  const props = feature.properties;
  let code = props.ISO_A2;
  if (!code || code === '-99') code = props.ISO_A2_EH;
  if (!code || code === '-99') {
    if (props.NAME === 'Kosovo') return 'XK';
    if (props.NAME === 'France') return 'FR';
    if (props.NAME === 'Norway') return 'NO';
    return null;
  }
  return code;
}

function buildCountryIndex(countriesGeoJSON) {
  const countryPolygons = [];
  for (const feature of countriesGeoJSON.features) {
    const code = getCountryCode(feature);
    if (!code || !EUROPE_CODES.has(code)) continue;
    countryPolygons.push({ code, feature });
  }
  console.log(`  Found ${countryPolygons.length} European country polygons`);
  return countryPolygons;
}

function findCountry(countryPolygons, lat, lon) {
  const point = turf.point([lon, lat]);
  for (const { code, feature } of countryPolygons) {
    try {
      if (turf.booleanPointInPolygon(point, feature)) {
        // Turkey: only European part (East Thrace) — north of ~40°N and west of 29.5°E
        if (code === 'TR' && (lon > TURKEY_EUROPE_LON_MAX || lat < TURKEY_EUROPE_LAT_MIN)) continue;
        return code;
      }
    } catch {
      // Skip invalid geometries
    }
  }
  // Crimea fallback — if no country matched but we're in the Crimea bounding box,
  // assign to Ukraine
  if (isInCrimea(lat, lon)) {
    return 'UA';
  }
  return null;
}

// ─── Main ───────────────────────────────────────────────────────────────

async function main() {
  console.log('GHS-POP Population Grid Extractor');
  console.log('='.repeat(50));
  console.log(`Grid: ${LAT_MIN}-${LAT_MAX}°N, ${LON_MIN}-${LON_MAX}°E, step ${STEP}°\n`);

  // Step 1: Find all TIF tiles
  const tifFiles = readdirSync(TILES_DIR)
    .filter(f => f.endsWith('.tif'))
    .sort();
  console.log(`Found ${tifFiles.length} GeoTIFF tiles in ${TILES_DIR}\n`);

  if (tifFiles.length === 0) {
    console.error('No TIF files found! Run download first.');
    process.exit(1);
  }

  // Step 2: Load all tiles — read their raster data and georef into memory
  console.log('Loading tiles into memory...');
  const tiles = [];
  for (const f of tifFiles) {
    const filepath = join(TILES_DIR, f);
    process.stdout.write(`  Loading ${f}...`);
    const tileInfo = await loadTile(filepath);
    const rasters = await tileInfo.image.readRasters();
    const rasterData = rasters[0]; // Band 0
    tiles.push({ filename: f, rasterData, tileInfo });
    process.stdout.write(` ${tileInfo.width}x${tileInfo.height}, bbox=[${tileInfo.bbox.map(v => v.toFixed(2)).join(', ')}]\n`);
  }
  console.log(`\nAll ${tiles.length} tiles loaded.\n`);

  // Step 3: For each 0.125° cell, sum population from matching tile(s)
  console.log('Sampling population on 0.125° grid...');
  const latSteps = Math.round((LAT_MAX - LAT_MIN) / STEP);
  const lonSteps = Math.round((LON_MAX - LON_MIN) / STEP);
  const totalCells = latSteps * lonSteps;
  console.log(`  Grid dimensions: ${lonSteps} x ${latSteps} = ${totalCells} cells\n`);

  // Accumulator: key "lat,lon" -> population sum
  const popGrid = new Map();
  let processedCells = 0;
  let lastPct = -1;

  for (let latIdx = 0; latIdx < latSteps; latIdx++) {
    const cellCenterLat = Math.round((LAT_MIN + latIdx * STEP + STEP / 2) * 1000) / 1000;

    for (let lonIdx = 0; lonIdx < lonSteps; lonIdx++) {
      const cellCenterLon = Math.round((LON_MIN + lonIdx * STEP + STEP / 2) * 1000) / 1000;
      processedCells++;

      // Find which tile(s) cover this cell
      const halfStep = STEP / 2;
      const cellWest = cellCenterLon - halfStep;
      const cellEast = cellCenterLon + halfStep;
      const cellSouth = cellCenterLat - halfStep;
      const cellNorth = cellCenterLat + halfStep;

      let cellPop = 0;
      for (const { rasterData, tileInfo } of tiles) {
        const [tileWest, tileSouth, tileEast, tileNorth] = tileInfo.bbox;
        // Check if tile overlaps with cell
        if (cellEast <= tileWest || cellWest >= tileEast ||
            cellNorth <= tileSouth || cellSouth >= tileNorth) {
          continue; // No overlap
        }
        cellPop += samplePopulation(rasterData, tileInfo, cellCenterLat, cellCenterLon);
      }

      if (cellPop > 0) {
        popGrid.set(`${cellCenterLat},${cellCenterLon}`, {
          lat: cellCenterLat,
          lon: cellCenterLon,
          pop: Math.round(cellPop),
        });
      }
    }

    // Progress
    const pct = Math.round((latIdx / latSteps) * 100);
    if (pct !== lastPct && pct % 5 === 0) {
      process.stdout.write(`\r  Progress: ${pct}%`);
      lastPct = pct;
    }
  }
  process.stdout.write(`\r  Progress: 100%\n`);

  console.log(`\n  Total cells processed: ${processedCells}`);
  console.log(`  Cells with pop > 0: ${popGrid.size}`);

  // Step 4: Country assignment
  console.log('\nAssigning countries...');
  const countriesGeoJSON = await downloadCountries();
  const countryPolygons = buildCountryIndex(countriesGeoJSON);

  const grid = [];
  let assigned = 0;
  let unassigned = 0;
  let lastPct2 = -1;
  const entries = [...popGrid.values()];

  for (let i = 0; i < entries.length; i++) {
    const { lat, lon, pop } = entries[i];
    const country = findCountry(countryPolygons, lat, lon);

    if (country && pop > 0) {
      grid.push({ lat, lon, country, pop });
      assigned++;
    } else if (!country) {
      // Still include cells with population but no country match (might be
      // coastal or on disputed borders) — assign "XX" for unknown
      // Actually, the original script only includes cells that match a country.
      // Let's skip unassigned to match the original behavior.
      unassigned++;
    }

    const pct = Math.round((i / entries.length) * 100);
    if (pct !== lastPct2 && pct % 5 === 0) {
      process.stdout.write(`\r  Country assignment: ${pct}%`);
      lastPct2 = pct;
    }
  }
  process.stdout.write(`\r  Country assignment: 100%\n`);

  console.log(`  Assigned to country: ${assigned}`);
  console.log(`  No country match (skipped): ${unassigned}`);

  // Step 5: Sort by lat desc, lon asc (north-to-south, west-to-east)
  grid.sort((a, b) => b.lat - a.lat || a.lon - b.lon);

  // Step 6: Summary
  const countrySummary = {};
  let grandTotal = 0;
  for (const cell of grid) {
    countrySummary[cell.country] = (countrySummary[cell.country] || { count: 0, pop: 0 });
    countrySummary[cell.country].count++;
    countrySummary[cell.country].pop += cell.pop;
    grandTotal += cell.pop;
  }

  console.log('\nCountry summary:');
  console.log('  Code  Cells       Population');
  console.log('  ' + '-'.repeat(40));
  const sorted = Object.entries(countrySummary).sort((a, b) => b[1].pop - a[1].pop);
  for (const [code, { count, pop }] of sorted) {
    console.log(`  ${code.padEnd(4)} ${String(count).padStart(5)}   ${(pop / 1e6).toFixed(2).padStart(10)}M`);
  }
  console.log(`\n  Total: ${grid.length} cells, ${(grandTotal / 1e6).toFixed(1)}M people across ${sorted.length} countries`);

  // Step 7: Write output
  if (!existsSync(DATA_DIR)) mkdirSync(DATA_DIR, { recursive: true });
  const outputPath = join(DATA_DIR, 'population-grid.json');
  writeFileSync(outputPath, JSON.stringify(grid));
  const fileSizeKB = (Buffer.byteLength(JSON.stringify(grid)) / 1024).toFixed(0);
  console.log(`\nSaved ${grid.length} grid cells to ${outputPath}`);
  console.log(`File size: ${fileSizeKB} KB`);
  console.log('\nDone!');
}

main().catch(err => {
  console.error('Fatal error:', err);
  process.exit(1);
});
