#!/usr/bin/env python3
"""
find_plays.py — today's postable plays, straight from the edge tables.

Emits the same alert-format lines the Discord feeds post, so a card can be
built without anyone pasting numbers in:

    * Jhostynxon Garcia 1+ (proj +873): +1200 br (0.7u)
    * Kiah Stokes first basket by team exact, two point field goal
      (proj +916 fg): +1100 dk (0.8u)

Usage:
    python3 find_plays.py                     # every league, today
    python3 find_plays.py --league wnba
    python3 find_plays.py --player "Kiah Stokes"
    python3 find_plays.py --date 2026-08-03
    python3 find_plays.py --json             # machine-readable

READ-ONLY. This script opens data files and writes nothing, anywhere.

MLB reuses the production alert math by importing pure helpers out of
jobs/odds/src/py/mlb_home_runs_alerts.py, so the numbers here match the feed
by construction rather than by reimplementation. It never calls that module's
main(): main() writes the day-peak state files behind the 🚨/🚀/⬇️ emoji in the
real Discord feed, and running it would corrupt them.

Data roots come from ~/.<league>_jobs.env. Those files also hold webhooks and
tokens — only *_DATA_ROOT is ever read out of them, and nothing is echoed.

Covers MLB home runs and the WNBA first-event markets: the only two leagues a
card has ever been made for. PGA and F1 also publish edge tables
(``02_curated/edges/{pga,f1}_edges_current.csv``) and would slot in as another
``*_plays()`` function; NFL and NHL have model outputs but no edge table yet,
and NBA has no ~/.nba_jobs.env on this host.
"""
import argparse
import csv
import gzip
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
# .claude/skills/pick-post/ -> repo root -> sibling jobs repo
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
JOBS = os.environ.get("JOBS_ROOT", os.path.abspath(os.path.join(REPO, "..", "jobs")))
ALERT_LIB = os.path.join(JOBS, "odds", "src", "py")

ET = timezone(timedelta(hours=-4))  # matches the alert scripts' day boundary

# Books the alert feed never posts: prediction-market exchanges. Their prices
# move too often and the resting liquidity is too thin to call broadly
# available. Mirrors EXCLUDED_ALERT_BOOKS in mlb_home_runs_alerts.py.
EXCLUDED = ("novig", "kalshi", "nvg", "ksh")

# Per-market alert floors, mirroring jobs/mlb/_lib.sh
# mlb_bet_min_edge_overrides_for_date(). 1+ tightened to 2% at the cutover.
MLB_MIN_EDGE = 0.015
MLB_MIN_EDGE_HR1 = 0.02
MLB_HR1_CUTOVER = "2026-07-29"

# WNBA plays_*.csv are already filtered to qualifying plays by the R edge
# pipeline, so there is no threshold to re-apply here — only the per-book
# units sign. `proj` names which model grades the market (see SKILL.md rule 3):
# exact-method markets settle on a made FG, so they carry the FG price alone.
WNBA_MARKETS = {
    "first_basket":                {"label": "first basket",                "proj": [("line_points", "points"), ("line_fgm", "fg")]},
    "first_basket_by_team":        {"label": "first basket by team",        "proj": [("line_points", "points"), ("line_fgm", "fg")]},
    "first_basket_by_team_exact":  {"label": "first basket by team exact",  "proj": [("line_fgm", "fg")]},
    "first_basket_exact":          {"label": "first basket exact",          "proj": [("line_fgm", "fg")]},
    "first_three_by_team":         {"label": "first three by team",         "proj": [("line_fg3m", "fg3")]},
}
# Team/game-level markets — no player, so no card. Surfaced as a count only.
WNBA_TEAM_MARKETS = ("first_team", "first_team_exact", "first_basket_type")

WNBA_BOOKS = ("dk", "fd", "mgm", "br", "tsb", "ftics", "csr", "hrb", "b365", "nvg")

