#!/usr/bin/env python3
"""
Tag every population-grid cell with the NUTS region it falls in.

The population grid (data/population-grid.json) is a fixed, ordered list of
~175k cells. NUTS boundaries (data/nuts.geojson, from Eurostat GISCO) are stable
per release. So the cell -> region mapping is computed once here and saved to
data/nuts-by-cell.json as an array aligned to the population grid order. The
data-fetch pipeline (scripts/fetch-dwd.py) loads that file and stamps the region
name onto each grid feature in the GeoJSON the frontend consumes.

We capture three NUTS levels per cell:
  a -> NUTS-1 name (broad; ~"federal state" for DE)
  b -> NUTS-2 name (region)
  c -> NUTS-3 name (granular; province / Landkreis)

Cells outside any NUTS polygon (open sea, non-NUTS countries) get null and the
frontend falls back to the country name alone.

Run once after changing the population grid or the NUTS release:
    python scripts/build-nuts-grid.py
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
from shapely import STRtree
from shapely.geometry import Point, shape

ROOT = Path(__file__).resolve().parent.parent
POP_GRID = ROOT / "data" / "population-grid.json"
NUTS_GEOJSON = ROOT / "data" / "nuts.geojson"
OUTPUT = ROOT / "data" / "nuts-by-cell.json"


# NUTS uses a few country codes that differ from the ISO-2 codes the
# population grid is tagged with. Normalise so the country guard matches.
NUTS_TO_ISO = {"UK": "GB", "EL": "GR"}


def load_level(features, level):
    """Build (geometries, names, country_codes) for one NUTS level."""
    geoms, names, cntrs = [], [], []
    for f in features:
        props = f.get("properties", {})
        if props.get("LEVL_CODE") != level:
            continue
        name = props.get("NAME_LATN") or props.get("NAME_ENGL") or props.get("NUTS_ID")
        cntr = props.get("CNTR_CODE") or (props.get("NUTS_ID") or "")[:2]
        cntr = NUTS_TO_ISO.get(cntr, cntr)
        try:
            geoms.append(shape(f["geometry"]))
            names.append(name)
            cntrs.append(cntr)
        except Exception:
            continue
    return geoms, names, cntrs


def tag_points(points, cell_countries, geoms, names, cntrs):
    """Return a list (len == points) of the name of the first geom intersecting
    each point, or None. A match is only accepted when the polygon's country
    equals the cell's country — this drops border-bleed where a coarse boundary
    pulls a cell into a neighbouring country's region. Uses a vectorised
    STRtree query."""
    result = [None] * len(points)
    if not geoms:
        return result
    tree = STRtree(geoms)
    # query returns pairs (input_index, tree_geom_index) for the predicate
    input_idx, tree_idx = tree.query(points, predicate="intersects")
    for pi, gi in zip(input_idx, tree_idx):
        if result[pi] is not None:
            continue
        if cntrs[gi] == cell_countries[pi]:
            result[pi] = names[gi]
    return result


def main():
    if not NUTS_GEOJSON.exists():
        sys.exit(
            f"Missing {NUTS_GEOJSON}. Download it first, e.g.:\n"
            "  curl -o data/nuts.geojson "
            "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/"
            "NUTS_RG_03M_2021_4326.geojson"
        )

    print(f"Loading population grid {POP_GRID.name} ...")
    cells = json.loads(POP_GRID.read_text())
    points = np.array([Point(c["lon"], c["lat"]) for c in cells], dtype=object)
    cell_countries = [c["country"] for c in cells]
    print(f"  {len(points):,} cells")

    print(f"Loading NUTS boundaries {NUTS_GEOJSON.name} ...")
    nuts = json.loads(NUTS_GEOJSON.read_text())["features"]

    out = [None] * len(points)
    for key, level in (("a", 1), ("b", 2), ("c", 3)):
        t0 = time.time()
        geoms, names, cntrs = load_level(nuts, level)
        tagged = tag_points(points, cell_countries, geoms, names, cntrs)
        matched = 0
        for i, name in enumerate(tagged):
            if name is None:
                continue
            matched += 1
            if out[i] is None:
                out[i] = {}
            out[i][key] = name
        print(
            f"  NUTS-{level}: {len(geoms):,} polygons, "
            f"{matched:,} cells tagged ({time.time()-t0:.1f}s)"
        )

    n_tagged = sum(1 for x in out if x)
    OUTPUT.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    size_kb = OUTPUT.stat().st_size // 1024
    print(
        f"Wrote {OUTPUT.name}: {n_tagged:,}/{len(out):,} cells tagged, "
        f"{size_kb:,} KB"
    )


if __name__ == "__main__":
    main()
