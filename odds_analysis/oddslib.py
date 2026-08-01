#!/usr/bin/env python3
"""
Shared plumbing for the odds-analysis tools.

Not a CLI (beyond a self-test) — the tools import it:

    python3 oddslib.py    # run the self-test

Everything the tools have in common lives here: odds math, the raw-CSV
normalizer, the event/outcome keys that let one outcome be tracked across
books and time, no-vig fair prices, and the parquet lake reader.
"""
import datetime as dt
import json
import os
import re
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds

ROOT = os.path.dirname(os.path.abspath(__file__))
LAKE_DEFAULT = os.environ.get("ODDS_LAKE") or os.path.join(ROOT, "data")
RAW_DEFAULT = os.environ.get("ODDS_RAW") or "/Users/jim/Desktop/files/odds_getter"
OUT_DIR = os.path.join(ROOT, "out")

# The scraper writes one directory per league, but `wnba/archive/` predates the
# split and still holds ~12k nba/mlb files. The league in the *filename* is the
# only trustworthy one — see README, "Where the data comes from".
LEAGUES = ("nba", "wnba", "nfl", "mlb", "pga", "f1")
TEAM_LEAGUES = ("nba", "wnba", "nfl", "mlb")   # 10-column schema, "AWY @ HOM"
FIELD_LEAGUES = ("pga", "f1")                  # 13-column schema, one big field

# Books that stopped reporting. Kept in the lake for historical work, excluded
# from present-tense rankings unless a tool passes include_dead.
DEAD_BOOKS = ("espnbet", "bet365")
# Exchanges, not books: no vig to speak of, so their price is roughly the fair
# price. Never ranked alongside the books — used as the sanity benchmark.
EXCHANGES = ("novig", "kalshi")

# MLB is the only league whose event names are full club names ("New York
# Yankees @ Chicago Cubs"). The 2025-era files use abbreviations instead, so
# both eras collapse to the abbreviation to keep one event_key vocabulary.
MLB_ABBREV = {
    "Arizona Diamondbacks": "AZ", "Athletics": "ATH", "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS", "Chicago Cubs": "CHC",
    "Chicago White Sox": "CWS", "Cincinnati Reds": "CIN",
    "Cleveland Guardians": "CLE", "Colorado Rockies": "COL",
    "Detroit Tigers": "DET", "Houston Astros": "HOU",
    "Kansas City Royals": "KC", "Los Angeles Angels": "LAA",
    "Los Angeles Dodgers": "LAD", "Miami Marlins": "MIA",
    "Milwaukee Brewers": "MIL", "Minnesota Twins": "MIN", "New York Mets": "NYM",
    "New York Yankees": "NYY", "Philadelphia Phillies": "PHI",
    "Pittsburgh Pirates": "PIT", "San Diego Padres": "SD",
    "San Francisco Giants": "SF", "Seattle Mariners": "SEA",
    "St. Louis Cardinals": "STL", "Tampa Bay Rays": "TB", "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR", "Washington Nationals": "WSH",
}

# hardrockbet named MLB games "Athletics vs Red Sox" until 2026-07-29, when it
# switched to everyone else's "Boston Red Sox @ Athletics". Two things differ:
# the clubs are nicknames, and the home team comes first. Left unhandled, that
# book's whole MLB history forks off into events no other book shares.
MLB_NICKNAME = {
    "Diamondbacks": "AZ", "Athletics": "ATH", "Braves": "ATL", "Orioles": "BAL",
    "Red Sox": "BOS", "Cubs": "CHC", "White Sox": "CWS", "Reds": "CIN",
    "Guardians": "CLE", "Rockies": "COL", "Tigers": "DET", "Astros": "HOU",
    "Royals": "KC", "Angels": "LAA", "Dodgers": "LAD", "Marlins": "MIA",
    "Brewers": "MIL", "Twins": "MIN", "Mets": "NYM", "Yankees": "NYY",
    "Phillies": "PHI", "Pirates": "PIT", "Padres": "SD", "Giants": "SF",
    "Mariners": "SEA", "Cardinals": "STL", "Rays": "TB", "Rangers": "TEX",
    "Blue Jays": "TOR", "Nationals": "WSH",
}

# How the vig comes out depends on what a market's prices have to add up to.
#
#   two_way      the two sides of one number — moneyline, spread, total.
#                Their implied probabilities sum to ~1.05.
#   complement   a yes/no pair that the scraper files as two market names.
#                YRFI and NRFI are the same question, so they pair across it.
#   exclusive    a field where exactly one entrant can win — first basket,
#                the outright. The whole field sums to ~1.05.
#   independent  standalone yes/no props that are not alternatives to each
#                other. Every hitter can homer in the same game: "home run 1+"
#                across a lineup sums to ~2.8, so normalizing it to 1 would
#                understate every price by about a third.
#
# Only the first three can be devigged. For the last one the honest reference
# is what the other books say, with their vig still in it — see fair_prices().
TWO_WAY = ("moneyline", "spread", "total")
# The two markets books publish as a ladder of alternate numbers — see
# main_line(), which picks the one that actually is "the" spread or total.
LINE_MARKETS = ("spread", "total")
COMPLEMENT_PAIRS = {"YRFI": "NRFI", "NRFI": "YRFI"}
EXCLUSIVE_FIELDS = (
    "first basket", "first basket exact", "first basket type", "first team",
    "first team exact", "first three", "first dunk", "first free throw",
    "first two", "win tipoff", "tournament winner", "race winner",
    "driver championship",
)
# "first basket by team" looks like a field but is really two of them, one per
# roster, summing to ~2.2. The scraper doesn't record which team a player is
# on for this market — `matched_team_name` is empty on every row — so the two
# can't be told apart, and it's treated as un-devigable rather than guessed at.
# `market_type` carries the shot type on first-basket-exact only; on totals it
# carries over/under, which we move into `side` instead.
OVER_UNDER = ("over", "under")

# Game day is the ET calendar date — the day a bettor would call the game.
ET = dt.timezone(dt.timedelta(hours=-4))  # see et_date(); DST-aware below

# Card copy per market, for the build_card.py bridge in line_shop.py.
# market -> (tag_sub, pick_text, chip)
MARKET_CARD = {
    "home run 1+": ("HOME RUN PROP", "HOME RUN", "1+"),
    "home run 2+": ("HOME RUN PROP", "2+ HOME RUNS", "2+"),
    "home run 3+": ("HOME RUN PROP", "3+ HOME RUNS", "3+"),
    "first basket": ("FIRST BASKET PROP", "FIRST BASKET", "1ST"),
    "first basket exact": ("FIRST BASKET PROP", "FIRST BASKET", "1ST"),
    "first basket by team": ("FIRST BASKET PROP", "FIRST BASKET", "1ST"),
    "first three": ("FIRST THREE PROP", "FIRST THREE", "3PT"),
    "first dunk": ("FIRST DUNK PROP", "FIRST DUNK", "DUNK"),
    "moneyline": ("GAME PICK", "MONEYLINE", ""),
    "spread": ("GAME PICK", "SPREAD", ""),
    "total": ("GAME PICK", "TOTAL", ""),
    "YRFI": ("FIRST INNING", "YES RUN 1ST INN", "YRFI"),
    "NRFI": ("FIRST INNING", "NO RUN 1ST INN", "NRFI"),
    "tournament winner": ("GOLF FUTURE", "OUTRIGHT", ""),
    "race winner": ("F1 FUTURE", "RACE WINNER", ""),
}