# Per-market unit-standardization norms. plays_*.csv carries RAW half-Kelly in
# `<code>_units`; the Discord alert posts units / norm, and that standardized
# number is the recommended stake. Read live from the R config because these
# move (first_basket_by_team_exact was 1.38 in June, 1.31 by August) — the
# literals below are only a last-resort fallback.
WNBA_UNIT_NORMS_FALLBACK = {
    "first_team": 1.47, "first_team_exact": 1.10, "first_basket_type": 0.67,
    "first_basket": 1.10, "first_basket_by_team": 2.10, "first_basket_exact": 0.65,
    "first_basket_by_team_exact": 1.31, "first_three_by_team": 1.56,
}
WNBA_NORM_SOURCE = "shared/basketball/R/first_event_market_config_utils.R"


def wnba_unit_norms():
    """(norms, source) — parsed from first_event_tracking_avg_units() in models/."""
    models = os.environ.get("MODELS_ROOT")
    if not models:
        for lg in ("wnba", "mlb"):
            env = os.path.expanduser("~/.%s_jobs.env" % lg)
            if not os.path.exists(env):
                continue
            with open(env) as fh:
                for line in fh:
                    m = re.match(r"\s*(?:export\s+)?MODELS_ROOT\s*=\s*(.*)", line)
                    if m:
                        models = os.path.expanduser(m.group(1).strip().strip('"\''))
                        break
            if models:
                break
    path = os.path.join(models, WNBA_NORM_SOURCE) if models else None
    if not path or not os.path.exists(path):
        return dict(WNBA_UNIT_NORMS_FALLBACK), "fallback literals (models repo not found)"
    src = open(path).read()
    fn = re.search(r"first_event_tracking_avg_units\s*<-\s*function.*?\n\}", src, re.S)
    block = re.search(r"\bwnba\s*=\s*c\((.*?)\)", fn.group(0) if fn else src, re.S)
    if not block:
        return dict(WNBA_UNIT_NORMS_FALLBACK), "fallback literals (couldn't parse R config)"
    norms = {k: float(v) for k, v in re.findall(r"(\w+)\s*=\s*([0-9.]+)", block.group(1))}
    return (norms, path) if norms else (dict(WNBA_UNIT_NORMS_FALLBACK), "fallback literals")


def die(msg):
    sys.exit("error: " + msg)


def data_root(league):
    """Read <LEAGUE>_DATA_ROOT out of ~/.<league>_jobs.env, and nothing else."""
    env = os.path.expanduser("~/.%s_jobs.env" % league)
    if not os.path.exists(env):
        return None
    want = "%s_DATA_ROOT" % league.upper()
    with open(env) as fh:
        for line in fh:
            m = re.match(r"\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*)", line)
            if m and m.group(1) == want:
                return os.path.expanduser(m.group(2).strip().strip('"\''))
    return None


def load_alert_helpers():
    """Pure formatting/sizing helpers from the production alert module."""
    if ALERT_LIB not in sys.path:
        sys.path.insert(0, ALERT_LIB)
    try:
        import mlb_home_runs_alerts as a
        return a
    except Exception as exc:
        die("can't import the alert helpers from %s (%s: %s).\n"
            "       Set JOBS_ROOT if the jobs repo isn't beside this one."
            % (ALERT_LIB, type(exc).__name__, exc))


def fold(s):
    """Accent- and case-insensitive form for name matching: 'Báez' -> 'baez'.

    Every roster in both leagues carries accented names (Báez, Narváez,
    Ramírez), and the obvious `--player Baez` misses all of them. Matching on
    the raw string doesn't just run slow — it prints "(no qualifying plays)",
    which reads as "there is no play" rather than "you spelled it without the
    accent". Fold both sides at every comparison.
    """
    return "".join(c for c in unicodedata.normalize("NFKD", str(s))
                   if not unicodedata.combining(c)).lower()


