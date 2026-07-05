#!/usr/bin/env python3
"""
Renders hourly heatwave timelapse videos (absolute temperature + difference
from average) directly from the production Heat Tracker API, using Pillow
instead of a browser — no MapLibre/Chromium/WebGL involved, so this is light
on CPU/RAM/disk and safe to run on a loaded machine.

Colour stops are ported 1:1 from src/lib/utils/scales.ts so the palette
matches the live web app. The basemap (dark background + country outlines,
Web-Mercator projected to the same [[-10,30],[40,60]] bbox the app's image
export uses) is built once from data/nuts.geojson and cached to disk.

Usage:
    python3 scripts/video/render_video.py --start 2026-06-23T00:00:00.000Z \\
        --end 2026-07-01T23:00:00.000Z --views temperature,difference

    # quick smoke test with just a handful of hours:
    python3 scripts/video/render_video.py --limit 3
"""
import argparse
import gc
import json
import math
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

try:
    import requests
    from PIL import Image, ImageDraw, ImageFont
except ImportError as e:
    sys.exit(
        f"Missing dependency: {e}\n"
        "Install the video-rendering requirements with:\n"
        "  pip install requests Pillow fonttools brotli"
    )


def check_ffmpeg():
    if shutil.which("ffmpeg") is None:
        sys.exit(
            "ffmpeg not found on PATH — it's needed to assemble frames into an mp4.\n"
            "Install it with:\n"
            "  brew install ffmpeg        # macOS\n"
            "  apt install ffmpeg         # Debian/Ubuntu"
        )

ROOT = Path(__file__).resolve().parents[2]
VIDEO_DIR = Path(__file__).resolve().parent
CACHE_DIR = VIDEO_DIR / ".cache"
FRAMES_DIR = VIDEO_DIR / "frames"
OUTPUT_DIR = VIDEO_DIR / "output"
FONT_DIR = VIDEO_DIR / "fonts"
NUTS_PATH = ROOT / "data" / "nuts.geojson"
NUTS_URL = "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/NUTS_RG_03M_2021_4326.geojson"

# Barlow ships in the repo only as woff/woff2 (see src/app.css); Pillow needs
# TTF/OTF, so we convert once and cache alongside this script. Source files
# are committed to git, so this works on a fresh clone with no manual step.
FONT_SOURCES = {
    "barlow-regular.ttf": ROOT / "static" / "fonts" / "barlow-v12-latin-regular.woff2",
    "barlow-600.ttf": ROOT / "static" / "fonts" / "barlow-v12-latin-600.woff2",
}

PROD = "https://heat-tracker.eu"

DEFAULT_START = "2026-06-23T00:00:00.000Z"  # first hour with true hourly (not 3-hourly) analysis data
DEFAULT_END = "2026-07-01T23:00:00.000Z"  # Wednesday this week, last hour

# Daily mode aggregates a whole day per frame (same daily-max/mean-anomaly
# definition the app itself uses for "yesterday/today"), so it isn't limited
# to the hourly-data cutoff — it can start from Monday as originally asked.
DEFAULT_START_DAILY = "2026-06-22"  # Monday last week
DEFAULT_END_DAILY = "2026-07-01"  # Wednesday this week

# Same bbox DownloadButton.svelte fits the map to for the Europe-wide image export.
BBOX = (-10.0, 30.0, 40.0, 60.0)  # lon_min, lat_min, lon_max, lat_max

BG_COLOR = (8, 8, 14)
LAND_COLOR = (20, 20, 29)
BORDER_COLOR = (150, 160, 180, 70)

# --- Colour stops, ported from src/lib/utils/scales.ts ---------------------
ANOMALY_STOPS = [
    (-12, "#313695"), (-8, "#4575b4"), (-4, "#74add1"), (-1.5, "#abd9e9"),
    (0, "#ececf2"), (1.5, "#fee090"), (4, "#fdae61"), (8, "#f46d43"), (12, "#a50026"),
]
TEMP_STOPS = [
    (-15, "#1e4d9e"), (-5, "#3d80c0"), (0, "#5aaed8"), (5, "#7ec8e3"), (10, "#98d4a0"),
    (15, "#b5d86c"), (20, "#dcc43c"), (25, "#f0a020"), (30, "#e87020"), (35, "#d43d1a"),
    (40, "#b01515"), (45, "#7a0000"),
]
NULL_COLOR = (80, 80, 96, 115)