LEAGUE_TZ = {"nba": "America/New_York", "wnba": "America/New_York",
             "nfl": "America/New_York", "mlb": "America/New_York",
             "pga": "America/New_York", "f1": "America/New_York"}


def die(msg):
    sys.exit("error: " + msg)


# --- odds math ------------------------------------------------------------

def american_to_decimal(a):
    """American odds as a decimal payout multiplier (+150 -> 2.5)."""
    a = float(a)
    if a == 0:
        return float("nan")
    return a / 100.0 + 1.0 if a > 0 else 100.0 / abs(a) + 1.0


def decimal_to_american(d):
    """Decimal multiplier back to American odds, rounded to the integer."""
    d = float(d)
    if not d > 1.0:
        return float("nan")
    return round((d - 1.0) * 100.0) if d >= 2.0 else -round(100.0 / (d - 1.0))


def american_to_prob(a):
    """Implied probability, vig included."""
    d = american_to_decimal(a)
    return float("nan") if d != d else 1.0 / d


def prob_to_american(p):
    """Probability to the American price that pays it fairly."""
    p = float(p)
    if not 0.0 < p < 1.0:
        return float("nan")
    return decimal_to_american(1.0 / p)


def fmt_american(a):
    """American odds as the string a card wants: "+323", "-110", "" if unknown."""
    if a is None or a != a:
        return ""
    a = int(round(float(a)))
    return "%+d" % a


# --- raw CSV normalizing --------------------------------------------------

def parse_ts(series):
    """The `ts` column: always full ISO-8601 UTC, so one pass does it."""
    return pd.to_datetime(series, utc=True, format="ISO8601")


def parse_event_start(series):
    """Normalize `event_start` to UTC across every format in the archive.

    The scraper's output drifted over time and all of these still appear:
      "2025-04-15T23:30:00.000Z"    ISO with a Z
      "2025-04-17 00:30:00"         naive — verified to be UTC
      "2026-07-31 19:30:00-04:00"   space-separated with an ET offset
      "1744759800000"               epoch milliseconds, in the oldest files
    Handing the mix to one to_datetime call raises under pandas 3, so each
    shape is parsed on its own and stitched back together.
    """
    s = pd.Series(series, dtype="object").fillna("").astype(str).str.strip()
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[us, UTC]")

    blank = s.eq("") | s.str.lower().isin(("nan", "nat", "none"))
    # 13 digits is milliseconds, 10 is seconds; both turn up.
    epoch = pd.Series(False, index=s.index)
    for length, unit in ((13, "ms"), (10, "s")):
        hit = ~blank & s.str.fullmatch(r"\d{%d}" % length)
        if hit.any():
            out.loc[hit] = pd.to_datetime(
                s[hit].astype("int64"), unit=unit, utc=True
            ).astype("datetime64[us, UTC]")
            epoch |= hit

    rest = ~blank & ~epoch
    # An offset is a +/- in the time half, past the "YYYY-MM-DD" prefix.
    has_offset = s.str.slice(10).str.contains(r"[+-]", regex=True, na=False)
    is_z = s.str.endswith("Z")
    aware = (is_z | has_offset) & rest
    naive = rest & ~aware

    if aware.any():
        out.loc[aware] = pd.to_datetime(
            s[aware], utc=True, format="ISO8601").astype("datetime64[us, UTC]")
    if naive.any():
        out.loc[naive] = pd.to_datetime(
            s[naive], format="ISO8601").dt.tz_localize("UTC").astype(
                "datetime64[us, UTC]")
    return out


def et_date(ts_utc, league="nba"):
    """The ET calendar date of a UTC timestamp — the game day a bettor means."""
    tz = LEAGUE_TZ.get(league, "America/New_York")
    return pd.Series(ts_utc).dt.tz_convert(tz).dt.date


def norm_team(name, league):
    """Team as its abbreviation, collapsing MLB's two naming eras."""
    name = (name or "").strip()
    if league == "mlb":
        return MLB_ABBREV.get(name, name)
    return name


def slugify(text):
    """Lowercase, hyphenated, safe for a filename or a key."""
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return s or "unknown"


def split_event(event_name, league):
    """"AWY @ HOM" -> (away, home) abbreviations; (None, None) if not a game."""
    if not event_name or " @ " not in event_name:
        return None, None
    away, home = event_name.split(" @ ", 1)
    return norm_team(away, league), norm_team(home, league)


def make_event_key(league, event_date, away, home, event_name, game_no=None):
    """A stable id for one contest, agreeing across books and naming eras.

    Deliberately built from the ET date and the matchup, not the clock: books
    disagree on start time (kalshi has been seen 3 hours off), and the value
    format drifted. Tournaments have no matchup, so they key on their name.
    """
    if league in FIELD_LEAGUES or not away or not home:
        return "%s|%s" % (league, slugify(event_name))
    key = "%s|%s|%s@%s" % (league, event_date, away, home)
    return key if not game_no else "%s|G%d" % (key, game_no)


def make_outcome_key(event_key, market, side, subtype=""):
    """A stable id for one bettable outcome within an event.

    The line is left out on purpose: a spread drifting 12.5 -> 13 is the same
    outcome at a new number, which is what lets movement analysis separate a
    price move from a line move. Group on (outcome_key, line) when you need
    the series for one specific number.
    """
    return "%s|%s|%s|%s" % (event_key, market, side, subtype or "")


def derive_side(row_market, market_type, team, player, league):
    """Which selection a row is: a team, over/under, a player, or the market."""
    mt = (market_type or "").strip().lower()
    if mt in OVER_UNDER:
        return mt
    if player:
        return player
    if team:
        return norm_team(team, league)
    return row_market


def derive_subtype(market_type):
    """`market_type` only means something on the exact-shot first-basket market."""
    mt = (market_type or "").strip().lower()
    return "" if mt in OVER_UNDER else mt