def fmt_units(u):
    """Nearest-tenth, trailing zero trimmed — 0.71 -> '0.7', 1.01 -> '1'."""
    s = "%.1f" % (round(float(u) * 10.0) / 10.0)
    return s.rstrip("0").rstrip(".") or "0"


def prob_to_american(p):
    p = float(p)
    if not 0.0 < p < 1.0:
        return "n/a"
    dec = 1.0 / p
    am = round((dec - 1.0) * 100.0) if dec >= 2.0 else round(-100.0 / (dec - 1.0))
    return "+%d" % am if am > 0 else "%d" % am


def fmt_american(v):
    a = int(round(float(v)))
    return "+%d" % a if a > 0 else "%d" % a


# ---------------------------------------------------------------- MLB

def _mlb_frame(root, date):
    """The fullest view of `date`: (DataFrame, path).

    home_runs_edges_current.csv holds the live slate. Older dates come out of
    the archive, whose filenames are UTC-stamped — a late ET game on date D
    lands in a file stamped D or D+1. Among those, take the file with the most
    rows for D, not the newest: the newest is a late-night snapshot with most
    props already pulled, so it under-reports the slate.
    """
    import pandas as pd
    edges = os.path.join(root, "02_curated", "edges")

    # The edge job rewrites current.csv in place every ~30 min, so a read can
    # land mid-write and see an empty or truncated file. Fall through to the
    # archive rather than dying — it holds the same rows, timestamped.
    cur = os.path.join(edges, "home_runs_edges_current.csv")
    if os.path.exists(cur):
        try:
            df = pd.read_csv(cur)
            if "date" in df.columns and (df["date"].astype(str) == date).any():
                return df, cur
        except Exception:
            pass

    arc = os.path.join(edges, "archive")
    if not os.path.isdir(arc):
        return None, None
    nxt = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y%m%d")
    stems = (date.replace("-", ""), nxt)

    best, best_n = None, 0
    for f in os.listdir(arc):
        if not f.startswith("home_runs_edges_") or not any(s in f for s in stems):
            continue
        p = os.path.join(arc, f)
        try:  # cheap pass — one column
            n = int((pd.read_csv(p, usecols=["date"])["date"].astype(str) == date).sum())
        except Exception:
            continue
        if n > best_n:
            best, best_n = p, n
    return (pd.read_csv(best), best) if best else (None, None)


