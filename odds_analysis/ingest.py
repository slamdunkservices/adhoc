#!/usr/bin/env python3
"""
Load the odds-scraper CSV archive into the parquet lake.

Usage:
    python3 ingest.py                          # every league, new files only
    python3 ingest.py --league wnba            # one league
    python3 ingest.py --league wnba --ym 2026-07   # one month, for spot checks
    python3 ingest.py --status                 # what's loaded, no writes

The first run reads ~145k CSVs and takes a while; every run after it picks up
only what the scraper has written since. Safe to interrupt — a killed run
leaves the lake exactly as it was.

Options:
    --raw DIR     archive root (default $ODDS_RAW or ~/Desktop/files/odds_getter)
    --lake DIR    lake root (default $ODDS_LAKE or ./data)
    --strict      stop on the first unreadable file instead of skipping it
    --limit N     stop after N files, for a quick trial run
"""
import os
import re
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

import oddslib
from oddslib import die

# <book>_<league>_<timestamp>.csv. The timestamp is captured but never parsed:
# most of the archive is missing zero-padding ("202659224" is 2026-05-09 02:24),
# so it is genuinely ambiguous. The `ts` column inside the file is the truth.
FILENAME_RE = re.compile(
    r"^(?P<book>[a-z0-9]+)_(?P<league>%s)_(?P<stamp>\d+)\.csv$"
    % "|".join(oddslib.LEAGUES))

MANIFEST_COLUMNS = ["src_file", "size", "mtime_ns", "league", "book",
                    "rows_raw", "rows_kept", "ts_min", "ts_max", "status",
                    "error", "batch", "ingested_at"]

# Rows buffered before a fragment is written out.
ROWS_PER_FRAGMENT = 2_000_000
# Raw rows read before one normalize pass. Files average ~65 rows, so
# normalizing them individually would spend nearly all its time on pandas'
# per-frame overhead rather than on the data.
NORMALIZE_BATCH_ROWS = 400_000


def manifest_path(lake):
    return os.path.join(lake, "manifest.parquet")


def load_manifest(lake):
    path = manifest_path(lake)
    if not os.path.exists(path):
        return pd.DataFrame(columns=MANIFEST_COLUMNS)
    return pq.read_table(path).to_pandas()


def save_manifest(lake, df):
    """Rewrite the manifest atomically — it is the record of what's real."""
    os.makedirs(lake, exist_ok=True)
    path = manifest_path(lake)
    tmp = path + ".tmp"
    pq.write_table(pa.Table.from_pandas(df[MANIFEST_COLUMNS],
                                        preserve_index=False), tmp)
    os.replace(tmp, path)


def sweep_orphans(lake, manifest):
    """Drop fragments from runs that died before their manifest was written.

    Fragments carry the batch id of the run that made them. A batch the
    manifest never heard of is by definition a partial write.
    """
    known = set(manifest["batch"].dropna().astype(str)) if not manifest.empty \
        else set()
    root = oddslib.lake_dir(lake)
    removed = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            m = re.match(r"^part-(?P<batch>[0-9A-Za-z]+)-\d+\.parquet$", name)
            if m and m.group("batch") not in known:
                os.remove(os.path.join(dirpath, name))
                removed += 1
    tmp = os.path.join(lake, "tmp")
    if os.path.isdir(tmp):
        shutil.rmtree(tmp)
    if removed:
        print("swept %d orphaned fragment(s) from an interrupted run" % removed)
    return removed