VIEW_META = {
    "temperature": {"label": "Temperature", "stops": TEMP_STOPS, "range": (0, 22, 45)},
    "difference": {"label": "Difference from average", "stops": ANOMALY_STOPS, "range": (-8, 0, 8)},
}


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def make_interp(stops):
    rgb_stops = [(v, hex_to_rgb(c)) for v, c in stops]

    def interp(v):
        if v <= rgb_stops[0][0]:
            return rgb_stops[0][1]
        if v >= rgb_stops[-1][0]:
            return rgb_stops[-1][1]
        for (v0, c0), (v1, c1) in zip(rgb_stops, rgb_stops[1:]):
            if v0 <= v <= v1:
                t = (v - v0) / (v1 - v0)
                return tuple(round(c0[j] + (c1[j] - c0[j]) * t) for j in range(3))
        return rgb_stops[-1][1]

    return interp


INTERP = {name: make_interp(meta["stops"]) for name, meta in VIEW_META.items()}


# --- Web-Mercator projection, fit-to-bbox (no distortion, letterboxed) -----
class Projector:
    def __init__(self, bbox, width, height, fit="contain"):
        """fit='contain' (default) letterboxes so the whole bbox is visible,
        matching MapLibre's fitBounds. fit='cover' scales up and crops
        instead, so the bbox fills every pixel with no empty margins —
        for full-bleed abstract backgrounds where letterboxing would show."""
        lon_min, lat_min, lon_max, lat_max = bbox
        self.x0 = math.radians(lon_min)
        self.x1 = math.radians(lon_max)
        self.y0 = self._merc_y(lat_min)
        self.y1 = self._merc_y(lat_max)
        width_merc = self.x1 - self.x0
        height_merc = self.y1 - self.y0
        fn = max if fit == "cover" else min
        self.scale = fn(width / width_merc, height / height_merc)
        self.offset_x = (width - width_merc * self.scale) / 2
        self.offset_y = (height - height_merc * self.scale) / 2

    @staticmethod
    def _merc_y(lat_deg):
        lat = math.radians(max(min(lat_deg, 85.05), -85.05))
        return math.log(math.tan(math.pi / 4 + lat / 2))

    def project(self, lon, lat):
        mx = math.radians(lon)
        my = self._merc_y(lat)
        x = self.offset_x + (mx - self.x0) * self.scale
        y = self.offset_y + (self.y1 - my) * self.scale
        return x, y


def iter_polygons(geometry):
    """Yield exterior rings (list of [lon,lat]) for Polygon/MultiPolygon geometries."""
    if geometry["type"] == "Polygon":
        yield geometry["coordinates"][0]
    elif geometry["type"] == "MultiPolygon":
        for poly in geometry["coordinates"]:
            yield poly[0]


def build_basemap(width, height):
    cache_path = CACHE_DIR / f"basemap_{width}x{height}.png"
    if cache_path.exists():
        return Image.open(cache_path).convert("RGB")

    print(f"Building basemap ({width}x{height})...", file=sys.stderr)
    proj = Projector(BBOX, width, height)
    img = Image.new("RGB", (width, height), BG_COLOR)
    draw = ImageDraw.Draw(img, "RGBA")

    if not NUTS_PATH.exists():
        print(
            f"NOTE: {NUTS_PATH} not found — rendering with a plain background, no country "
            f"outlines. This file isn't committed to the repo (~17MB); to get borders, "
            f"download it from Eurostat GISCO and save it there:\n  curl -o {NUTS_PATH} '{NUTS_URL}'",
            file=sys.stderr,
        )
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        img.save(cache_path)
        return img

    with open(NUTS_PATH) as f:
        nuts = json.load(f)
    # Turkey is excluded from the grid_data queries (see weather.ts) for the
    # same political reasons noted in the root README — drop it from the
    # basemap too so the outline matches what the dots actually cover.
    countries = [
        f
        for f in nuts["features"]
        if f["properties"].get("LEVL_CODE") == 0 and f["properties"].get("CNTR_CODE") != "TR"
    ]

    for feature in countries:
        for ring in iter_polygons(feature["geometry"]):
            pts = [proj.project(lon, lat) for lon, lat in ring]
            if len(pts) < 3:
                continue
            draw.polygon(pts, fill=LAND_COLOR)

    for feature in countries:
        for ring in iter_polygons(feature["geometry"]):
            pts = [proj.project(lon, lat) for lon, lat in ring]
            if len(pts) < 2:
                continue
            draw.line(pts + [pts[0]], fill=BORDER_COLOR, width=1)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    img.save(cache_path)
    return img


