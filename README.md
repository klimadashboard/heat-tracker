# Europe Heat Tracker

A real-time map showing how many people across Europe are experiencing extreme heat. Built by [Klimadashboard](https://klimadashboard.org).

For every hour of the day the tracker answers: **how many people are currently living in a grid cell where the temperature exceeds 30 °C (or 35 °C, or whatever threshold you pick)?** It also shows how much hotter today is compared to the 1961–1990 average — both as a Europe-wide headline and as a per-cell anomaly map.

---

## How it works

```
DWD ICON-EU forecast (GRIB2)
        │  fetched hourly, 73 steps (0 h … 72 h ahead)
        ▼
scripts/fetch-dwd.py
        │  interpolates to ~175 k population-weighted grid cells
        │  computes apparent temperature (Steadman 1994)
        │  writes snapshots to SQLite
        ▼
data/heat-tracker.db   ◄──── data/climatology-1961-1990.npz
        │                          (E-OBS 1961–1990 reference)
        ▼
SvelteKit API routes (/api/current, /api/grid, …)
        │  pre-generated static JSON/GeoJSON served from disk
        ▼
SvelteKit frontend  ←  MapLibre GL map
```

**Data source:** [DWD ICON-EU](https://www.dwd.de/EN/ourservices/nwp_forecast_data/nwp_forecast_data.html) — Germany's national weather service, open data. The model runs every 3 hours and publishes hourly forecast steps; we ingest steps 0 h … 72 h ahead.

**Population grid:** ~175,000 cells built from [GHS-POP 2020](https://ghsl.jrc.ec.europa.eu/ghs_pop2023.php) at 30 arc-second resolution, clipped to Europe (Turkey excluded).

**Climatology reference:** Per-cell daily-mean and 90th-percentile temperatures computed from [E-OBS v30](https://www.ecad.eu/download/ensembles/download.php) (1961–1990), smoothed with a ±15-day window. Built once by `scripts/build-climatology.py`.

---

## Local development

### Prerequisites

- **Node.js 22+** (`node --version`)
- **Python 3.10+** with: `pip install cfgrib xarray scipy numpy requests`
- **eccodes** for GRIB2 parsing: `brew install eccodes` (macOS) or `apt-get install libeccodes-dev` (Linux)

### First-time setup

```bash
git clone https://github.com/klimadashboard/heat-tracker
cd heat-tracker
npm install
cp .env.example .env
```

If you want to run the data pipeline locally (optional — see the proxy tip below):

```bash
# Fetch the latest ICON-EU run and populate data/heat-tracker.db
python scripts/fetch-dwd.py

# Then start the dev server
npm run dev
```

**Tip:** If you just want to work on the frontend without running the Python pipeline, set `API_PROXY` in `.env` to proxy requests to the live server:

```
API_PROXY=https://heat-tracker.eu
```

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `HEAT_THRESHOLD` | `30` | Default heat threshold in °C for the "affected" headline |
| `PORT` | `3000` | HTTP server port |
| `API_PROXY` | — | Dev only: forward `/api` requests to this URL instead of the local DB |

---

## Data pipeline scripts

All scripts live in `scripts/`. They are separated into one-time setup scripts (run once to generate committed reference files) and the recurring cron script.

### Recurring (production cron)

**`scripts/fetch-dwd.py`** — the core pipeline, run hourly in production.

```bash
python scripts/fetch-dwd.py [--threshold 30] [--forecast-hours 72]

# First run of the day: also backfill analysis for earlier model runs
python scripts/fetch-dwd.py --backfill-today
```

Downloads the latest ICON-EU GRIB2 files for T_2M, RELHUM_2M, U_10M, and V_10M; interpolates them to the population grid; computes apparent temperature (Steadman 1994); writes hourly snapshots to `data/heat-tracker.db`; regenerates the pre-computed GeoJSON and JSON files that the API serves from disk.

### One-time setup

These scripts were used to build the committed reference files. You don't need to run them unless you're changing the grid or regional boundaries.

**`scripts/build-climatology.py`** — builds `data/climatology-1961-1990.npz` from the E-OBS NetCDF source. **Required for the anomaly headlines.** The output file (~404 MB) is deployed out-of-band to the server volume — see `data/README.md`.

```bash
# Download E-OBS first (4.3 GB):
# https://www.ecad.eu/download/ensembles/download.php
#   → TG (mean temperature) → 0.1° regular grid → Full period
python scripts/build-climatology.py --input data/eobs-tg-0.1deg-v30.nc
```

**`scripts/build-nuts-grid.py`** — tags each cell in `population-grid.json` with its NUTS-1/2/3 region name, writing `data/nuts-by-cell.json`. Requires `data/nuts.geojson` (download from [Eurostat GISCO](https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/NUTS_RG_03M_2021_4326.geojson)).

**`scripts/build-population-grid.js`** — builds `data/population-grid.json` from GHS-POP GeoTIFF tiles. Tiles are not in git; see `data/ghspop-tiles/download.sh`.

**`scripts/extract-turkey-border.js`** — extracts `static/turkey-border.json` from Natural Earth data. Turkey is excluded from the European totals for political reasons; the border is drawn as a visual boundary on the map.

**`scripts/seed-indoor-temps.js`** — populates the `indoor_temps` table with 100 synthetic readings. Useful for local development to see the IndoorTemps component with data.

---

## Deployment

The project ships as two Docker containers defined in `docker-compose.yml`:

- **`app`** — the SvelteKit server (Node.js)
- **`fetcher`** — a Python container that stays alive so Coolify's scheduled task runner can `exec` into it to run `fetch-dwd.py` hourly

Both share a persistent volume (`heat-data`) mounted at `/app/data` — this is where the SQLite database, pre-generated GeoJSON/JSON, and the climatology file live.

```bash
docker compose up --build
```

### First deployment checklist

1. Start the stack and let it run once so the volume and DB are created.
2. Copy the climatology file to the server volume (without it, the anomaly headlines show no data):
   ```bash
   # Build locally first if you haven't:
   python scripts/build-climatology.py --input data/eobs-tg-0.1deg-v30.nc

   # Copy to server (adjust path to your volume):
   rsync -az data/climatology-1961-1990.npz user@server:/path/to/volume/
   ```
3. Trigger the cron or wait for the next scheduled run. Check logs for:
   - ✅ `Climatology loaded: climatology-1961-1990.npz`
   - ✅ `Complete. 73 snapshots written.`

### Cron configuration (Coolify)

Set the scheduled task to run every hour:

```
docker exec <fetcher-container> python /app/scripts/fetch-dwd.py
```

---

## Methodology

The interactive methodology page at `/methodology` explains the indicator definitions, data sources, and known limitations in plain language.

Key choices documented there:
- **"Affected" definition** — a person is counted if the peak temperature in their grid cell reaches the threshold at any point during the day (daily maximum, not daily mean).
- **Apparent temperature** — Steadman (1994), the same formula used by Open-Meteo. Accounts for humidity and wind but not solar radiation.
- **Anomaly** — today's daily mean temperature minus the per-cell 1961–1990 daily mean for the same calendar day, smoothed over ±15 days.

---

## Project structure

```
scripts/          Python data pipeline + one-time setup scripts
src/
  lib/
    components/   Svelte UI components
    server/       DB access layer (weather.ts, db.ts, fileCache.ts)
    stores/       Svelte stores + shared types
    utils/        Colour scales, formatters
  routes/
    api/          JSON API endpoints
    [country]/    Country detail page
    methodology/  Methodology explanation page
data/             Runtime data (see data/README.md)
static/           Fonts, favicon, turkey border GeoJSON
```

---

## Contributing

Issues and pull requests are welcome. For large changes, please open an issue first to discuss the approach.

The codebase uses:
- **SvelteKit** with TypeScript
- **MapLibre GL** for the map
- **Tailwind CSS** for styles
- **better-sqlite3** for synchronous DB access on the server
- **cfgrib / xarray** for GRIB2 parsing in Python

---

## License

[MIT](LICENSE) — data from DWD is open data under [GeoNutzV](https://www.dwd.de/EN/service/copyright/copyright_node.html). E-OBS climatology data is from [ECA&D](https://www.ecad.eu), for non-commercial use. Population data from [GHS-POP (JRC)](https://ghsl.jrc.ec.europa.eu), CC BY 4.0.