def normalize_frame(df, league, book_hint, src_file):
    """Raw scraper CSV rows (either schema) -> the lake's row shape.

    `df` must have been read with dtype=str and na_filter=False so blanks stay
    blanks. `book_hint` and `src_file` may be scalars or per-row Series, which
    is what lets ingest normalize a whole batch of files in one pass.
    Everything here is vectorized: batches run to millions of rows.
    """
    thirteen = "player_name" in df.columns and "event_name" in df.columns

    if thirteen:
        ev_raw = df["matched_event_name"].where(
            df["matched_event_name"].ne(""), df["event_name"])
        player = df["matched_player_name"].where(
            df["matched_player_name"].ne(""), df["player_name"])
        team = df["matched_team_name"].where(
            df["matched_team_name"].ne(""), df["team_name"])
    else:
        ev_raw = df["matched_event_name"]
        player = df["matched_player_name"]
        team = df["matched_team_name"]
    ev_raw = ev_raw.fillna("").str.strip()
    player = player.fillna("").str.strip()
    team = team.fillna("").str.strip()

    out = pd.DataFrame({
        "league": league,
        "book": df["book"].where(df["book"].ne(""), book_hint),
        "ts": parse_ts(df["ts"]),
        "event_start": parse_event_start(df["event_start"]),
        "event_name": ev_raw,
        "market": df["market_name"].fillna("").str.strip(),
        "player": player,
        "american": pd.to_numeric(df["american_odds"], errors="coerce"),
        "line": pd.to_numeric(df["market_value"], errors="coerce"),
        "src_file": src_file,
    }, index=df.index)

    # "AWY @ HOM" -> the two sides. MLB arrives three ways: abbreviations in
    # the 2025 files, full club names since, and hardrockbet's "HOME vs AWAY"
    # nicknames. All three fold onto the abbreviation.
    at_game = ev_raw.str.contains(" @ ", regex=False)
    vs_game = ~at_game & ev_raw.str.contains(" vs ", regex=False)

    at = ev_raw.str.split(" @ ", n=1, expand=True)
    if at.shape[1] < 2:
        at[1] = None
    away = at[0].where(at_game)
    home = at[1].where(at_game)

    if vs_game.any():
        # Note the order: this format puts the home team first.
        vs = ev_raw.str.split(" vs ", n=1, expand=True)
        if vs.shape[1] < 2:
            vs[1] = None
        home = home.fillna(vs[0].where(vs_game))
        away = away.fillna(vs[1].where(vs_game))
        # Put the reversed names back in everyone else's order, so a digest
        # doesn't show the same game two ways. Names that already read
        # "away @ home" are left exactly as the book wrote them.
        out["event_name"] = ev_raw.mask(
            vs_game, vs[1].fillna("").str.strip() + " @ "
            + vs[0].fillna("").str.strip())

    is_game = at_game | vs_game
    away = away.fillna("").str.strip()
    home = home.fillna("").str.strip()
    if league == "mlb":
        away = away.replace(MLB_ABBREV).replace(MLB_NICKNAME)
        home = home.replace(MLB_ABBREV).replace(MLB_NICKNAME)
        team = team.replace(MLB_ABBREV).replace(MLB_NICKNAME)
    out["away"] = away.where(is_game, None)
    out["home"] = home.where(is_game, None)

    # event_start is what makes a game day; ts is only when we looked.
    out["event_date"] = et_date(out["event_start"], league).astype(str)

    mtype = df["market_type"].fillna("").str.strip().str.lower()
    over_under = mtype.isin(OVER_UNDER)
    # market_type carries the shot type on first-basket-exact, but over/under
    # on totals — that belongs in `side`, not as a subtype of the market.
    out["subtype"] = mtype.mask(over_under, "")
    out["side"] = (mtype.where(over_under)
                   .fillna(player.replace("", None))
                   .fillna(team.replace("", None))
                   .fillna(out["market"]))

    out = out[out["american"].notna() & out["ts"].notna()].copy()
    if out.empty:
        return out

    a = out["american"].astype("float64")
    out["decimal"] = np.where(a > 0, a / 100.0 + 1.0, 100.0 / np.abs(a) + 1.0)
    out["imp_prob"] = 1.0 / out["decimal"]

    if league in FIELD_LEAGUES:
        # A tournament has no matchup, so it keys on its own name.
        out["event_key"] = league + "|" + out["event_name"].map(slugify)
    else:
        game = out["away"].notna() & out["home"].notna()
        out["event_key"] = (
            league + "|" + out["event_date"] + "|"
            + out["away"].fillna("") + "@" + out["home"].fillna("")
        ).where(game, league + "|" + out["event_name"].map(slugify))

    out["outcome_key"] = (out["event_key"] + "|" + out["market"] + "|"
                          + out["side"].astype(str) + "|" + out["subtype"])
    out["american"] = out["american"].astype("int32")
    return out


LAKE_COLUMNS = ["league", "book", "ts", "event_start", "event_date",
                "event_name", "away", "home", "market", "subtype", "line",
                "side", "player", "american", "decimal", "imp_prob",
                "event_key", "outcome_key", "src_file"]


# --- lake reading ---------------------------------------------------------

def lake_dir(lake=None):
    return os.path.join(lake or LAKE_DEFAULT, "lake")


def _as_utc(value, end=False):
    """Accept "today", "24h", "14d", "2026-07-31", or a datetime -> UTC stamp."""
    if value is None:
        return None
    if isinstance(value, (dt.datetime, pd.Timestamp)):
        t = pd.Timestamp(value)
        return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    s = str(value).strip().lower()
    now = pd.Timestamp.now(tz="UTC")
    if s == "today":
        base = now.tz_convert("America/New_York").normalize()
        return (base + pd.Timedelta(days=1) if end else base).tz_convert("UTC")
    m = re.match(r"^(\d+(?:\.\d+)?)([hd])$", s)
    if m:
        span = pd.Timedelta(hours=float(m.group(1))) if m.group(2) == "h" \
            else pd.Timedelta(days=float(m.group(1)))
        return now - span
    try:
        t = pd.Timestamp(s)
    except ValueError:
        die("cannot read %r as a date — try 2026-07-31, today, 24h, or 14d"
            % value)
    if t.tzinfo is None:
        t = t.tz_localize("America/New_York")
    if end and t == t.normalize():
        t = t + pd.Timedelta(days=1)
    return t.tz_convert("UTC")


def resolve_date(value):
    """A game day as "YYYY-MM-DD" in ET. Accepts "today" or an ISO date."""
    return str(_as_utc(value).tz_convert("America/New_York").date())


def _ym_range(start, end):
    """The YYYY-MM partitions a [start, end] window can touch."""
    if start is None or end is None:
        return None
    months, cur = [], pd.Timestamp(start).tz_convert("UTC").normalize()
    stop = pd.Timestamp(end).tz_convert("UTC")
    cur = cur.replace(day=1)
    while cur <= stop:
        months.append(cur.strftime("%Y-%m"))
        cur = (cur + pd.Timedelta(days=32)).replace(day=1)
    # A game late in a month can be quoted from the month before.
    first = pd.Timestamp(months[0] + "-01", tz="UTC") - pd.Timedelta(days=1)
    return sorted(set(months + [first.strftime("%Y-%m")]))


