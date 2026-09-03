#!/usr/bin/env python3
"""
One-time migration: normalize grid_data's per-row lat/lon/country/population
into a static grid_cells reference table, referenced by grid_data.cell_id.

This is the historic-data counterpart to the schema change in fetch-dwd.py /
db.ts — those already write new snapshots in the normalized format, but an
existing database still has old-format grid_data rows (lat/lon/country/
population repeated per row, per snapshot) until this script runs once.

SAFETY DESIGN — read carefully before running against production:

  * This script NEVER writes to the source database. It builds a brand new
    file (<db>.migrated) from scratch and only reads from the original.
    If it crashes, is interrupted, or produces something wrong, the original
    file is untouched — just delete the .migrated file and re-run.
  * It is resumable: if <db>.migrated already has some snapshots migrated
    (from a prior interrupted run), it skips them and continues.
  * It verifies row counts (old grid_data vs new grid_data) before declaring
    success. If any row fails to match a grid cell (shouldn't happen unless
    population-grid.json has changed since old rows were written), it
    reports the mismatch and refuses to declare success.
  * Swapping the migrated file into place is a SEPARATE, manual step (see
    "After migration" below) — this script does not touch the live file.

Usage:
    python scripts/migrate-grid-cells.py --db data/heat-tracker.db

    # Dry run: just report what would happen (row counts, estimated new size)
    python scripts/migrate-grid-cells.py --db data/heat-tracker.db --dry-run

After migration (do this during a maintenance window, on the server):
    1. Stop the fetcher container FIRST so no new snapshot can be written to
       the source db while you finish up (a large source db may take longer
       to migrate than one hourly cron interval, so a fetch could otherwise
       land between your last migration run and the swap and be missed).
    2. Re-run this script once more (now that the fetcher is stopped, this
       pass is authoritative — it picks up anything written since your last
       run, in seconds, since only the delta needs migrating).
    3. mv heat-tracker.db heat-tracker.db.pre-migration   # keep as a backup
    4. mv heat-tracker.db.migrated heat-tracker.db        # single file, no
       # -wal/-shm sidecars to move — this script leaves a clean checkpointed
       # file (journal_mode=DELETE) as its last step.
    5. Redeploy/restart the app + fetcher so they run the matching new code
       and open the freshly-swapped file. The app's already-open connection
       to the old file is unaffected by the mv until it's actually restarted
       (POSIX rename semantics), so reads stay served from the old file with
       zero disruption right up until that restart.
    6. Once you've confirmed the site looks correct and a fetch cycle has run
       cleanly, delete heat-tracker.db.pre-migration.
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
import time

NEW_SCHEMA_SQL = """
CREATE TABLE grid_cells (
    id INTEGER PRIMARY KEY,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    country TEXT NOT NULL,
    population INTEGER NOT NULL
);
CREATE UNIQUE INDEX idx_grid_cells_latlon ON grid_cells(lat, lon);
CREATE INDEX idx_grid_cells_country ON grid_cells(country);

CREATE TABLE grid_data (
    snapshot_id INTEGER NOT NULL,
    cell_id INTEGER NOT NULL,
    temperature REAL,
    apparent_temperature REAL,
    is_affected INTEGER NOT NULL DEFAULT 0,
    anomaly_c REAL,
    is_above_avg INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(id),
    FOREIGN KEY (cell_id) REFERENCES grid_cells(id)
);
"""
# grid_data's own indexes are deliberately NOT created up front. Maintaining a
# B-tree incrementally while inserting hundreds of millions of rows gets
# progressively slower as the index outgrows cache (each insert becomes a
# random-page disk read); building the index once, after all rows exist, is
# a single efficient bulk sort+build instead. grid_cells' indexes stay
# up-front — that table is tiny (~175k rows, seeded once) and its (lat, lon)
# index is what makes the migration join itself fast.
GRID_DATA_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_grid_snapshot ON grid_data(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_grid_cell ON grid_data(cell_id);
"""

