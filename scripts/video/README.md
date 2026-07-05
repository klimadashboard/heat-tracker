# Heatwave video generation

Renders timelapse videos of the heat tracker's data directly from the
**production API** (`https://heat-tracker.eu`) — no database access, SSH, or
local `data/heat-tracker.db` required. A plain internet connection is enough.

Rendering uses Pillow, not a browser — there's no MapLibre/Chromium/WebGL
anywhere in this pipeline, so it's cheap on CPU/RAM/disk and safe to run
alongside other work.

## Scripts

- **`render_video.py`** — map-style videos matching the live app's design
  (dark basemap, country outlines, MapLibre colour scale, corner overlays
  like the image export: branding, date/time, headline metric, legend).
  Supports hourly or `--daily` (one frame per day, using the same
  daily-max/mean-anomaly definition the app uses for "today/yesterday").
- **`render_abstract.py`** — abstract vertical (1080×1920, Instagram Story)
  background loops: just a full-density field of population-grid dots in
  plain colour, no basemap/text/legend, continuously crossfading through the
  hourly sequence.

Both render two variants — `temperature` (absolute) and `difference` (vs.
the 1961–1990 average) — from the same fetched data per hour.

## Requirements

```bash
pip install requests Pillow fonttools brotli   # fonttools+brotli only needed
                                                # once, to convert Barlow
                                                # from woff2 to TTF
brew install ffmpeg   # macOS — or `apt install ffmpeg` on Debian/Ubuntu
```

Both scripts check for these on startup and exit with an install command if
anything's missing, rather than failing partway through a long render.

### Optional: country borders for `render_video.py`

The basemap's country outlines are drawn from `data/nuts.geojson`, a ~17MB
Eurostat file that's `.gitignore`d (see the root `README.md`). Without it,
`render_video.py` still works — it just skips the borders and renders on a
plain dark background (the dots alone still trace Europe's coastline
reasonably well). To get borders:

```bash
curl -o data/nuts.geojson \
  'https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/NUTS_RG_03M_2021_4326.geojson'
```

## Usage

```bash
# Map videos, hourly (default: 2026-06-23 -> 2026-07-01, whenever hourly
# data starts, through the requested end date)
python3 scripts/video/render_video.py

# One frame per day instead (can start from any date — no hourly-data floor)
python3 scripts/video/render_video.py --daily --start 2026-06-22 --end 2026-07-01

# Abstract vertical background loop
python3 scripts/video/render_abstract.py

# Quick smoke test before a long run — renders just a few frames, keeps them
# on disk instead of deleting after encode, for a fast visual sanity check
python3 scripts/video/render_video.py --limit 3 --keep-frames
```

Run with `--help` on either script for the full flag list (date range,
views, fps, resolution, fade timing, sampling, etc).

## Notes

- Outputs land in `scripts/video/output/`. Intermediate PNG frames are
  written to `scripts/video/frames/` (or `frames_abstract/`) and deleted
  after a successful encode unless `--keep-frames` is passed.
- `scripts/video/.cache/` holds the pre-rendered basemap PNG so repeated
  runs at the same resolution skip rebuilding it.
- A failed/timed-out fetch for a given hour is retried a few times, then
  skipped (logged clearly) rather than aborting the whole run — a handful of
  dropped hours just means the video jumps over them.

## Be considerate of the production server

A full run makes ~200+ requests to the custom date-range grid/current
endpoints, which — unlike the app's normal "today" view — aren't served from
a cached file; each one runs a live DB query. Both scripts wait at least
`--delay` seconds (default `0.5`) between requests as a courtesy default.
**Please don't run multiple instances of these scripts against production in
parallel, or set `--delay` to 0** — the API also enforces its own per-IP
throttle on these endpoints (`src/lib/server/rateLimit.ts`, ~3 req/s), so an
aggressive client will just get `429`s rather than actually overloading
anything, but it's still better not to lean on it.