def load(league, start=None, end=None, dates=None, markets=None, books=None,
         player=None, team=None, columns=None, include_dead=True, lake=None):
    """Read a slice of the lake into a DataFrame.

    Filters are pushed down to the parquet scan, so a day of one market reads
    a few MB out of a multi-GB lake. `start`/`end` bound the snapshot time
    (`ts`); `dates` bounds the game day (`event_date`) and takes one date or a
    list. `player` matches case-insensitively on a substring, since canonical
    names drift between books.
    """
    if league not in LEAGUES:
        die("unknown league %r — one of %s" % (league, ", ".join(LEAGUES)))
    root = os.path.join(lake_dir(lake), "league=%s" % league)
    if not os.path.isdir(root):
        die("no data for %s at %s — run: python3 ingest.py --league %s"
            % (league, root, league))

    start_ts, end_ts = _as_utc(start), _as_utc(end, end=True)
    dataset = ds.dataset(root, format="parquet", partitioning="hive")

    expr = None

    def keep(e):
        return e if expr is None else (expr & e)

    months = _ym_range(start_ts, end_ts)
    if months and "ym" in dataset.schema.names:
        expr = keep(pc.field("ym").isin(months))
    if start_ts is not None:
        expr = keep(pc.field("ts") >= pa.scalar(start_ts.to_pydatetime()))
    if end_ts is not None:
        expr = keep(pc.field("ts") <= pa.scalar(end_ts.to_pydatetime()))
    if dates:
        want = [dates] if isinstance(dates, str) else list(dates)
        expr = keep(pc.field("event_date").isin([str(d) for d in want]))
    if markets:
        want = [markets] if isinstance(markets, str) else list(markets)
        expr = keep(pc.field("market").isin(want))
    if books:
        want = [books] if isinstance(books, str) else list(books)
        expr = keep(pc.field("book").isin(want))
    if not include_dead:
        expr = keep(~pc.field("book").isin(list(DEAD_BOOKS)))

    cols = list(columns) if columns else None
    if cols:
        # The name filters below run after the scan, so their columns have to
        # survive it even when the caller didn't ask for them.
        needed = (["player"] if player is not None else []) + (
            ["away", "home", "side"] if team is not None else [])
        cols += [c for c in needed if c not in cols]
    table = dataset.to_table(filter=expr, columns=cols)
    df = table.to_pandas()

    # Substring name matching can't be pushed down, so it runs after the scan.
    if player is not None and not df.empty:
        df = df[df["player"].fillna("").str.lower()
                .str.contains(str(player).lower(), regex=False)]
    if team is not None and not df.empty:
        t = str(team).upper()
        df = df[(df["away"].fillna("").str.upper() == t)
                | (df["home"].fillna("").str.upper() == t)
                | (df["side"].fillna("").str.upper() == t)]
    return df.reset_index(drop=True)


def lake_freshness(league, lake=None):
    """Newest snapshot time in the lake for a league, or None if it's empty."""
    root = os.path.join(lake_dir(lake), "league=%s" % league)
    if not os.path.isdir(root):
        return None
    table = ds.dataset(root, format="parquet",
                       partitioning="hive").to_table(columns=["ts"])
    if table.num_rows == 0:
        return None
    return pd.Timestamp(pc.max(table["ts"]).as_py())


# --- market structure -----------------------------------------------------

def consensus_event_start(df):
    """Modal `event_start` per event — one book's wrong clock can't move it."""
    if df.empty:
        return pd.Series(dtype="datetime64[us, UTC]")
    modes = df.groupby("event_key")["event_start"].agg(
        lambda s: s.mode().iloc[0] if not s.mode().empty else s.min())
    return modes


def snap_open_close(df, open_days=3.0, grace_min=0.0):
    """First and last quote per (outcome, book, line) around the event start.

    Close is the last snapshot at or before the consensus start; open is the
    first one within `open_days` before it. Both use the consensus start, so a
    book with a bad clock still gets cut at the right moment.
    """
    if df.empty:
        return df.copy()
    starts = consensus_event_start(df)
    d = df.copy()
    d["start_c"] = d["event_key"].map(starts)
    lo = d["start_c"] - pd.Timedelta(days=float(open_days))
    hi = d["start_c"] + pd.Timedelta(minutes=float(grace_min))
    d = d[(d["ts"] >= lo) & (d["ts"] <= hi)]
    if d.empty:
        return d

    keys = ["outcome_key", "book"]
    d = d.sort_values("ts")
    first = d.groupby(keys, as_index=False).first()
    last = d.groupby(keys, as_index=False).last()
    cols = ["american", "decimal", "imp_prob", "line", "ts"]
    out = first.merge(last[keys + cols], on=keys, suffixes=("_open", "_close"))
    keep = ["outcome_key", "book", "league", "event_key", "event_date",
            "event_name", "market", "subtype", "side", "player", "start_c"]
    keep = [c for c in keep if c in out.columns]
    return out[keep + [c + "_open" for c in cols] + [c + "_close" for c in cols]]


def devig_two_way(df):
    """Fair probability and hold for the two-sided markets.

    Pairs the two sides a book shows at one instant — both teams on a
    moneyline or spread, over and under on a total — and strips the vig
    proportionally. Rows whose partner is missing from that snapshot are
    dropped, since a one-sided quote says nothing about the hold.
    """
    if df.empty:
        return df.copy()
    d = df[df["market"].isin(TWO_WAY)].copy()
    if d.empty:
        return d
    # A total is only two-sided at one number; a spread pairs across its sign.
    d["_pair"] = d["line"].abs().fillna(-1.0)
    keys = ["book", "ts", "event_key", "market", "_pair"]
    grp = d.groupby(keys)["imp_prob"]
    d["_sum"] = grp.transform("sum")
    d["_n"] = grp.transform("size")
    d = d[d["_n"] == 2].copy()
    if d.empty:
        return d
    d["fair_prob"] = d["imp_prob"] / d["_sum"]
    d["overround"] = d["_sum"] - 1.0
    # Hold is the share of handle the book keeps, not the raw overround:
    # a -110/-110 total is 4.76% overround but the familiar 4.55% hold.
    d["hold"] = d["overround"] / d["_sum"]
    d["fair_american"] = [prob_to_american(p) for p in d["fair_prob"]]
    return d.drop(columns=["_pair", "_sum", "_n"])


def market_class(market):
    """Which devig rule a market obeys — see the constants above."""
    m = str(market)
    if m in TWO_WAY:
        return "two_way"
    if m in COMPLEMENT_PAIRS:
        return "complement"
    if m in EXCLUSIVE_FIELDS:
        return "exclusive"
    return "independent"