def mlb_plays(root, date, player):
    alerts = load_alert_helpers()

    df, path = _mlb_frame(root, date)
    if df is None:
        return [], "no edge table holds %s (checked current + archive)" % date
    df = df.dropna(subset=["projection", "american_odds", "edge", "player_id"])
    df = df[~df["book"].str.lower().isin(EXCLUDED)]
    df = df[df["date"].astype(str) == date]
    if player:
        df = df[df["player"].map(fold).str.contains(fold(player), regex=False)]
    if df.empty:
        return [], None

    floor = MLB_MIN_EDGE_HR1 if date >= MLB_HR1_CUTOVER else MLB_MIN_EDGE
    thresholds = df["market"].map(lambda m: floor if m == "1+" else MLB_MIN_EDGE)
    qualifying = df[df["edge"] >= thresholds]

    out = []
    for (name, market), rows in qualifying.groupby(["player", "market"], sort=False):
        # Stake comes from the stored units_std, never recomputed (rule 4).
        # half_kelly_units() returns RAW half-Kelly at its default scale=1.0;
        # the feed passes HOME_RUNS_UNIT_SCALE (1/HOME_RUNS_UNIT_NORM), and that
        # norm changed at a cutover — so recomputing silently inflates current
        # dates ~2x and is simply wrong for older ones. Each row already carries
        # the units_std that was alerted, plus the unit_norm that produced it.
        groups = {}
        for _, r in rows.iterrows():
            try:
                units = float(r["units_std"])
                am = int(round(float(r["american_odds"])))
            except (ValueError, TypeError):
                continue
            if units < 0.05:
                continue
            g = groups.setdefault(am, {"units": units, "codes": set()})
            g["units"] = max(g["units"], units)
            g["codes"].add(alerts.book_code(r["book"]))
        if not groups:
            continue

        order = alerts.BOOK_ORDER
        segments = []
        for am, g in sorted(groups.items(), key=lambda kv: kv[1]["units"], reverse=True):
            codes = "/".join(sorted(g["codes"],
                                    key=lambda c: (order.index(c) if c in order else 99, c)))
            segments.append("%s %s (%su)" % (alerts.fmt_american(am), codes,
                                             fmt_units(g["units"])))
        best_units = max(g["units"] for g in groups.values())
        first = rows.iloc[0]
        # `team` used to disagree with `event` for traded players: the crosswalk
        # tracks StatsAPI `currentTeam`, which lags trades by days, so on
        # 2026-08-04 Luis Arraez was still listed as a Giant while batting 8th
        # for the Phillies. The GAME was right; the TEAM was stale. Fixed
        # upstream (mlb_home_runs_edges.resolve_teams now derives team from the
        # simulated game), but archived tables predating that fix still carry it,
        # so keep flagging — a card's `team` must be the club they play for now
        # (rules 2 and 6).
        team, event = str(first["team"]), str(first.get("event", ""))
        matchup_ok = bool(event) and team in event
        out.append({
            "league": "MLB",
            "player": name,
            "market": market,
            "market_label": market,
            "team": team,
            "matchup": str(first.get("matchup", "")),
            "matchup_ok": matchup_ok,
            "proj": [(alerts.prob_to_american(float(first["projection"])), "")],
            "segments": segments,
            "best_units": best_units,
            "stamp": str(first.get("edge_run_timestamp", "")),
        })
    out.sort(key=lambda p: p["best_units"], reverse=True)
    suspect = sum(1 for p in out if not p["matchup_ok"])
    return out, ("%d play(s) marked ⚠ — the listed team isn't playing in that "
                 "game, which means a stale post-trade crosswalk. The game is "
                 "right; confirm the club before it reaches a card" % suspect
                 if suspect else None)


# ---------------------------------------------------------------- WNBA

def wnba_history(root, date, player):
    """Past dates: tracking/<market>/<date>/<run>.csv — long form, one row per book."""
    base = os.path.join(root, "02_curated", "wnba_first_to_score", "tracking")
    out = []
    for slug, spec in WNBA_MARKETS.items():
        day = os.path.join(base, slug, date)
        if not os.path.isdir(day):
            continue
        runs = sorted(f for f in os.listdir(day) if f.endswith(".csv"))
        if not runs:
            continue
        with open(os.path.join(day, runs[-1])) as fh:  # last run of the day
            rows = list(csv.DictReader(fh))

        keyed = {}
        for r in rows:
            name = r.get("player_name", "")
            if not name or (player and fold(player) not in fold(name)):
                continue
            if r.get("site", "") in EXCLUDED:
                continue
            # units_standardized is what the alert recommended THAT DAY, already
            # divided by the norm in force then. The norms change, so never
            # re-derive it from `units` for a historical date (rule 4).
            try:
                units = float(r.get("units_standardized") or 0)
                line = int(round(float(r["line"])))
            except (ValueError, TypeError, KeyError):
                continue
            if units < 0.05:
                continue
            k = (name, r.get("shot_type", ""))
            g = keyed.setdefault(k, {"row": r, "groups": {}})
            grp = g["groups"].setdefault(line, {"units": units, "codes": set()})
            grp["units"] = max(grp["units"], units)
            grp["codes"].add(r["site"])

        for (name, shot), g in keyed.items():
            segments = ["%s %s (%su)" % (fmt_american(am), "/".join(sorted(v["codes"])),
                                         fmt_units(v["units"]))
                        for am, v in sorted(g["groups"].items(),
                                            key=lambda kv: kv[1]["units"], reverse=True)]
            r = g["row"]
            label = spec["label"] + (", %s" % shot if shot else "")
            tag = spec["proj"][-1][1]  # the model that grades it (rule 3)
            out.append({
                "league": "WNBA", "player": name, "market": slug, "market_label": label,
                "team": r.get("team_abbreviation", ""), "matchup": r.get("game", ""),
                "proj": [(fmt_american(r["line_model"]), tag)] if r.get("line_model") else [],
                "segments": segments,
                "best_units": max(v["units"] for v in g["groups"].values()),
                "stamp": r.get("run_timestamp_utc", ""), "shot_type": shot,
            })
    out = [p for p in out if p["proj"] and p["segments"]]
    out.sort(key=lambda p: p["best_units"], reverse=True)
    return out, ("historical — from tracking/, last run of %s" % date if out else None)


