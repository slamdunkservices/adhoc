#!/usr/bin/env python3
"""
Which book to bet at: hold, and how often each one has the best number.

Usage:
    python3 book_holds.py wnba --since 30d
    python3 book_holds.py mlb --since 30d --market "home run 1+"
    python3 book_holds.py nba --from 2026-01-01 --to 2026-04-15 --market total

Hold is the share of every dollar wagered a book keeps when both sides are
covered — the price of doing business there. `best_price_share` is how often
that book had the longest price on the board, which is what actually decides
where a bet should go.

Options:
    --since SPAN    look back this far (default 30d)
    --from / --to   an explicit window instead of --since
    --market M      market name (repeatable; default: every market)
    --min-n N       drop book/market pairs with fewer than N events (default 10)
    --all-books     include books that have stopped reporting
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

import oddslib


def holds(df):
    """Per book and market: hold at the close, and best-price frequency.

    Only the closing snapshot counts. Books shade their numbers all day and
    the last one before the event is the one worth comparing.
    """
    if df.empty:
        return pd.DataFrame(), pd.DataFrame()

    # Hold is a property of the main number. Alternate rungs are priced with
    # their own margins and would drag the figure around.
    df = oddslib.main_line(df)
    snaps = oddslib.snap_open_close(df, open_days=3.0)
    if snaps.empty:
        return pd.DataFrame(), pd.DataFrame()

    # Rebuild the closing board so hold is computed on quotes that coexisted.
    close = df.merge(
        snaps[["outcome_key", "book", "ts_close"]].rename(
            columns={"ts_close": "ts"}),
        on=["outcome_key", "book", "ts"], how="inner")
    if close.empty:
        return pd.DataFrame(), pd.DataFrame()

    priced = oddslib.fair_prices(close)
    if priced.empty:
        return pd.DataFrame(), pd.DataFrame()

    # Hold needs both sides of a question, or a whole field. Standalone props
    # like "home run 1+" have neither, so there is no margin to measure and
    # they're left out rather than reported as blanks.
    holdable = priced[priced["hold"].notna()]
    per_event = (holdable.groupby(["book", "market", "event_key"],
                                  as_index=False)["hold"].mean())
    agg = (per_event.groupby(["book", "market"], as_index=False)
           .agg(median_hold=("hold", "median"), mean_hold=("hold", "mean")))

    # How often each book had the longest price, judged on the same closes.
    # This one is computable everywhere, including the standalone props.
    board = oddslib.best_price(close)
    share = (board.groupby(["book", "market"], as_index=False)
             .agg(best_price_share=("is_best", "mean"),
                  n_events=("event_key", "nunique")))
    agg = share.merge(agg, on=["book", "market"], how="left")
    agg["median_hold_pct"] = 100.0 * agg["median_hold"]
    agg["best_price_pct"] = 100.0 * agg["best_price_share"]
    return agg.sort_values(["market", "median_hold"]), priced


def main():
    argv = sys.argv[1:]
    args = [a for a in argv if not a.startswith("-")]
    if not args or "--help" in argv or "-h" in argv:
        sys.exit(__doc__.strip())

    def opt(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default

    def opt_all(name):
        return [argv[i + 1] for i, a in enumerate(argv) if a == name
                and i + 1 < len(argv)]

    league = args[0]
    markets = opt_all("--market") or None
    lake = opt("--lake")
    start, end = opt("--from"), opt("--to")
    if not start:
        start, label = opt("--since", "30d"), opt("--since", "30d")
    else:
        label = "%s_%s" % (start, end or "now")

    df = oddslib.load(league, start=start, end=end, markets=markets,
                      include_dead="--all-books" in argv, lake=lake)

    stem = "holds-%s-%s" % (league, label)
    head = ["# Book comparison — %s, %s" % (league.upper(), label), "",
            oddslib.freshness_line(league, lake), ""]

    if df.empty:
        oddslib.write_outputs({}, head + [
            "No quotes in that window. Widen it, or run `python3 ingest.py`."
        ], stem)
        return

    agg, priced = holds(df)
    if agg.empty:
        oddslib.write_outputs({}, head + [
            "Not enough paired quotes to measure hold. A market needs both "
            "sides (or a full field) in the same snapshot."], stem)
        return

    agg = agg[agg["n_events"] >= int(opt("--min-n", 10))]
    if agg.empty:
        oddslib.write_outputs({}, head + [
            "Every book/market pair fell under --min-n. Lower it or widen "
            "the window."], stem)
        return

    is_exch = agg["book"].isin(oddslib.EXCHANGES)
    books, exch = agg[~is_exch], agg[is_exch]

    body = list(head)
    for market, grp in books.groupby("market"):
        has_hold = grp["median_hold"].notna().any()
        grp = grp.sort_values(
            "median_hold" if has_hold else "best_price_pct",
            ascending=has_hold).copy()
        grp["hold"] = ["%.2f%%" % h if h == h else "—"
                       for h in grp["median_hold_pct"]]
        grp["best_price"] = ["%.0f%%" % b for b in grp["best_price_pct"]]
        sharpest = grp.sort_values("best_price_pct", ascending=False).iloc[0]
        lead = ("Most often the best price: **%s** (%s of outcomes)."
                % (sharpest["book"], sharpest["best_price"]))
        if has_hold:
            cheapest = grp.iloc[0]
            lead = ("Cheapest hold: **%s** at %s. " % (cheapest["book"],
                                                       cheapest["hold"])) + lead
        else:
            lead += (" Hold isn't measurable here — a standalone prop has no "
                     "second side to price against.")
        body += ["## %s" % market, "", lead, "",
                 oddslib.md_table(grp, ["book", "hold", "best_price",
                                        "n_events"]), ""]

    if not exch.empty:
        e = exch[exch["median_hold"].notna()].copy()
        e["hold"] = ["%.2f%%" % h for h in e["median_hold_pct"]]
        body += ["## Exchanges", "",
                 "Not ranked with the books — they charge commission instead "
                 "of a spread, so a near-zero hold here is the sanity check "
                 "that the numbers above are right.", "",
                 oddslib.md_table(e, ["book", "market", "hold", "n_events"]),
                 ""]

    oddslib.write_outputs(
        {"holds": agg[["book", "market", "median_hold", "mean_hold",
                       "best_price_share", "n_events"]]}, body, stem)


if __name__ == "__main__":
    main()