def devig_complement(df):
    """Fair price for a yes/no pair the scraper files under two market names."""
    if df.empty:
        return df.copy()
    d = df[df["market"].isin(COMPLEMENT_PAIRS)].copy()
    if d.empty:
        return d
    # Both names map to one question, so they group together.
    d["_pair"] = [min(m, COMPLEMENT_PAIRS[m]) for m in d["market"]]
    keys = ["book", "ts", "event_key", "_pair"]
    grp = d.groupby(keys)["imp_prob"]
    d["_sum"] = grp.transform("sum")
    d["_n"] = grp.transform("size")
    d = d[d["_n"] == 2].copy()
    if d.empty:
        return d
    d["fair_prob"] = d["imp_prob"] / d["_sum"]
    d["overround"] = d["_sum"] - 1.0
    d["hold"] = d["overround"] / d["_sum"]
    d["fair_american"] = [prob_to_american(p) for p in d["fair_prob"]]
    return d.drop(columns=["_pair", "_sum", "_n"])


def devig_field(df):
    """Fair probability and overround for a field where one entrant wins.

    Every player on first basket, every golfer on the outright. Normalizes one
    book's whole field at one instant, so it only means anything when the book
    listed the full field — a filtered slice reads as far too confident.

    Markets that merely look like fields are excluded: several hitters can
    homer in the same game, so "home run 1+" is a stack of independent yes/no
    props, not a field, and forcing it to sum to 1 would be wrong.

    The field spans every subtype, so subtype is *not* part of the key. On
    "first basket type" the fg2 and fg3 prices are alternatives to each other
    and only sum to 1 together; splitting them would price each as its own
    market and report a negative hold.
    """
    if df.empty:
        return df.copy()
    d = df[df["market"].isin(EXCLUSIVE_FIELDS)].copy()
    if d.empty:
        return d
    keys = ["book", "ts", "event_key", "market"]
    grp = d.groupby(keys, dropna=False)["imp_prob"]
    d["_sum"] = grp.transform("sum")
    d["_n"] = grp.transform("size")
    d = d[d["_n"] >= 2].copy()
    if d.empty:
        return d
    d["fair_prob"] = d["imp_prob"] / d["_sum"]
    d["overround"] = d["_sum"] - 1.0
    d["hold"] = d["overround"] / d["_sum"]
    d["fair_american"] = [prob_to_american(p) for p in d["fair_prob"]]
    return d.drop(columns=["_sum", "_n"])


def consensus_independent(df, bucket_min=60):
    """A reference price for standalone yes/no props, from the other books.

    These can't be devigged: one side of "home run 1+" says nothing about the
    margin baked into it. What can be said is how this price compares to what
    everyone else is charging, so the reference is the median implied
    probability across books — vig still in it, and labelled that way. Because
    both sides of the comparison carry vig, an edge measured against it
    understates rather than flatters.

    Books don't scrape in lockstep, so quotes are bucketed into `bucket_min`
    windows to be compared. Pass bucket_min=None when the frame is already one
    snapshot per book, as a board is.
    """
    if df.empty:
        return df.copy()
    d = df[[market_class(m) == "independent" for m in df["market"]]].copy()
    if d.empty:
        return d
    if bucket_min:
        d["_bucket"] = d["ts"].dt.floor("%dmin" % int(bucket_min))
        keys = ["outcome_key", "_bucket"]
    else:
        keys = ["outcome_key"]
    grp = d.groupby(keys)["imp_prob"]
    d["fair_prob"] = grp.transform("median")
    d["_n"] = grp.transform("size")
    d = d[d["_n"] >= 2].copy()
    if d.empty:
        return d
    d["overround"] = float("nan")
    d["hold"] = float("nan")
    d["fair_american"] = [prob_to_american(p) for p in d["fair_prob"]]
    return d.drop(columns=[c for c in ("_n", "_bucket") if c in d.columns])


def fair_prices(df, bucket_min=60):
    """A reference probability for every market present, by its own rule.

    Adds `basis`, naming how each row's number was reached, because they are
    not equally strong: the devigged ones are true no-vig prices, the
    independent ones are only a comparison against the rest of the market.
    Pass bucket_min=None when the frame is a board (one row per book).
    """
    if df.empty:
        return df.copy()
    parts = []
    for fn, basis in ((devig_two_way, "no-vig (two-way)"),
                      (devig_complement, "no-vig (pair)"),
                      (devig_field, "no-vig (field)")):
        got = fn(df)
        if not got.empty:
            parts.append(got.assign(basis=basis))
    got = consensus_independent(df, bucket_min=bucket_min)
    if not got.empty:
        parts.append(got.assign(basis="market median (vig incl.)"))
    if not parts:
        return df.iloc[0:0].copy()
    return pd.concat(parts, ignore_index=True)


# --- the board ------------------------------------------------------------

def main_line(df):
    """Keep each book's primary spread/total, dropping the alternate ladder.

    Books post a whole ladder at once — one snapshot can hold DAL at -15.5
    (+575) through +8.5 (-525), 24 rows, all the same `outcome_key` because
    the key deliberately ignores the number. Left in, the ladder wrecks
    everything downstream: the longest alternate looks like the best price on
    the board, and consecutive quotes appear to swing twenty points as they
    hop between rungs.

    The primary number is the rung whose two sides are priced most evenly
    against each other — that's what "the spread" means, the number chosen to
    split the action. Judging a side on its own doesn't work: on this ladder
    -3.5 at +100 looks more even than -3.0 at -110, but the pair -3.0/-110
    against +3.0/-110 is the balanced one. Rungs quoted on one side only fall
    back to whichever sits closest to even money.
    """
    if df.empty:
        return df.copy()
    laddered = df["market"].isin(LINE_MARKETS) & df["line"].notna()
    if not laddered.any():
        return df.copy()

    rungs = df[laddered].copy()
    # A spread pairs across its sign (-3.0 with +3.0); a total pairs over
    # with under at the same number. Both group on the magnitude.
    rungs["_rung"] = rungs["line"].abs()
    book_keys = [k for k in ("book", "ts", "event_key", "market", "subtype")
                 if k in rungs.columns]
    rung_keys = book_keys + ["_rung"]

    by_rung = rungs.groupby(rung_keys)["imp_prob"]
    spread_of_rung = by_rung.transform("max") - by_rung.transform("min")
    one_sided = by_rung.transform("size") < 2
    # One side only: fall back to distance from even money.
    rungs["_imbalance"] = spread_of_rung.where(
        ~one_sided, (rungs["imp_prob"] - 0.5).abs() * 2)

    per_rung = rungs.groupby(rung_keys, as_index=False)["_imbalance"].min()
    winner = per_rung.loc[per_rung.groupby(book_keys)["_imbalance"].idxmin()]
    primary = rungs.merge(winner[rung_keys], on=rung_keys, how="inner").drop(
        columns=["_rung", "_imbalance"])
    return (pd.concat([df[~laddered], primary], ignore_index=True)
            .sort_values("ts").reset_index(drop=True))