def wnba_plays(root, date, player):
    base = os.path.join(root, "02_curated", "wnba_first_to_score", "plays")
    if not os.path.isdir(base):
        return [], "no plays directory at %s" % base

    # plays_*.csv is the live slate only; older dates live in tracking/.
    today = datetime.now(ET).strftime("%Y-%m-%d")
    if date != today:
        return wnba_history(root, date, player)

    out, team_level, notes_out = [], 0, []
    for slug in WNBA_TEAM_MARKETS:
        p = os.path.join(base, "plays_%s.csv" % slug)
        if os.path.exists(p):
            with open(p) as fh:
                team_level += sum(1 for r in csv.DictReader(fh)
                                  if str(r.get("game_date", "")) == date)

    norms, norm_src = wnba_unit_norms()
    for slug, spec in WNBA_MARKETS.items():
        path = os.path.join(base, "plays_%s.csv" % slug)
        if not os.path.exists(path):
            continue
        norm = norms.get(slug)
        if not norm:
            notes_out.append("%s: no unit norm configured — skipped" % slug)
            continue
        with open(path) as fh:
            for row in csv.DictReader(fh):
                if str(row.get("game_date", "")) != date:
                    continue
                name = row.get("player_name", "")
                if player and fold(player) not in fold(name):
                    continue

                groups = {}
                for code in WNBA_BOOKS:
                    if code in EXCLUDED:
                        continue
                    line, units = row.get("%s_line" % code), row.get("%s_units" % code)
                    if not line or not units or line in ("NA", "") or units in ("NA", ""):
                        continue
                    try:
                        line_v, units_v = float(line), float(units)
                    except ValueError:
                        continue
                    # plays_*.csv holds RAW half-Kelly; the alert recommends
                    # units / norm. Post the alert's number (rule 4).
                    units_v = units_v / norm
                    # Anything that displays as 0u isn't a postable stake — the
                    # card needs a real number in `stake`.
                    if units_v < 0.05:
                        continue
                    am = int(round(line_v))
                    g = groups.setdefault(am, {"units": units_v, "codes": set()})
                    g["units"] = max(g["units"], units_v)
                    g["codes"].add(code)
                if not groups:
                    continue

                segments = []
                for am, g in sorted(groups.items(), key=lambda kv: kv[1]["units"], reverse=True):
                    codes = "/".join(sorted(g["codes"],
                                            key=lambda c: (WNBA_BOOKS.index(c) if c in WNBA_BOOKS else 99, c)))
                    segments.append("%s %s (%su)" % (fmt_american(am), codes, fmt_units(g["units"])))

                proj = []
                for col, tag in spec["proj"]:
                    v = row.get(col)
                    if v and v not in ("NA", ""):
                        proj.append((fmt_american(v), tag))
                if not proj:
                    continue

                label = spec["label"]
                if row.get("shot_type"):
                    label += ", %s" % row["shot_type"]

                out.append({
                    "league": "WNBA",
                    "player": name,
                    "market": slug,
                    "market_label": label,
                    "team": row.get("team_abbreviation", ""),
                    "matchup": row.get("game", ""),
                    "proj": proj,
                    "segments": segments,
                    "best_units": max(g["units"] for g in groups.values()),
                    "stamp": row.get("run_timestamp_utc", ""),
                    "shot_type": row.get("shot_type", ""),
                })

    out.sort(key=lambda p: p["best_units"], reverse=True)
    if team_level:
        notes_out.append("%d team-level play(s) not shown — no player, so no card" % team_level)
    if out:
        notes_out.append("stakes standardized per market from %s" % norm_src)
    return out, ("; ".join(notes_out) if notes_out else None)


