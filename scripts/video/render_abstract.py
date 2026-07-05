#!/usr/bin/env python3
"""
Abstract vertical (Instagram Story, 1080x1920) background loop: a subsampled
field of population-grid dots in plain colour values — no basemap, no
borders, no text/legend/branding, no threshold/population opacity
highlighting. Crossfades continuously through the same hourly production
data as render_video.py's map videos.

Usage:
    python3 scripts/video/render_abstract.py
    python3 scripts/video/render_abstract.py --limit 4 --keep-frames   # quick test
"""
import argparse
import gc
import random
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_video as rv  # noqa: E402  (color stops, fetch_hour, Projector, encode_video)

VIDEO_DIR = Path(__file__).resolve().parent
FRAMES_DIR = VIDEO_DIR / "frames_abstract"
OUTPUT_DIR = VIDEO_DIR / "output"

W, H = 1080, 1920  # Instagram Story

# A landlocked core of Central Europe (Germany/Austria/Switzerland/Czechia/
# west Poland) — almost no coastline, so a portrait cover-crop fills edge to
# edge with populated land instead of showing empty sea/Scandinavia gaps.
# Deliberately zoomed in past the "whole continent" framing; not meant to
# read as a literal map.
ABSTRACT_BBOX = (5.0, 45.5, 17.0, 54.0)

BG_COLOR = (8, 8, 14)
DOT_ALPHA = 235  # constant for every dot — "plain colour values", no opacity ramp

# The population grid is a regular lat/lon lattice — a fixed stride ("every
# Nth feature") aliases into a visible moiré/wave pattern. A seeded random
# subset avoids that, and the seed keeps the SAME cells picked every hour so
# the animation doesn't flicker between different dots frame to frame.
_INDEX_CACHE = {}


def sample_indices(n, keep_every):
    if keep_every <= 1:
        return range(n)
    key = (n, keep_every)
    if key not in _INDEX_CACHE:
        rng = random.Random(20260622)
        _INDEX_CACHE[key] = sorted(rng.sample(range(n), n // keep_every))
    return _INDEX_CACHE[key]


def radius_for_abstract(pop):
    if pop < 5000:
        return 3.4
    if pop < 50000:
        return 4.4
    if pop < 300000:
        return 5.6
    if pop < 1500000:
        return 7.0
    return 8.5


def render_dots(proj, grid, view, sample_every):
    img = Image.new("RGBA", (W, H), (*BG_COLOR, 255))
    draw = ImageDraw.Draw(img, "RGBA")
    interp = rv.INTERP[view]
    prop_key = "anomalyC" if view == "difference" else "temperature"

    features = grid["features"]
    indices = sample_indices(len(features), sample_every)

    for i in indices:
        feat = features[i]
        props = feat["properties"]
        val = props.get(prop_key)
        if val is None:
            continue  # plain colour values only — no grey "null" dots
        pop = props.get("population") or 0
        lon, lat = feat["geometry"]["coordinates"]
        x, y = proj.project(lon, lat)
        if x < -8 or x > W + 8 or y < -8 or y > H + 8:
            continue
        color = interp(val)
        r = radius_for_abstract(pop)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(color[0], color[1], color[2], DOT_ALPHA))
    return img


def main():
    rv.check_ffmpeg()
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=rv.DEFAULT_START)
    ap.add_argument("--end", default=rv.DEFAULT_END)
    ap.add_argument("--views", default="temperature,difference")
    ap.add_argument("--sample-every", type=int, default=1, help="keep 1 in N grid points (1 = every populated cell, no sampling)")
    ap.add_argument("--fade-steps", type=int, default=8, help="interpolated frames per hour-to-hour transition")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--limit", type=int, default=None, help="only render the first N hours (for testing)")
    ap.add_argument("--keep-frames", action="store_true")
    ap.add_argument("--delay", type=float, default=rv.REQUEST_DELAY, help="min seconds between API requests (be considerate of the production server)")
    args = ap.parse_args()
    rv.REQUEST_DELAY = args.delay

    views = [v.strip() for v in args.views.split(",") if v.strip()]
    for v in views:
        if v not in rv.VIEW_META:
            sys.exit(f"unknown view: {v}")

    hours = rv.hour_range(args.start, args.end)
    if args.limit:
        hours = hours[: args.limit]
    print(f"{len(hours)} hours: {hours[0]} .. {hours[-1]}", file=sys.stderr)

    proj = rv.Projector(ABSTRACT_BBOX, W, H, fit="cover")

    for v in views:
        (FRAMES_DIR / v).mkdir(parents=True, exist_ok=True)

    prev_frame = {v: None for v in views}
    frame_idx = {v: 0 for v in views}

    failed = []
    t_start = time.time()
    for idx, at_iso in enumerate(hours):
        try:
            grid, _current = rv.fetch_hour(at_iso)
        except Exception as e:  # noqa: BLE001
            print(f"[{idx+1}/{len(hours)}] SKIPPING {at_iso}: {e}", file=sys.stderr)
            failed.append(at_iso)
            continue

        for v in views:
            cur = render_dots(proj, grid, v, args.sample_every)
            prev = prev_frame[v]
            if prev is not None:
                for k in range(1, args.fade_steps + 1):
                    t = k / args.fade_steps
                    Image.blend(prev, cur, t).convert("RGB").save(
                        FRAMES_DIR / v / f"frame_{frame_idx[v]:04d}.png"
                    )
                    frame_idx[v] += 1
            else:
                cur.convert("RGB").save(FRAMES_DIR / v / f"frame_{frame_idx[v]:04d}.png")
                frame_idx[v] += 1
            prev_frame[v] = cur

        del grid
        if idx % 20 == 0:
            gc.collect()
        elapsed = time.time() - t_start
        print(f"[{idx+1}/{len(hours)}] {at_iso} done ({elapsed:.0f}s elapsed)", file=sys.stderr)

    # Final hold frame so the last transition doesn't cut off mid-fade.
    for v in views:
        if prev_frame[v] is not None:
            prev_frame[v].convert("RGB").save(FRAMES_DIR / v / f"frame_{frame_idx[v]:04d}.png")
            frame_idx[v] += 1

    if failed:
        print(f"\n{len(failed)} hours failed and were skipped:", file=sys.stderr)
        for h in failed:
            print(f"  - {h}", file=sys.stderr)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_tag = f"{args.start[:10]}_to_{args.end[:10]}"
    for v in views:
        n = frame_idx[v]
        out_path = OUTPUT_DIR / f"heatwave-abstract-{v}-{date_tag}.mp4"
        print(f"Encoding {out_path} ({n} frames, {n / args.fps:.1f}s)...", file=sys.stderr)
        rv.encode_video(FRAMES_DIR / v, out_path, args.fps)
        print(f"  -> {out_path}", file=sys.stderr)
        if not args.keep_frames:
            for p in (FRAMES_DIR / v).glob("*.png"):
                p.unlink()

    print("done.", file=sys.stderr)


if __name__ == "__main__":
    main()