def pregame_only(df, grace_min=0.0):
    """Drop quotes taken after the ball was in the air.

    The scraper keeps running through a game, and live prices are a different
    animal: a hitter's home-run price lengthens every inning he doesn't go
    deep, so the last quote of the day is an in-play number, not the close.
    Anything comparing days or measuring a close has to cut here first.
    """
    if df.empty:
        return df.copy()
    starts = consensus_event_start(df)
    cutoff = df["event_key"].map(starts) + pd.Timedelta(minutes=float(grace_min))
    return df[df["ts"] <= cutoff].copy()


def latest_board(df, stale_min=90, main_only=True):
    """The current price per (outcome, book): each book's newest live quote.

    A book that stopped quoting hours ago is dropped rather than shown as
    current — `stale_min` is measured against the newest snapshot in the
    slice, not the wall clock, so this behaves the same on historical data.

    Spreads and totals are cut to the main number first; comparing a book's
    main line against another's alternate rung isn't line shopping.
    """
    if df.empty:
        return df.copy()
    if main_only:
        df = main_line(df)
    newest = df["ts"].max()
    d = df[df["ts"] >= newest - pd.Timedelta(minutes=float(stale_min))]
    if d.empty:
        return d.copy()
    return (d.sort_values("ts").groupby(["outcome_key", "book"], as_index=False)
            .last())


def best_price(board):
    """Flag the longest price per outcome and name the book holding it."""
    if board.empty:
        return board.copy()
    d = board.copy()
    idx = d.groupby("outcome_key")["decimal"].transform("max")
    d["is_best"] = d["decimal"] >= idx - 1e-9
    # Ties go to the alphabetically first book so runs are reproducible.
    winner = (d[d["is_best"]].sort_values(["outcome_key", "book"])
              .groupby("outcome_key", as_index=False).first()
              [["outcome_key", "book", "american"]]
              .rename(columns={"book": "best_book", "american": "best_odds"}))
    return d.merge(winner, on="outcome_key", how="left")


def books_json(rows, stake="1.0u"):
    """The `books` array build_card.py expects, longest price first.

    Every entry needs a stake or pick_best_book() refuses the card, so a
    placeholder goes in and the real sizing is the user's call.
    """
    out = []
    for _, r in rows.sort_values("decimal", ascending=False).iterrows():
        out.append({"book": str(r["book"]).upper(),
                    "odds": fmt_american(r["american"]),
                    "stake": stake})
    if out:
        out[0]["best"] = True
    return out


# --- output ---------------------------------------------------------------

def md_table(df, cols=None, floatfmt="%.4g"):
    """A markdown table — small enough to hand-roll, no dependency needed."""
    cols = list(cols or df.columns)
    if df.empty:
        return "_(nothing to show)_"

    def cell(v):
        if v is None or (isinstance(v, float) and v != v):
            return ""
        if isinstance(v, float):
            return floatfmt % v
        return str(v).replace("|", "\\|")

    lines = ["| " + " | ".join(cols) + " |",
             "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(cell(r.get(c)) for c in cols) + " |")
    return "\n".join(lines)


def write_outputs(frames, digest, stem, out_dir=None):
    """Write the tidy CSVs and the digest, and print the digest to stdout."""
    out_dir = out_dir or OUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for name, frame in (frames or {}).items():
        path = os.path.join(out_dir, "%s_%s.csv" % (stem, name))
        frame.to_csv(path, index=False)
        written.append(path)
    text = "\n".join(digest) if isinstance(digest, (list, tuple)) else str(digest)
    md_path = os.path.join(out_dir, "%s.md" % stem)
    with open(md_path, "w") as fh:
        fh.write(text.rstrip() + "\n")
    print(text.rstrip())
    print("\nwrote %s" % ", ".join(
        os.path.relpath(p, ROOT) for p in written + [md_path]))
    return md_path


def write_card(card, out_dir=None):
    """Drop a build_card.py config into out/cards/."""
    out_dir = out_dir or os.path.join(OUT_DIR, "cards")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, card["slug"] + ".json")
    with open(path, "w") as fh:
        json.dump(card, fh, indent=2)
        fh.write("\n")
    return path