# ------------------------------------------------------ roster enrichment

def wnba_roster(root, name):
    """Jersey + team city for the card's `jersey` / `team` fields."""
    path = os.path.join(root, "02_curated", "rosters", "current.csv")
    if not os.path.exists(path):
        return {}
    with open(path) as fh:
        for r in csv.DictReader(fh):
            if fold(r.get("PLAYER_NAME", "")) == fold(name):
                return {"jersey": r.get("JERSEY_NUMBER", ""),
                        "team_city": r.get("TEAM_CITY", ""),
                        "team_name": r.get("TEAM_NAME", "")}
    return {}


# ---------------------------------------------------------------- output

def render(play):
    proj = ", ".join(("%s %s" % (v, t)).strip() for v, t in play["proj"])
    head = "%s %s" % (play["player"], play["market_label"])
    return "* %s (proj %s): %s" % (head, proj, ", ".join(play["segments"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--league", choices=("mlb", "wnba"), help="default: both")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: today ET)")
    ap.add_argument("--player", default=None, help="substring match on player name")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    date = args.date or datetime.now(ET).strftime("%Y-%m-%d")
    leagues = [args.league] if args.league else ["mlb", "wnba"]

    everything, notes = [], []
    for lg in leagues:
        root = data_root(lg)
        if not root:
            notes.append("%s: no ~/.%s_jobs.env — skipped" % (lg.upper(), lg))
            continue
        if not os.path.isdir(root):
            notes.append("%s: data root %s not mounted — skipped" % (lg.upper(), root))
            continue
        plays, note = (mlb_plays if lg == "mlb" else wnba_plays)(root, date, args.player)
        if lg == "wnba":
            for p in plays:
                p.update(wnba_roster(root, p["player"]))
        everything += plays
        if note:
            notes.append("%s: %s" % (lg.upper(), note))

    if args.json:
        print(json.dumps({"date": date, "plays": everything, "notes": notes}, indent=2))
        return

    print("Plays for %s\n" % date)
    for lg in leagues:
        rows = [p for p in everything if p["league"] == lg.upper()]
        if not rows:
            continue
        print("## %s" % lg.upper())
        # Group by matchup, best group first, best play first inside it. Never
        # print a header and then let a lower-ranked play from another game fall
        # underneath it.
        by_game = {}
        for p in rows:
            by_game.setdefault(p["matchup"] or "(no game)", []).append(p)
        for game, plays in sorted(by_game.items(),
                                  key=lambda kv: max(x["best_units"] for x in kv[1]),
                                  reverse=True):
            suspect = any(p.get("matchup_ok") is False for p in plays)
            print("\n### %s%s" % (game, "   ⚠ stale team below" if suspect else ""))
            for p in sorted(plays, key=lambda x: x["best_units"], reverse=True):
                print(render(p))
                extra = [x for x in (("#%s" % p["jersey"]) if p.get("jersey") else "",
                                     p.get("team_city", ""),
                                     ("team says %s" % p["team"])
                                     if p.get("matchup_ok") is False else "",
                                     p.get("stamp", "")) if x]
                if extra:
                    print("    %s" % " · ".join(extra))
        print()

    if not everything:
        print("(no qualifying plays)")
    for n in notes:
        print("note: %s" % n)
    print("\nNumbers move. Re-run before posting, and see SKILL.md rules 3 and 4.")


if __name__ == "__main__":
    main()
