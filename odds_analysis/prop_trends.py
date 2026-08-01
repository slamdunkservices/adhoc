#!/usr/bin/env python3
"""
One player's price, tracked over time.

Usage:
    python3 prop_trends.py mlb "Kyle Schwarber" --market "home run 1+" --since 14d
    python3 prop_trends.py wnba "Caitlin Clark" --market "first basket" --since 30d
    python3 prop_trends.py wnba "Angel Reese" --market "first basket" --intraday

By default it gives one row per game day: what each book closed at, the best
price available, and the market's own no-vig number. `--intraday` switches to
every snapshot of a single day, for watching a price move hour by hour.

Options:
    --market M      market name (default: the player's most-quoted one)
    --since SPAN    how far back: 14d, 30d (default 30d)
    --intraday      every snapshot instead of one row per day
    --date D        which day, with --intraday (default: today)
    --all-books     include books that have stopped reporting
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

import oddslib


def daily_trend(df, field=None):
    """Each book's closing price per game day, plus best and market fair.

    "Closing" means the last quote before the game started — see
    oddslib.pregame_only(). A substring player search can also match the same
    name in two events on one day, so each day keeps the best-covered one.

    `field` is the whole market — every player, not just this one. Fair prices
    have to be worked out across the full field, since one player's slice of a
    first-basket market sums to about a tenth and would devig to a certainty.
    """
    df = oddslib.pregame_only(df)
    if df.empty:
        return df
    field = df if field is None else oddslib.pregame_only(field)

    # One game per day: whichever event the books quoted most.
    coverage = (df.groupby(["event_date", "outcome_key"], as_index=False)
                .agg(n=("book", "nunique")))
    winner = (coverage.sort_values(["event_date", "n"], ascending=[True, False])
              .groupby("event_date", as_index=False).first()[["event_date",
                                                              "outcome_key"]])
    df = df.merge(winner, on=["event_date", "outcome_key"], how="inner")

    # Devig the whole field, then keep this player's rows out of the result.
    fair = oddslib.fair_prices(field)
    if not fair.empty:
        fair = fair[fair["outcome_key"].isin(set(df["outcome_key"]))]
    if not fair.empty:
        fair = (fair.groupby(["event_date", "outcome_key"], as_index=False)
                ["fair_prob"].median())

    daily = (df.sort_values("ts")
             .groupby(["event_date", "outcome_key", "book"], as_index=False)
             .last())
    daily = oddslib.best_price(daily)
    if not fair.empty:
        daily = daily.merge(fair, on=["event_date", "outcome_key"], how="left")
    else:
        daily["fair_prob"] = float("nan")
    return daily


def wide(daily, value="american"):
    """Days down the side, books across the top — the shape of a post."""
    table = daily.pivot_table(index="event_date", columns="book",
                              values=value, aggfunc="last")
    out = pd.DataFrame(index=table.index)
    for book in sorted(table.columns):
        out[book] = [oddslib.fmt_american(v) for v in table[book]]

    # Taken from the same rows the columns came from, so BEST always matches
    # a number actually shown on its row.
    top = daily.loc[daily.groupby("event_date")["decimal"].idxmax()]
    top = top.set_index("event_date")
    out["BEST"] = [oddslib.fmt_american(v)
                   for v in top["american"].reindex(out.index)]
    out["AT"] = top["book"].reindex(out.index).fillna("")
    if daily["fair_prob"].notna().any():
        fair = daily.groupby("event_date")["fair_prob"].median()
        out["FAIR"] = [oddslib.fmt_american(oddslib.prob_to_american(p))
                       if p == p else "" for p in fair.reindex(out.index)]
    return out.reset_index()


def main():
    argv = sys.argv[1:]
    args = [a for a in argv if not a.startswith("-")]
    if len(args) < 2 or "--help" in argv or "-h" in argv:
        sys.exit(__doc__.strip())

    def opt(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default

    league, player = args[0], args[1]
    lake = opt("--lake")
    intraday = "--intraday" in argv
    market = opt("--market")

    if intraday:
        date = opt("--date", "today")
        day = oddslib.resolve_date(date)
        df = oddslib.load(league, dates=[day], player=player,
                          markets=[market] if market else None,
                          include_dead="--all-books" in argv, lake=lake)
        label = day
    else:
        since = opt("--since", "30d")
        df = oddslib.load(league, start=since, player=player,
                          markets=[market] if market else None,
                          include_dead="--all-books" in argv, lake=lake)
        label = since

    stem = "trend-%s-%s-%s" % (league, oddslib.slugify(player), label)
    head = ["# %s — price history" % player, "",
            oddslib.freshness_line(league, lake), ""]

    if df.empty:
        oddslib.write_outputs({}, head + [
            "No quotes for %r in %s over %s. Check the spelling, widen "
            "`--since`, or run `python3 ingest.py`." % (player, league, label)
        ], stem)
        return

    if not market:
        market = df["market"].value_counts().index[0]
        df = df[df["market"] == market]
    # A substring search can catch two players; keep the one that was meant.
    if df["player"].nunique() > 1:
        who = df["player"].value_counts().index[0]
        head += ["_Matched %s (also saw: %s)._" % (
            who, ", ".join(sorted(set(df["player"]) - {who}))), ""]
        df = df[df["player"] == who]

    # The rest of the field, for devigging. Restricted to the games this
    # player actually appears in, so it stays a small read.
    field = oddslib.load(
        league, dates=sorted(set(df["event_date"])), markets=[market],
        include_dead="--all-books" in argv, lake=lake)
    field = field[field["event_key"].isin(set(df["event_key"]))]

    if intraday:
        d = oddslib.best_price(df.sort_values("ts"))
        d["when"] = d["ts"].dt.tz_convert("America/New_York").dt.strftime(
            "%H:%M ET")
        table = d.pivot_table(index="when", columns="book", values="american",
                              aggfunc="last")
        show = pd.DataFrame(index=table.index)
        for book in sorted(table.columns):
            show[book] = [oddslib.fmt_american(v) for v in table[book]]
        body = head + ["## %s — %s, by the half hour" % (market, label), "",
                       oddslib.md_table(show.reset_index(),
                                        ["when"] + sorted(table.columns))]
        oddslib.write_outputs({"intraday": d[[
            "ts", "event_date", "event_name", "market", "book", "american",
            "decimal", "imp_prob", "is_best"]]}, body, stem)
        return

    daily = daily_trend(df, field=field)
    table = wide(daily)

    first, last = table.iloc[0], table.iloc[-1]
    fair_series = daily.groupby("event_date")["fair_prob"].median().dropna()
    drift = ""
    if len(fair_series) > 1:
        delta = 100.0 * (fair_series.iloc[-1] - fair_series.iloc[0])
        drift = (" The market's read on him has moved %+.1f probability "
                 "points across that span." % delta)

    body = head + [
        "## %s, %s across %d game day(s)%s" % (
            market, player, len(table),
            " — best price %s at %s" % (last["BEST"], last["AT"])
            if "BEST" in last else ""),
        "",
        "Each cell is that book's closing price for the day. BEST is the "
        "longest price available; FAIR is the market's own number with the "
        "vig stripped out.%s" % drift,
        "",
        oddslib.md_table(table, list(table.columns)),
    ]

    keep = ["event_date", "event_name", "market", "player", "book", "american",
            "decimal", "imp_prob", "fair_prob", "is_best", "best_book",
            "outcome_key"]
    oddslib.write_outputs(
        {"trend": daily[[c for c in keep if c in daily.columns]],
         "wide": table}, body, stem)


if __name__ == "__main__":
    main()
