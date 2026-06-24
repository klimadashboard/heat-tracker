#!/usr/bin/env node
/**
 * Downloads Natural Earth 50m country boundaries and extracts Turkey's polygon,
 * saving it as a GeoJSON file for use as a map outline.
 */

import { writeFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT = join(__dirname, '..', 'static', 'turkey-border.json');

const NE_URL = 'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_countries.geojson';

console.log('Downloading Natural Earth 50m boundaries...');
const res = await fetch(NE_URL);
if (!res.ok) throw new Error(`Download failed: ${res.status}`);
const geojson = await res.json();

const turkey = geojson.features.find(f => {
  const p = f.properties;
  return p.ISO_A2 === 'TR' || p.ISO_A2_EH === 'TR';
});

if (!turkey) throw new Error('Turkey not found in Natural Earth data');

const out = {
  type: 'FeatureCollection',
  features: [{ type: 'Feature', geometry: turkey.geometry, properties: { country: 'TR' } }],
};

writeFileSync(OUT, JSON.stringify(out));
console.log(`Saved Turkey border to ${OUT}`);