def discover(raw, leagues=None, limit=None):
    """Every archive CSV worth reading, as (path, relpath, league, book, stat).

    Only `archive/` is read. The `*_current.csv` files beside it are byte-for-
    byte copies of the newest archive snapshot, so reading them would only
    duplicate rows.
    """
    found, skipped = [], []
    for sport_dir in sorted(oddslib.LEAGUES):
        archive = os.path.join(raw, sport_dir, "archive")
        if not os.path.isdir(archive):
            continue
        with os.scandir(archive) as it:
            for entry in it:
                if not entry.is_file() or not entry.name.endswith(".csv"):
                    continue
                m = FILENAME_RE.match(entry.name)
                if not m:
                    skipped.append(os.path.join(sport_dir, "archive", entry.name))
                    continue
                league = m.group("league")
                # The league in the name wins: wnba/archive/ still holds ~12k
                # nba and mlb files from before the per-sport split.
                if leagues and league not in leagues:
                    continue
                st = entry.stat()
                found.append((
                    entry.path,
                    os.path.join(sport_dir, "archive", entry.name),
                    league, m.group("book"), st.st_size, st.st_mtime_ns,
                ))
    found.sort(key=lambda r: r[1])
    if limit:
        found = found[:limit]
    return found, skipped


REQUIRED_COLUMNS = ("event_start", "market_name", "american_odds", "ts")


def read_raw(path, book, relpath):
    """One CSV as raw strings, tagged with where it came from.

    Files average only ~65 rows, so normalizing them one at a time spends
    almost all its time on pandas' per-frame overhead. Reading is kept
    separate so a whole batch can be normalized in one pass instead.
    """
    df = pd.read_csv(path, dtype=str, na_filter=False)
    if df.empty:
        return df
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError("missing column(s): %s" % ", ".join(missing))
    df["_src"] = relpath
    df["_book_hint"] = book
    return df


def normalize_batch(frames, league):
    """Normalize many same-league files at once, preserving per-file lineage.

    Returns (rows, files_whose_book_column_disagreed_with_their_name).
    """
    if not frames:
        return pd.DataFrame(), set()
    parts, mismatched = [], set()
    # The 10- and 13-column schemas can't share a concat, so each shape is
    # normalized on its own. Within a league they rarely both appear.
    by_shape = {}
    for f in frames:
        by_shape.setdefault(tuple(f.columns), []).append(f)
    for shape_frames in by_shape.values():
        raw = pd.concat(shape_frames, ignore_index=True)
        odd = raw["book"].ne("") & raw["book"].ne(raw["_book_hint"])
        if odd.any():
            mismatched |= set(raw.loc[odd, "_src"].unique())
        parts.append(oddslib.normalize_frame(
            raw, league, raw["_book_hint"], raw["_src"]))
    parts = [p for p in parts if not p.empty]
    if not parts:
        return pd.DataFrame(), mismatched
    return pd.concat(parts, ignore_index=True), mismatched


def salvage_batch(frames, league, by_file, exc):
    """Re-run a failed batch one file at a time, keeping everything that works.

    The bad file is recorded as an error in the manifest and skipped, so the
    next run doesn't retry it blindly and the failure is visible in --status.
    """
    print("  batch of %d file(s) failed to parse (%s) — isolating"
          % (len(frames), type(exc).__name__))
    good, mismatched, bad = [], set(), 0
    for f in frames:
        rel = f["_src"].iloc[0] if len(f) else "?"
        try:
            part, mism = normalize_batch([f], league)
        except Exception as inner:
            bad += 1
            rec = by_file.get(rel)
            if rec is not None:
                rec["status"] = "error"
                rec["error"] = "%s: %s" % (type(inner).__name__, inner)
            continue
        mismatched |= mism
        if not part.empty:
            good.append(part)
    print("  isolated %d bad file(s); kept %d" % (bad, len(frames) - bad))
    if not good:
        return pd.DataFrame(), mismatched
    return pd.concat(good, ignore_index=True), mismatched