# --- Data fetching (streams straight to Python objects, one hour at a time) -
# Minimum gap between requests to the production API — a courtesy default so
# a naive loop (or several people running this at once) doesn't hammer a
# live site that has no server-side caching for these custom-range queries.
# Override with --delay on either script.
REQUEST_DELAY = 0.5
_last_request_at = 0.0


def _throttle():
    global _last_request_at
    wait = REQUEST_DELAY - (time.time() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.time()


def fetch_json(url, timeout=120, retries=3):
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            _throttle()
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last_err = e
            wait = 2 * attempt
            print(f"  fetch failed ({e}), retrying in {wait}s...", file=sys.stderr)
            time.sleep(wait)
    raise last_err


def fetch_range(from_iso, to_iso):
    q = f"from={quote(from_iso)}&to={quote(to_iso)}"
    grid = fetch_json(f"{PROD}/api/grid?{q}&indicator=temperature")
    current = fetch_json(f"{PROD}/api/current?{q}&indicator=temperature&threshold=30")
    return grid, current


def fetch_hour(at_iso):
    return fetch_range(at_iso, at_iso)


def fetch_day(day_iso):
    """Daily aggregate — same [00:00, 23:59:59] range + MAX(temperature) /
    AVG(anomaly_c) definition the app itself uses for its yesterday/today/
    tomorrow views (see getGridDataForRange in weather.ts)."""
    from_iso = f"{day_iso}T00:00:00.000Z"
    to_iso = f"{day_iso}T23:59:59.000Z"
    return fetch_range(from_iso, to_iso)


def hour_range(start_iso, end_iso):
    start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    hours = []
    cur = start
    while cur <= end:
        hours.append(cur.strftime("%Y-%m-%dT%H:%M:%S.000Z"))
        cur += timedelta(hours=1)
    return hours


def day_range(start_day, end_day):
    start = datetime.fromisoformat(start_day).date()
    end = datetime.fromisoformat(end_day).date()
    days = []
    cur = start
    while cur <= end:
        days.append(cur.isoformat())
        cur += timedelta(days=1)
    return days


# --- Per-point styling, approximating HeatMap.svelte's paint expressions ---
def pop_ramp(pop):
    stops = [(100, 0.0), (10000, 0.3), (100000, 0.6), (1000000, 0.85), (5000000, 1.0)]
    if pop <= stops[0][0]:
        return 0.0
    if pop >= stops[-1][0]:
        return 1.0
    for (p0, t0), (p1, t1) in zip(stops, stops[1:]):
        if p0 <= pop <= p1:
            return t0 + (t1 - t0) * (pop - p0) / (p1 - p0)
    return 1.0


def alpha_for(view, pop, value, threshold=30):
    ramp = pop_ramp(pop)
    if view == "difference":
        return 0.5 + (0.95 - 0.5) * ramp
    affected = value is not None and value >= threshold
    return 0.92 if affected else 0.5 + (0.95 - 0.5) * ramp


def radius_for(pop):
    if pop < 1000:
        return 0.55
    if pop < 10000:
        return 0.75
    if pop < 100000:
        return 1.05
    if pop < 1000000:
        return 1.5
    if pop < 5000000:
        return 2.0
    return 2.5


# --- Text overlay ------------------------------------------------------------
_FONT_CACHE = {}
_fonts_ready = False


def ensure_fonts():
    """Convert the repo's committed woff2 Barlow files to TTF on first run
    (Pillow can't load woff2 directly). Idempotent — skips files that already
    exist, so this is cheap on every subsequent run."""
    global _fonts_ready
    if _fonts_ready:
        return
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    missing = [dst for dst in FONT_SOURCES if not (FONT_DIR / dst).exists()]
    if missing:
        try:
            from fontTools.ttLib import TTFont
        except ImportError:
            sys.exit(
                "Missing dependency 'fonttools' (with brotli support), needed once to convert "
                "Barlow from woff2 to TTF for Pillow. Install with:\n"
                "  pip install fonttools brotli"
            )
        for dst_name in missing:
            src = FONT_SOURCES[dst_name]
            if not src.exists():
                sys.exit(
                    f"Missing font source {src}\n"
                    "This should be committed to the repo (see src/app.css) — check your checkout."
                )
            f = TTFont(str(src))
            f.flavor = None
            f.save(str(FONT_DIR / dst_name))
            print(f"Converted {src.name} -> {FONT_DIR / dst_name}", file=sys.stderr)
    _fonts_ready = True


def font(weight, size):
    ensure_fonts()
    key = (weight, size)
    if key not in _FONT_CACHE:
        name = "barlow-600.ttf" if weight == "bold" else "barlow-regular.ttf"
        _FONT_CACHE[key] = ImageFont.truetype(str(FONT_DIR / name), size)
    return _FONT_CACHE[key]


def text_w(draw, text, f):
    return draw.textlength(text, font=f)


def millions(n):
    if n is None:
        return "–"
    if n >= 1_000_000:
        m = n / 1_000_000
        return f"~{m:.0f} million" if m >= 10 else f"~{m:.1f} million"
    if n >= 1000:
        return f"~{n / 1000:.0f},000"
    return f"~{n:,}"


def fmt_date_label(at_iso, daily=False):
    d = datetime.fromisoformat(at_iso.replace("Z", "+00:00"))
    day = d.strftime("%a %d %b")
    hm = "" if daily else d.strftime("%H:%M") + " UTC"
    return day, hm


def draw_legend(img_draw, W, H, view):
    meta = VIEW_META[view]
    box_w, box_h = 190, 78
    x0, y0 = W - box_w - 16, H - box_h - 16
    img_draw.rounded_rectangle(
        [x0, y0, x0 + box_w, y0 + box_h], radius=10, fill=(8, 8, 16, 230), outline=(255, 255, 255, 25)
    )
    pad = 12
    img_draw.text((x0 + pad, y0 + 8), meta["label"].upper(), font=font("bold", 10), fill=(230, 230, 235, 220))

    bar_x, bar_y, bar_w, bar_h = x0 + pad, y0 + 28, box_w - pad * 2, 6
    interp = INTERP[view]
    lo, mid, hi = meta["range"]
    span = hi - lo
    for i in range(bar_w):
        v = lo + span * (i / bar_w)
        c = interp(v)
        img_draw.line([(bar_x + i, bar_y), (bar_x + i, bar_y + bar_h)], fill=c)

    labels = [f"{lo}°C", f"{mid}°" if view == "difference" else f"{mid}°C", f"+{hi}°C" if view == "difference" else f"{hi}°C+"]
    lf = font("regular", 9)
    img_draw.text((bar_x, bar_y + 11), labels[0], font=lf, fill=(180, 180, 190, 210))
    mid_w = text_w(img_draw, labels[1], lf)
    img_draw.text((bar_x + bar_w / 2 - mid_w / 2, bar_y + 11), labels[1], font=lf, fill=(180, 180, 190, 210))
    right_w = text_w(img_draw, labels[2], lf)
    img_draw.text((bar_x + bar_w - right_w, bar_y + 11), labels[2], font=lf, fill=(180, 180, 190, 210))

    img_draw.text((x0 + pad, y0 + box_h - 20), "Dot size = residents", font=font("regular", 8), fill=(160, 160, 172, 190))


def draw_overlay(img, view, at_iso, headline, daily=False):
    W, H = img.size
    draw = ImageDraw.Draw(img, "RGBA")
    PAD = 20

    # Top-left pill.
    pill_text = "heat-tracker.eu"
    pf = font("bold", 15)
    tw = text_w(draw, pill_text, pf)
    pill_w, pill_h = tw + 28, 32
    draw.rounded_rectangle([PAD, PAD, PAD + pill_w, PAD + pill_h], radius=pill_h / 2, fill=(255, 255, 255, 255))
    draw.text((PAD + 14, PAD + 8), pill_text, font=pf, fill=(10, 10, 18, 255))

    # Top-right: view label + date(/time). A soft scrim behind the text keeps
    # it legible over busy map areas (dot antialiasing otherwise bleeds into
    # thin glyph strokes at these small sizes). Daily mode has no time line,
    # so the scrim is shorter.
    scrim_h = 44 if daily else 68
    draw.rounded_rectangle([W - 216 - PAD, PAD - 8, W - PAD + 8, PAD + scrim_h], radius=10, fill=(6, 6, 12, 130))

    view_label = VIEW_META[view]["label"].upper()
    vf = font("bold", 10)
    vw = text_w(draw, view_label, vf)
    draw.text((W - PAD - vw, PAD), view_label, font=vf, fill=(255, 255, 255, 150))

    day, hm = fmt_date_label(at_iso, daily=daily)
    df = font("bold", 24)
    dw = text_w(draw, day, df)
    draw.text((W - PAD - dw, PAD + 16), day, font=df, fill=(255, 255, 255, 255))

    if not daily:
        tf = font("regular", 14)
        tw2 = text_w(draw, hm, tf)
        draw.text((W - PAD - tw2, PAD + 44), hm, font=tf, fill=(255, 255, 255, 190))

    # Bottom-left: headline metric + source/branding, same scrim treatment.
    y = H - PAD - 92
    draw.rounded_rectangle([PAD - 10, y - 10, PAD + 320, H - PAD + 8], radius=10, fill=(6, 6, 12, 130))
    if view == "difference":
        anomaly = headline.get("meanAnomalyC")
        warmer = (anomaly or 0) >= 0
        color = (251, 146, 60, 255) if warmer else (56, 189, 248, 255)
        value = f"{'+' if anomaly is not None and anomaly >= 0 else ''}{anomaly:.1f}°C" if anomaly is not None else "–"
        label = f"{'warmer' if warmer else 'cooler'} than the 1961–1990 average"
    else:
        affected = headline.get("totalAffected")
        color = (248, 113, 113, 255)
        value = millions(affected)
        label = "people experiencing 30°C or more"

    draw.text((PAD, y), value, font=font("bold", 32), fill=color)
    draw.text((PAD, y + 38), label, font=font("regular", 13), fill=(212, 212, 216, 255))
    draw.text((PAD, y + 60), "DWD ICON-EU weather model", font=font("regular", 10), fill=(140, 140, 150, 255))
    draw.text((PAD, y + 76), "Klimadashboard.org", font=font("bold", 12), fill=(40, 168, 137, 255))

    draw_legend(draw, W, H, view)
    return img


# --- Frame rendering ---------------------------------------------------------
def render_frame(basemap, proj, grid, current, view, at_iso, daily=False):
    img = basemap.copy()
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    interp = INTERP[view]
    W, H = img.size

    prop_key = "anomalyC" if view == "difference" else "temperature"

    for feat in grid["features"]:
        props = feat["properties"]
        val = props.get(prop_key)
        pop = props.get("population") or 0
        lon, lat = feat["geometry"]["coordinates"]
        x, y = proj.project(lon, lat)
        if x < -4 or x > W + 4 or y < -4 or y > H + 4:
            continue
        if val is None:
            color, alpha = NULL_COLOR[:3], NULL_COLOR[3]
        else:
            color = interp(val)
            alpha = round(alpha_for(view, pop, val) * 255)
        r = radius_for(pop)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(color[0], color[1], color[2], alpha))

    composited = Image.alpha_composite(img.convert("RGBA"), overlay)
    headline = current.get("snapshot", {})
    draw_overlay(composited, view, at_iso, headline, daily=daily)
    return composited.convert("RGB")