def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--db', required=True, help='Path to the existing heat-tracker.db (read-only; never modified)')
    p.add_argument('--grid', default='data/population-grid.json', help='Population grid JSON (default: data/population-grid.json)')
    p.add_argument('--out', default=None, help='Output path (default: <db>.migrated)')
    p.add_argument('--dry-run', action='store_true', help='Report row counts and exit without writing anything')
    p.add_argument(
        '--batch-snapshots', type=int, default=25,
        help='Snapshots per INSERT...SELECT batch (default: 25). Each grid_data snapshot is '
             '~175k rows, so keep this small on large databases to bound peak WAL/temp disk use '
             'and get frequent progress/ETA logging.'
    )
    p.add_argument(
        '--vacuum', action='store_true',
        help='VACUUM the output file at the end. Off by default: VACUUM rebuilds the whole file '
             'into a temp copy first, needing scratch space roughly equal to the output file size '
             'on top of it — risky on a disk shared with other services. The freshly-built file is '
             'already unfragmented (built via INSERT, not ALTER/DELETE), so VACUUM only trims a '
             'little further; skip it unless you have confirmed headroom.'
    )
    p.add_argument('--verbose', action='store_true')
    return p.parse_args()


def old_schema_has_cell_id(conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(grid_data)")}
    return 'cell_id' in cols


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S',
    )

    if not os.path.exists(args.db):
        logging.error(f'Source database not found: {args.db}')
        sys.exit(1)

    out_path = args.out or (args.db + '.migrated')

    src = sqlite3.connect(f'file:{args.db}?mode=ro', uri=True)
    src.row_factory = sqlite3.Row

    if old_schema_has_cell_id(src):
        logging.info('Source database already has grid_data.cell_id — nothing to migrate.')
        src.close()
        return

    old_row_count = src.execute('SELECT COUNT(*) FROM grid_data').fetchone()[0]
    snapshot_count = src.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]
    logging.info(f'Source: {old_row_count:,} grid_data rows across {snapshot_count:,} snapshots')

    with open(args.grid) as f:
        pop_grid = json.load(f)
    logging.info(f'Population grid: {len(pop_grid):,} cells from {args.grid}')

    if args.dry_run:
        old_row_bytes = old_row_count * 68   # rough: 4 REALs/INTs + TEXT country, sqlite varint-packed
        new_row_bytes = old_row_count * 34   # cell_id (int) replaces lat/lon/country/population
        logging.info(f'Dry run only — estimated grid_data size: ~{old_row_bytes/1e9:.1f} GB -> ~{new_row_bytes/1e9:.1f} GB')
        src.close()
        return

    if os.path.exists(out_path):
        logging.info(f'{out_path} already exists — attempting to resume a prior run')

    dst = sqlite3.connect(out_path)
    dst.execute('PRAGMA journal_mode=WAL')
    dst.execute('PRAGMA synchronous=OFF')  # safe: on failure we just delete this file and restart

    existing_tables = {r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'grid_cells' not in existing_tables:
        dst.executescript(NEW_SCHEMA_SQL)
        dst.commit()

    # Copy schema-unchanged tables verbatim, once. Attached read-write (SQLite's
    # ATTACH doesn't reliably honor mode=ro URIs across platforms) but this
    # script only ever SELECTs from `old` — the source file is never written to.
    #
    # IMPORTANT: once `old` is attached, both databases have a same-named
    # grid_data table (old vs. new schema) plus we're about to copy tables
    # that also exist in `old` (snapshots, country_aggregates, ...). Every
    # reference to the destination file below MUST be qualified as `main.foo`
    # — an unqualified `foo` resolves via SQLite's attached-db search order
    # and can silently hit `old.foo` instead, which is exactly the bug this
    # comment is here to prevent from being reintroduced.
    dst.execute("ATTACH DATABASE ? AS old", (args.db,))
    for table in ('snapshots', 'country_aggregates', 'indoor_temps', 'forecast_log'):
        has_table = src.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not has_table:
            continue
        dst_has_table = dst.execute(
            "SELECT 1 FROM main.sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not dst_has_table:
            # IMPORTANT: `CREATE TABLE ... AS SELECT ... WHERE 0` does NOT carry
            # over the source's PRIMARY KEY/UNIQUE constraints — it infers a
            # plain table from the SELECT's result columns only. Reusing the
            # original CREATE TABLE statement instead is what makes the
            # INSERT OR IGNORE below an actual dedup rather than a silent
            # append-forever with nothing to conflict on.
            create_sql = src.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()[0]
            dst.execute(create_sql)
        before = dst.execute(f'SELECT COUNT(*) FROM main.{table}').fetchone()[0]
        # INSERT OR IGNORE (not a count>0-means-skip check): the source is a
        # live database that keeps growing between runs (e.g. the fetcher's
        # hourly cron), so re-running this must top up anything new rather
        # than silently leaving the destination stale at its first snapshot.
        # Each of these tables has a primary key / unique constraint, so
        # already-copied rows are cheaply no-ops here.
        dst.execute(f'INSERT OR IGNORE INTO main.{table} SELECT * FROM old.{table}')
        dst.commit()
        after = dst.execute(f'SELECT COUNT(*) FROM main.{table}').fetchone()[0]
        logging.info(f'{table}: {after:,} rows ({after - before:,} newly copied)')

    # Seed grid_cells (idempotent, cheap). old has no grid_cells table, so this
    # one is unambiguous, but we qualify it anyway for consistency.
    n_cells = dst.execute('SELECT COUNT(*) FROM main.grid_cells').fetchone()[0]
    if n_cells == 0:
        dst.executemany(
            'INSERT INTO main.grid_cells (id, lat, lon, country, population) VALUES (?, ?, ?, ?, ?)',
            [(i, c['lat'], c['lon'], c['country'], c['pop']) for i, c in enumerate(pop_grid)]
        )
        dst.commit()
        logging.info(f'grid_cells: seeded {len(pop_grid):,} cells')

    # Migrate grid_data in snapshot_id batches, joining old rows to grid_cells
    # by exact (lat, lon) — both derive from the same population-grid.json, so
    # this should match every row. We verify this below rather than assume it.
    already_migrated_snapshot_ids = {
        r[0] for r in dst.execute('SELECT DISTINCT snapshot_id FROM main.grid_data')
    }
    all_snapshot_ids = [r[0] for r in src.execute('SELECT id FROM snapshots ORDER BY id')]
    remaining = [sid for sid in all_snapshot_ids if sid not in already_migrated_snapshot_ids]
    logging.info(f'grid_data: {len(all_snapshot_ids) - len(remaining):,} snapshots already migrated, {len(remaining):,} remaining')

    if remaining:
        # Drop grid_data's own indexes (if a prior run got far enough to create
        # them) so the remaining rows load at bulk-insert speed, not
        # incremental-index-maintenance speed. Rebuilt once, after the loop.
        dst.execute('DROP INDEX IF EXISTS main.idx_grid_snapshot')
        dst.execute('DROP INDEX IF EXISTS main.idx_grid_cell')
        dst.commit()

    t0 = time.time()
    done = 0
    while remaining:
        batch, remaining = remaining[:args.batch_snapshots], remaining[args.batch_snapshots:]
        placeholders = ','.join('?' * len(batch))
        dst.execute(
            f'''INSERT INTO main.grid_data (snapshot_id, cell_id, temperature, apparent_temperature,
                                             is_affected, anomaly_c, is_above_avg)
                SELECT o.snapshot_id, gc.id, o.temperature, o.apparent_temperature,
                       o.is_affected, o.anomaly_c, o.is_above_avg
                FROM old.grid_data o
                JOIN main.grid_cells gc ON gc.lat = o.lat AND gc.lon = o.lon
                WHERE o.snapshot_id IN ({placeholders})''',
            batch
        )
        dst.commit()
        done += len(batch)
        elapsed = time.time() - t0
        rate = done / elapsed if elapsed > 0 else 0
        eta_min = (len(all_snapshot_ids) - len(already_migrated_snapshot_ids) - done) / rate / 60 if rate > 0 else float('nan')
        logging.info(f'  migrated {done:,}/{len(all_snapshot_ids) - len(already_migrated_snapshot_ids):,} snapshots '
                      f'({rate:.1f} snapshots/s, ETA {eta_min:.1f} min)')

    logging.info('Building grid_data indexes (one bulk pass over the full table)...')
    t_idx = time.time()
    dst.executescript(GRID_DATA_INDEXES_SQL)
    dst.commit()
    logging.info(f'Indexes built in {time.time() - t_idx:.0f}s')

    # Verification: every old row must have produced exactly one new row.
    # (old_row_count was captured at the very start of this run — if the
    # source is still live, a positive diff can mean rows genuinely didn't
    # match a grid cell, but it can also just mean the source changed
    # underneath us; a NEGATIVE diff always means the destination picked up
    # stale/orphaned rows from a snapshot_id that existed earlier in this run
    # but was since superseded/deleted in the source. Either way: stop the
    # source from changing (e.g. the fetcher container) before trusting this.)
    new_row_count = dst.execute('SELECT COUNT(*) FROM main.grid_data').fetchone()[0]
    logging.info(f'Verification: old grid_data={old_row_count:,} rows, new grid_data={new_row_count:,} rows')
    if new_row_count != old_row_count:
        logging.error(
            f'ROW COUNT MISMATCH: old={old_row_count:,} new={new_row_count:,} (diff {old_row_count - new_row_count:,}). '
            f'Do NOT swap this file into production — investigate first (see this script\'s verification comment '
            f'for what a positive vs. negative diff typically means). {out_path} left in place for inspection.'
        )
        sys.exit(1)

    # Same check for the small reference tables — these are cheap to get exactly right.
    for table in ('snapshots', 'country_aggregates', 'indoor_temps', 'forecast_log'):
        has_table = src.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not has_table:
            continue
        src_count = src.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        dst_count = dst.execute(f'SELECT COUNT(*) FROM main.{table}').fetchone()[0]
        logging.info(f'Verification: {table} old={src_count:,} new={dst_count:,}')
        if src_count != dst_count:
            logging.error(
                f'{table.upper()} ROW COUNT MISMATCH: old={src_count:,} new={dst_count:,}. '
                f'Do NOT swap this file into production — investigate first. {out_path} left in place for inspection.'
            )
            sys.exit(1)

    # Spot-check a handful of snapshots for exact aggregate parity.
    check_ids = all_snapshot_ids[:1] + all_snapshot_ids[len(all_snapshot_ids) // 2:len(all_snapshot_ids) // 2 + 1] + all_snapshot_ids[-1:]
    for sid in check_ids:
        old_sum = tuple(src.execute(
            'SELECT SUM(temperature), COUNT(*) FROM grid_data WHERE snapshot_id = ?', (sid,)
        ).fetchone())
        new_sum = tuple(dst.execute(
            'SELECT SUM(temperature), COUNT(*) FROM main.grid_data WHERE snapshot_id = ?', (sid,)
        ).fetchone())
        if old_sum != new_sum:
            logging.error(f'Spot-check FAILED for snapshot {sid}: old={old_sum} new={new_sum}')
            sys.exit(1)
        logging.info(f'Spot-check OK for snapshot {sid}: {new_sum[1]:,} rows, sum(temperature) matches')

    dst.execute('DETACH DATABASE old')
    dst.execute('PRAGMA synchronous=NORMAL')
    if args.vacuum:
        logging.info('Running VACUUM (this needs scratch space roughly equal to the output file size)...')
        dst.execute('VACUUM')
    else:
        logging.info('Skipping VACUUM (pass --vacuum to run it, if you have confirmed disk headroom)')
    # Switch out of WAL mode so the output is a single self-contained file with
    # no -wal/-shm sidecars to remember to move — a production swap is then a
    # plain `mv` of one file. This also forces a full checkpoint.
    dst.execute('PRAGMA journal_mode=DELETE')
    dst.close()
    src.close()

    for sidecar in (out_path + '-wal', out_path + '-shm'):
        if os.path.exists(sidecar):
            os.remove(sidecar)

    old_size = os.path.getsize(args.db)
    new_size = os.path.getsize(out_path)
    logging.info(
        f'Migration complete: {out_path} ({new_size/1e9:.2f} GB, was {old_size/1e9:.2f} GB — '
        f'{100 * (1 - new_size/old_size):.0f}% smaller). See the "After migration" steps in this '
        f'script\'s docstring to swap it into place.'
    )


if __name__ == '__main__':
    main()