def freshness_line(league, lake=None):
    """One line on how current the lake is, for the top of every digest."""
    newest = lake_freshness(league, lake)
    if newest is None:
        return "_lake has no %s data — run `python3 ingest.py --league %s`_" % (
            league, league)
    age = pd.Timestamp.now(tz="UTC") - newest
    mins = int(age.total_seconds() // 60)
    stamp = newest.tz_convert("America/New_York").strftime("%Y-%m-%d %H:%M ET")
    warn = "  **stale — run `python3 ingest.py`**" if mins > 90 else ""
    return "_Lake current through %s (%dm ago).%s_" % (stamp, mins, warn)


# --- self-test ------------------------------------------------------------

def _check(label, got, want):
    ok = got == want if not isinstance(want, float) else abs(got - want) < 1e-6
    print("%-46s %s" % (label, "ok" if ok else "FAIL got=%r want=%r" % (got, want)))
    return ok


def _self_test():
    ok = True
    ok &= _check("american_to_decimal(+150)", american_to_decimal(150), 2.5)
    ok &= _check("american_to_decimal(-110)",
                 round(american_to_decimal(-110), 6), 1.909091)
    ok &= _check("decimal_to_american(2.5)", decimal_to_american(2.5), 150)
    ok &= _check("decimal_to_american(1.909091)",
                 decimal_to_american(1.909091), -110)
    ok &= _check("round-trip +323", decimal_to_american(
        american_to_decimal(323)), 323)
    ok &= _check("round-trip -810", decimal_to_american(
        american_to_decimal(-810)), -810)
    ok &= _check("american_to_prob(+100)", american_to_prob(100), 0.5)
    ok &= _check("prob_to_american(0.5)", prob_to_american(0.5), 100)
    ok &= _check("fmt_american(323)", fmt_american(323), "+323")
    ok &= _check("fmt_american(-110)", fmt_american(-110), "-110")

    # The three event_start shapes for the same night must agree once in UTC.
    parsed = parse_event_start(pd.Series([
        "2025-04-15T23:30:00.000Z", "2025-04-15 23:30:00",
        "2025-04-15 19:30:00-04:00", "",
    ]))
    ok &= _check("event_start: Z form", str(parsed.iloc[0]),
                 "2025-04-15 23:30:00+00:00")
    ok &= _check("event_start: naive form", str(parsed.iloc[1]),
                 "2025-04-15 23:30:00+00:00")
    ok &= _check("event_start: ET-offset form", str(parsed.iloc[2]),
                 "2025-04-15 23:30:00+00:00")
    ok &= _check("event_start: all three agree",
                 parsed.iloc[:3].nunique(), 1)
    ok &= _check("event_start: blank -> NaT", pd.isna(parsed.iloc[3]), True)
    # The oldest files store the same instant as epoch milliseconds.
    epochs = parse_event_start(pd.Series(["1744759800000", "1744759800",
                                          "2025-04-15T23:30:00.000Z"]))
    ok &= _check("event_start: epoch millis", str(epochs.iloc[0]),
                 "2025-04-15 23:30:00+00:00")
    ok &= _check("event_start: epoch seconds", str(epochs.iloc[1]),
                 "2025-04-15 23:30:00+00:00")
    ok &= _check("event_start: epoch matches ISO", epochs.nunique(), 1)
    ok &= _check("et_date of 23:30Z is that evening",
                 str(et_date(parsed[:1], "wnba").iloc[0]), "2025-04-15")

    # MLB's two naming eras must land on one key.
    ok &= _check("norm_team full name", norm_team("Chicago Cubs", "mlb"), "CHC")
    ok &= _check("norm_team already abbrev", norm_team("CHC", "mlb"), "CHC")
    ok &= _check("split_event mlb full",
                 split_event("New York Yankees @ Chicago Cubs", "mlb"),
                 ("NYY", "CHC"))
    ok &= _check("split_event wnba abbrev",
                 split_event("SEA @ ATL", "wnba"), ("SEA", "ATL"))
    ok &= _check("MLB_ABBREV covers 30 clubs", len(MLB_ABBREV), 30)
    ok &= _check("MLB_NICKNAME covers 30 clubs", len(MLB_NICKNAME), 30)
    ok &= _check("nicknames and full names agree",
                 sorted(set(MLB_NICKNAME.values())),
                 sorted(set(MLB_ABBREV.values())))

    # hardrockbet's old MLB naming: nicknames, and home team first. It has to
    # land on the same event_key as everyone else's away-first full names.
    raw_cols = {
        "event_start": ["2026-07-28 19:30:00-04:00"] * 3,
        "market_name": ["moneyline"] * 3, "market_type": [""] * 3,
        "market_value": [""] * 3, "american_odds": ["-145", "120", "-145"],
        "matched_team_name": ["Boston Red Sox", "Athletics", "Red Sox"],
        "matched_player_name": [""] * 3,
        "matched_event_name": ["Boston Red Sox @ Athletics",
                               "Boston Red Sox @ Athletics",
                               "Athletics vs Red Sox"],
        "book": ["draftkings", "draftkings", "hardrockbet"],
        "ts": ["2026-07-28 18:00:00.000000+00:00"] * 3,
    }
    norm = normalize_frame(pd.DataFrame(raw_cols), "mlb", "x", "t.csv")
    ok &= _check("reversed naming lands on one event_key",
                 norm["event_key"].nunique(), 1)
    ok &= _check("reversed naming keeps BOS away",
                 norm["event_key"].iloc[2], "mlb|2026-07-28|BOS@ATH")
    ok &= _check("reversed naming is re-ordered for display",
                 norm["event_name"].iloc[2], "Red Sox @ Athletics")
    ok &= _check("reversed naming maps the team too",
                 norm["side"].iloc[2], "BOS")
    ok &= _check("standard naming is left alone",
                 norm["event_name"].iloc[0], "Boston Red Sox @ Athletics")

    ek = make_event_key("wnba", "2026-07-31", "SEA", "ATL", "SEA @ ATL")
    ok &= _check("event_key", ek, "wnba|2026-07-31|SEA@ATL")
    ok &= _check("event_key mlb G2", make_event_key(
        "mlb", "2026-07-31", "NYY", "CHC", "x", game_no=2),
        "mlb|2026-07-31|NYY@CHC|G2")
    ok &= _check("event_key pga", make_event_key(
        "pga", None, None, None, "Genesis Scottish Open"),
        "pga|genesis-scottish-open")
    ok &= _check("outcome_key moneyline", make_outcome_key(ek, "moneyline", "SEA"),
                 "wnba|2026-07-31|SEA@ATL|moneyline|SEA|")
    ok &= _check("outcome_key total over",
                 make_outcome_key(ek, "total", "over"),
                 "wnba|2026-07-31|SEA@ATL|total|over|")
    ok &= _check("outcome_key first basket exact",
                 make_outcome_key(ek, "first basket exact", "Angel Reese",
                                  "layup"),
                 "wnba|2026-07-31|SEA@ATL|first basket exact|Angel Reese|layup")
    ok &= _check("spread key ignores the number",
                 make_outcome_key(ek, "spread", "SEA")
                 == make_outcome_key(ek, "spread", "SEA"), True)

    ok &= _check("derive_side total -> over", derive_side(
        "total", "over", "", "", "wnba"), "over")
    ok &= _check("derive_side prop -> player", derive_side(
        "first basket", "", "", "Angel Reese", "wnba"), "Angel Reese")
    ok &= _check("derive_side spread -> team", derive_side(
        "spread", "", "SEA", "", "wnba"), "SEA")
    ok &= _check("derive_side YRFI -> market", derive_side(
        "YRFI", "", "", "", "mlb"), "YRFI")
    ok &= _check("derive_subtype drops over/under",
                 derive_subtype("over"), "")
    ok &= _check("derive_subtype keeps shot type",
                 derive_subtype("layup"), "layup")

    # Devig: a -110/-110 total holds ~4.5% and splits 50/50 once stripped.
    total = pd.DataFrame({
        "book": ["dk", "dk"], "ts": [pd.Timestamp("2026-07-31", tz="UTC")] * 2,
        "event_key": [ek, ek], "market": ["total", "total"],
        "line": [178.5, 178.5], "side": ["over", "under"],
        "american": [-110, -110],
        "imp_prob": [american_to_prob(-110)] * 2,
    })
    dv = devig_two_way(total)
    ok &= _check("devig_two_way fair sums to 1",
                 round(dv["fair_prob"].sum(), 9), 1.0)
    ok &= _check("devig_two_way fair is 50/50",
                 round(dv["fair_prob"].iloc[0], 6), 0.5)
    ok &= _check("devig_two_way overround on -110/-110",
                 round(dv["overround"].iloc[0], 4), 0.0476)
    ok &= _check("devig_two_way hold on -110/-110",
                 round(dv["hold"].iloc[0], 4), 0.0455)

    field = pd.DataFrame({
        "book": ["dk"] * 3, "ts": [pd.Timestamp("2026-07-31", tz="UTC")] * 3,
        "event_key": [ek] * 3, "market": ["first basket"] * 3,
        "subtype": [""] * 3, "side": ["A", "B", "C"],
        "american": [500, 600, 700],
        "imp_prob": [american_to_prob(x) for x in (500, 600, 700)],
    })
    fv = devig_field(field)
    ok &= _check("devig_field fair sums to 1",
                 round(fv["fair_prob"].sum(), 9), 1.0)
    ok &= _check("devig_field longest price is least likely",
                 fv.sort_values("american")["fair_prob"].is_monotonic_decreasing,
                 True)

    ok &= _check("market_class moneyline", market_class("moneyline"), "two_way")
    ok &= _check("market_class first basket",
                 market_class("first basket"), "exclusive")
    ok &= _check("market_class YRFI", market_class("YRFI"), "complement")
    # The one that matters: several hitters can homer in the same game, so
    # this is not a field and must never be normalized to sum to 1.
    ok &= _check("market_class home run 1+",
                 market_class("home run 1+"), "independent")
    ok &= _check("market_class tournament winner",
                 market_class("tournament winner"), "exclusive")
    # Two rosters in one market, with no column saying who's on which.
    ok &= _check("market_class first basket by team",
                 market_class("first basket by team"), "independent")

    # fg2 and fg3 are alternatives within one field; devigging them apart
    # would make each look like its own market.
    shot = pd.DataFrame({
        "book": ["dk"] * 2, "ts": [pd.Timestamp("2026-07-31", tz="UTC")] * 2,
        "event_key": [ek] * 2, "market": ["first basket type"] * 2,
        # Real draftkings numbers: together they sum to 1.07, apart they don't.
        "subtype": ["fg2", "fg3"], "side": ["first basket type"] * 2,
        "american": [-361, 245],
        "imp_prob": [american_to_prob(-361), american_to_prob(245)],
    })
    sv = devig_field(shot)
    ok &= _check("devig_field spans subtypes", len(sv), 2)
    ok &= _check("devig_field subtype fair sums to 1",
                 round(sv["fair_prob"].sum(), 9), 1.0)
    ok &= _check("devig_field subtype hold is positive",
                 bool(sv["hold"].iloc[0] > 0), True)

    # A lineup's worth of HR props sums well past 1 — devig_field must not
    # touch them, or every price would read a third too long.
    hr = pd.DataFrame({
        "book": ["dk"] * 4, "ts": [pd.Timestamp("2026-07-31", tz="UTC")] * 4,
        "event_key": [ek] * 4, "market": ["home run 1+"] * 4,
        "subtype": [""] * 4, "side": list("ABCD"),
        "outcome_key": ["k1", "k2", "k3", "k4"],
        "american": [400, 450, 500, 550],
        "imp_prob": [american_to_prob(x) for x in (400, 450, 500, 550)],
    })
    ok &= _check("devig_field ignores home run props",
                 devig_field(hr).empty, True)
    hr2 = pd.concat([hr, hr.assign(book="mgm", american=hr["american"] + 50,
                                   imp_prob=[american_to_prob(x + 50)
                                             for x in hr["american"]])])
    ci = consensus_independent(hr2, bucket_min=None)
    ok &= _check("consensus_independent keeps both books", len(ci), 8)
    ok &= _check("consensus_independent stays near the quotes",
                 bool(0.9 * hr2["imp_prob"].min() <= ci["fair_prob"].min()
                      <= hr2["imp_prob"].max()), True)

    yrfi = pd.DataFrame({
        "book": ["dk", "dk"], "ts": [pd.Timestamp("2026-07-31", tz="UTC")] * 2,
        "event_key": [ek, ek], "market": ["YRFI", "NRFI"],
        "side": ["YRFI", "NRFI"], "american": [-135, 110],
        "imp_prob": [american_to_prob(-135), american_to_prob(110)],
    })
    cp = devig_complement(yrfi)
    ok &= _check("devig_complement pairs YRFI with NRFI", len(cp), 2)
    ok &= _check("devig_complement fair sums to 1",
                 round(cp["fair_prob"].sum(), 9), 1.0)

    # A real fanatics ladder: 24 alternate spreads in one snapshot, all one
    # outcome_key. Only the rung nearest even money is "the" spread.
    ladder_prices = [575, 500, 460, 410, 340, 290, 250, 220, 180, 155, 130,
                     110, 100, -110, -120, -140, -170, -200, -225, -255,
                     -300, -375, -425, -525]
    ladder_lines = [-15.5, -14.5, -13.5, -12.5, -11.5, -10.5, -9.5, -8.5,
                    -7.5, -6.5, -5.5, -4.5, -3.5, -3.0, -2.5, -1.5, 1.5, 2.5,
                    3.5, 4.5, 5.5, 6.5, 7.5, 8.5]
    # WAS is the mirror side, so each rung has the pair main_line needs.
    was_lines = [-x for x in ladder_lines]
    was_prices = [-800, -700, -625, -550, -450, -380, -320, -270, -225, -190,
                  -160, -135, -120, -110, 100, 120, 145, 170, 190, 215, 250,
                  310, 350, 425]
    ladder = pd.DataFrame({
        "book": ["fanatics"] * 48,
        "ts": [pd.Timestamp("2026-07-31", tz="UTC")] * 48,
        "event_key": [ek] * 48, "market": ["spread"] * 48,
        "subtype": [""] * 48, "side": ["DAL"] * 24 + ["WAS"] * 24,
        "line": ladder_lines + was_lines,
        "american": ladder_prices + was_prices,
        "imp_prob": [american_to_prob(p)
                     for p in ladder_prices + was_prices],
    })
    picked = main_line(ladder)
    ok &= _check("main_line keeps one rung, both sides", len(picked), 2)
    ok &= _check("main_line picks the balanced number",
                 sorted(abs(v) for v in picked["line"]), [3.0, 3.0])
    ok &= _check("main_line drops the long alternates",
                 sorted(picked["american"]), [-110, -110])
    ok &= _check("main_line leaves moneylines alone",
                 len(main_line(total.assign(market="moneyline", line=None))), 2)

    board = pd.DataFrame({
        "outcome_key": ["k"] * 3, "book": ["betmgm", "betrivers", "fanatics"],
        "american": [750, 800, 700],
        "decimal": [american_to_decimal(x) for x in (750, 800, 700)],
    })
    bp = best_price(board)
    ok &= _check("best_price picks the longest", bp["best_book"].iloc[0],
                 "betrivers")
    bj = books_json(board)
    ok &= _check("books_json is best-first", bj[0],
                 {"book": "BETRIVERS", "odds": "+800", "stake": "1.0u",
                  "best": True})
    ok &= _check("books_json keeps every book", len(bj), 3)

    print("\n%s" % ("all checks passed" if ok else "SELF-TEST FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_self_test())