def encode_video(frames_dir, out_path, fps):
    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / "frame_%04d.png"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)


def main():
    global REQUEST_DELAY
    check_ffmpeg()
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--daily", action="store_true", help="one frame per day (daily max/mean-anomaly) instead of hourly")
    ap.add_argument("--views", default="temperature,difference")
    ap.add_argument("--fps", type=float, default=None)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--limit", type=int, default=None, help="only render the first N frames (for testing)")
    ap.add_argument("--keep-frames", action="store_true")
    ap.add_argument("--rebuild-basemap", action="store_true")
    ap.add_argument("--delay", type=float, default=REQUEST_DELAY, help="min seconds between API requests (be considerate of the production server)")
    args = ap.parse_args()
    REQUEST_DELAY = args.delay

    views = [v.strip() for v in args.views.split(",") if v.strip()]
    for v in views:
        if v not in VIEW_META:
            sys.exit(f"unknown view: {v}")

    if args.rebuild_basemap:
        cache_path = CACHE_DIR / f"basemap_{args.width}x{args.height}.png"
        cache_path.unlink(missing_ok=True)

    if args.daily:
        start = args.start or DEFAULT_START_DAILY
        end = args.end or DEFAULT_END_DAILY
        frames = day_range(start, end)
        fetch = fetch_day
        fps = args.fps if args.fps is not None else 1.0
        label_tag = f"{start}_to_{end}-daily"
    else:
        start = args.start or DEFAULT_START
        end = args.end or DEFAULT_END
        frames = hour_range(start, end)
        fetch = fetch_hour
        fps = args.fps if args.fps is not None else 8.0
        label_tag = f"{start[:10]}_to_{end[:10]}"

    if args.limit:
        frames = frames[: args.limit]
    unit = "days" if args.daily else "hours"
    print(f"{len(frames)} {unit}: {frames[0]} .. {frames[-1]}", file=sys.stderr)

    basemap = build_basemap(args.width, args.height)
    proj = Projector(BBOX, args.width, args.height)

    for v in views:
        (FRAMES_DIR / v).mkdir(parents=True, exist_ok=True)

    failed = []
    t_start = time.time()
    for idx, key in enumerate(frames):
        try:
            grid, current = fetch(key)
        except Exception as e:  # noqa: BLE001
            print(f"[{idx+1}/{len(frames)}] SKIPPING {key}: {e}", file=sys.stderr)
            failed.append(key)
            continue

        # render_frame's text overlay wants a full ISO timestamp; daily keys
        # are plain dates ("2026-06-22"), which datetime.fromisoformat parses
        # fine as midnight.
        for v in views:
            frame = render_frame(basemap, proj, grid, current, v, key, daily=args.daily)
            frame.save(FRAMES_DIR / v / f"frame_{idx:04d}.png")

        del grid, current
        if idx % 20 == 0:
            gc.collect()

        elapsed = time.time() - t_start
        print(f"[{idx+1}/{len(frames)}] {key} done ({elapsed:.0f}s elapsed)", file=sys.stderr)

    if failed:
        print(f"\n{len(failed)} {unit} failed and were skipped (video will jump over them):", file=sys.stderr)
        for h in failed:
            print(f"  - {h}", file=sys.stderr)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for v in views:
        out_path = OUTPUT_DIR / f"heatwave-{v}-{label_tag}.mp4"
        print(f"Encoding {out_path}...", file=sys.stderr)
        encode_video(FRAMES_DIR / v, out_path, fps)
        print(f"  -> {out_path}", file=sys.stderr)
        if not args.keep_frames:
            for p in (FRAMES_DIR / v).glob("*.png"):
                p.unlink()

    print("done.", file=sys.stderr)


if __name__ == "__main__":
    main()