def flush(rows, lake, batch, seq):
    """Write buffered rows out as one fragment per (league, month)."""
    if not rows:
        return 0, seq
    df = pd.concat(rows, ignore_index=True)
    if df.empty:
        return 0, seq
    df["ym"] = df["ts"].dt.strftime("%Y-%m")

    tmp_dir = os.path.join(lake, "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    written = 0
    for (league, ym), part in df.groupby(["league", "ym"], sort=True):
        part = part[oddslib.LAKE_COLUMNS].reset_index(drop=True)
        name = "part-%s-%d.parquet" % (batch, seq)
        seq += 1
        tmp_path = os.path.join(tmp_dir, name)
        pq.write_table(
            pa.Table.from_pandas(part, preserve_index=False), tmp_path,
            compression="zstd", use_dictionary=True)
        dest_dir = os.path.join(oddslib.lake_dir(lake),
                                "league=%s" % league, "ym=%s" % ym)
        os.makedirs(dest_dir, exist_ok=True)
        os.replace(tmp_path, os.path.join(dest_dir, name))
        written += len(part)
    return written, seq


def status(lake):
    manifest = load_manifest(lake)
    if manifest.empty:
        print("lake at %s is empty — run: python3 ingest.py" % lake)
        return
    ok = manifest[manifest["status"] == "ok"]
    print("lake: %s" % lake)
    print("files ingested: %d   rows: %s" % (len(ok), "{:,}".format(
        int(ok["rows_kept"].sum()))))
    bad = manifest[manifest["status"] != "ok"]
    if not bad.empty:
        print("files skipped: %d (%s)" % (
            len(bad), ", ".join("%s=%d" % (k, v) for k, v in
                                bad["status"].value_counts().items())))
    print()
    rows = []
    for league, grp in ok.groupby("league"):
        size = 0
        root = os.path.join(oddslib.lake_dir(lake), "league=%s" % league)
        for dirpath, _d, files in os.walk(root):
            size += sum(os.path.getsize(os.path.join(dirpath, f))
                        for f in files)
        rows.append({
            "league": league, "files": len(grp),
            "rows": "{:,}".format(int(grp["rows_kept"].sum())),
            "first": str(grp["ts_min"].min())[:16],
            "last": str(grp["ts_max"].max())[:16],
            "parquet_mb": "%.0f" % (size / 1e6),
            "books": grp["book"].nunique(),
        })
    print(oddslib.md_table(pd.DataFrame(rows)))


def ingest(raw, lake, leagues=None, ym=None, strict=False, limit=None):
    started = time.time()
    manifest = load_manifest(lake)
    sweep_orphans(lake, manifest)

    files, badnames = discover(raw, leagues, limit)
    if not files:
        die("no archive CSVs found under %s" % raw)

    seen = {}
    if not manifest.empty:
        seen = dict(zip(manifest["src_file"],
                        zip(manifest["size"], manifest["mtime_ns"])))
    todo = [f for f in files if seen.get(f[1]) != (f[4], f[5])]

    print("%d archive files, %d new or changed" % (len(files), len(todo)))
    if badnames:
        print("%d file(s) ignored — name doesn't parse (e.g. %s)"
              % (len(badnames), badnames[0]))
    if not todo:
        print("lake is already current.")
        return

    batch = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    records, buffer, buffered, seq, total = [], [], 0, 0, 0
    book_mismatch = 0
    pending = {}          # league -> list of raw frames awaiting normalizing
    pending_rows = 0
    by_file = {}

    def checkpoint():
        """Record what's on disk, so an interrupted backfill keeps its work.

        Called only right after a flush, when every row behind these records
        has landed in a fragment. Fragments go down before the manifest does,
        so a kill in between leaves orphans the next run sweeps — never a
        manifest claiming rows that aren't there.
        """
        done = pd.DataFrame(records)
        if done.empty:
            return
        keep = manifest
        if not keep.empty:
            keep = keep[~keep["src_file"].isin(done["src_file"])]
        save_manifest(lake, pd.concat([keep, done], ignore_index=True)
                      if not keep.empty else done)

    def drain(force=False):
        """Normalize what's been read, and write once enough has piled up."""
        nonlocal pending, pending_rows, buffer, buffered, seq, total
        nonlocal book_mismatch
        if not pending_rows:
            return
        for lg, frames in pending.items():
            try:
                frame, mismatched = normalize_batch(frames, lg)
            except Exception as exc:
                # Files are read in bulk and normalized together, so one
                # unreadable file would otherwise take a whole backfill down
                # with it. Retry the batch a file at a time to isolate it.
                if strict:
                    raise
                frame, mismatched = salvage_batch(frames, lg, by_file, exc)
            if mismatched:
                book_mismatch += len(mismatched)
                if strict:
                    die("%s: `book` column disagrees with the filename"
                        % sorted(mismatched)[0])
            if frame.empty:
                continue
            if ym:
                # Applied after parsing: filename stamps are unpadded and
                # cannot be trusted to pick a month.
                frame = frame[frame["ts"].dt.strftime("%Y-%m") == ym]
                if frame.empty:
                    continue
            stats = frame.groupby("src_file").agg(
                rows_kept=("ts", "size"), ts_min=("ts", "min"),
                ts_max=("ts", "max"))
            for rel, row in stats.iterrows():
                rec = by_file.get(rel)
                if rec is not None:
                    rec["rows_kept"] = int(row["rows_kept"])
                    rec["ts_min"] = row["ts_min"]
                    rec["ts_max"] = row["ts_max"]
            buffer.append(frame)
            buffered += len(frame)
        pending, pending_rows = {}, 0

        if buffered >= ROWS_PER_FRAGMENT or force:
            wrote, seq = flush(buffer, lake, batch, seq)
            total += wrote
            buffer, buffered = [], 0
            checkpoint()

    for n, (path, rel, league, book, size, mtime) in enumerate(todo, 1):
        rec = {"src_file": rel, "size": size, "mtime_ns": mtime,
               "league": league, "book": book, "rows_raw": 0, "rows_kept": 0,
               "ts_min": None, "ts_max": None, "status": "ok", "error": "",
               "batch": batch, "ingested_at": pd.Timestamp.now(tz="UTC")}
        by_file[rel] = rec
        records.append(rec)
        try:
            raw = read_raw(path, book, rel)
            rec["rows_raw"] = len(raw)
            if raw.empty:
                rec["status"] = "empty"
            else:
                pending.setdefault(league, []).append(raw)
                pending_rows += len(raw)
        except Exception as exc:            # a bad file shouldn't stop a backfill
            errors += 1
            rec["status"] = "error"
            rec["error"] = "%s: %s" % (type(exc).__name__, exc)
            if strict:
                die("%s: %s" % (rel, exc))

        if pending_rows >= NORMALIZE_BATCH_ROWS:
            drain()
            print("  %d/%d files, %s rows (%.0fs)"
                  % (n, len(todo), "{:,}".format(total + buffered),
                     time.time() - started))

    drain(force=True)
    checkpoint()

    took = time.time() - started
    print("\ningested %d file(s), %s rows in %.0fs"
          % (len(records), "{:,}".format(total), took))
    if book_mismatch:
        print("note: %d file(s) had a `book` column unlike their filename — "
              "kept the column's value" % book_mismatch)
    errors = sum(1 for r in records if r["status"] == "error")
    if errors:
        print("note: %d file(s) failed and were skipped — see status=error in "
              "the manifest (`python3 ingest.py --status`)" % errors)


def main():
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        sys.exit(__doc__.strip())

    def opt(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default

    lake = opt("--lake", oddslib.LAKE_DEFAULT)
    raw = opt("--raw", oddslib.RAW_DEFAULT)

    if "--status" in argv:
        status(lake)
        return

    league = opt("--league")
    if league and league not in oddslib.LEAGUES:
        die("unknown league %r — one of %s"
            % (league, ", ".join(oddslib.LEAGUES)))
    ym = opt("--ym")
    if ym and not re.match(r"^\d{4}-\d{2}$", ym):
        die("--ym wants YYYY-MM, got %r" % ym)
    limit = opt("--limit")
    if not os.path.isdir(raw):
        die("archive not found at %s — pass --raw" % raw)

    ingest(raw, lake, leagues=[league] if league else None, ym=ym,
           strict="--strict" in argv, limit=int(limit) if limit else None)


if __name__ == "__main__":
    main()
