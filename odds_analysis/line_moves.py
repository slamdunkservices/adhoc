#!/usr/bin/env python3
"""
What moved, how far, and whether the books moved together.

Usage:
    python3 line_moves.py wnba --date today
    python3 line_moves.py mlb --since 24h --market "home run 1+"
    python3 line_moves.py nba --date 2026-05-04 --market spread

Movement is measured in probability points, not in the American number: a
20-cent move on a favorite and on a longshot are nothing alike, and only the
probability says so. Steam is when several books move the same way at once —
that's the market agreeing on something, and it's the part worth posting.

Options:
    --date D        game day: today, 2026-07-31, all (default: today)
    --since SPAN    look back this far instead of a game day: 24h, 3d
    --market M      market name (repeatable)
    --open-days N   how far before the start counts as the open (default 3)
    --window N      minutes a steam move must land inside (default 60)
    --min-books N   books that must agree to call it steam (default 3)
    --min-move P    probability points each must move (default 2.0)
    --top N         movers to show in the digest (default 15)
    --with-exchanges  include novig/kalshi (their thin books drift wildly)
    --all-books     include books that have stopped reporting
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

import oddslib


def movements(df, open_days=3.0, with_exchanges=False):
    """Open-to-close change per outcome and book, in probability points.

    Two-sided markets are devigged first so the move reflects a changed
    opinion rather than a changed margin. Everything else is compared on the
    raw implied probability, which the digest says out loud.

    Exchanges sit out by default. A thin order book drifts to absurd numbers
    once the money leaves — a quote going +285 to +3233 overnight is an empty
    book, not the market changing its mind, and it would crowd out every real
    move in the table.
    """
    if df.empty:
        return df.copy()
    if not with_exchanges:
        df = df[~df["book"].isin(oddslib.EXCHANGES)]
    if df.empty:
        return df.copy()
    # Without this a spread's open and close land on different rungs of the
    # alternate ladder, and every game looks like it moved twenty points.
    df = oddslib.main_line(df)

    snaps = oddslib.snap_open_close(df, open_days=open_days)
    if snaps.empty:
        return snaps

    # For the two-sided markets, redo open and close as fair prices.
    fair = oddslib.devig_two_way(df)
    if not fair.empty:
        fair_snaps = oddslib.snap_open_close(
            fair.assign(imp_prob=fair["fair_prob"]), open_days=open_days)
        if not fair_snaps.empty:
            cols = ["outcome_key", "book", "imp_prob_open", "imp_prob_close"]
            snaps = snaps.merge(
                fair_snaps[cols].rename(columns={
                    "imp_prob_open": "fair_open",
                    "imp_prob_close": "fair_close"}),
                on=["outcome_key", "book"], how="left")

    if "fair_open" not in snaps.columns:
        snaps["fair_open"] = float("nan")
        snaps["fair_close"] = float("nan")

    # Recomputed after the merge above, so the mask can't be left aligned to
    # the pre-merge frame.
    two_way = snaps["market"].isin(oddslib.TWO_WAY)
    snaps["prob_open"] = snaps["fair_open"].where(two_way,
                                                  snaps["imp_prob_open"])
    snaps["prob_close"] = snaps["fair_close"].where(two_way,
                                                    snaps["imp_prob_close"])
    snaps["prob_open"] = snaps["prob_open"].fillna(snaps["imp_prob_open"])
    snaps["prob_close"] = snaps["prob_close"].fillna(snaps["imp_prob_close"])

    snaps["move_pp"] = 100.0 * (snaps["prob_close"] - snaps["prob_open"])
    snaps["devigged"] = two_way
    snaps["line_move"] = snaps["line_close"] - snaps["line_open"]
    snaps["price_open"] = [oddslib.fmt_american(a)
                           for a in snaps["american_open"]]
    snaps["price_close"] = [oddslib.fmt_american(a)
                            for a in snaps["american_close"]]
    return snaps[snaps["move_pp"].notna()]


def steam(df, window_min=60, min_books=3, min_move_pp=2.0):
    """Windows where several books moved one outcome the same way at once.

    Each book's own consecutive quotes are differenced, then the moves are
    bucketed into windows. Exchanges are left out — they follow their own
    traders, so counting them would inflate the agreement being measured.
    """
    if df.empty:
        return pd.DataFrame()

    d = df[~df["book"].isin(oddslib.EXCHANGES)]
    if d.empty:
        return pd.DataFrame()
    # Same reason as in movements(): diffing across alternate rungs would
    # manufacture huge moves that never happened.
    d = oddslib.main_line(d)

    d = d.sort_values("ts")
    grp = d.groupby(["outcome_key", "book"], sort=False)
    d["prev_prob"] = grp["imp_prob"].shift(1)
    d["prev_am"] = grp["american"].shift(1)
    d["step_pp"] = 100.0 * (d["imp_prob"] - d["prev_prob"])
    d = d[d["step_pp"].abs() >= float(min_move_pp)]
    if d.empty:
        return pd.DataFrame()

    d["window"] = d["ts"].dt.floor("%dmin" % int(window_min))
    d["dir"] = d["step_pp"].apply(lambda x: "shorter" if x > 0 else "longer")

    events = []
    for (key, window, direction), grp2 in d.groupby(
            ["outcome_key", "window", "dir"], sort=False):
        if grp2["book"].nunique() < int(min_books):
            continue
        first = grp2.iloc[0]
        events.append({
            "event_date": first["event_date"],
            "event_name": first["event_name"],
            "market": first["market"],
            "side": first["side"],
            "player": first["player"],
            "window_start": window,
            # "shorter" means the price got worse for the bettor: the market
            # moved toward this outcome happening.
            "direction": direction,
            "n_books": grp2["book"].nunique(),
            "avg_move_pp": grp2["step_pp"].mean(),
            "books": ", ".join(sorted(grp2["book"].unique())),
            "detail": " · ".join(
                "%s %s->%s" % (r["book"], oddslib.fmt_american(r["prev_am"]),
                               oddslib.fmt_american(r["american"]))
                for _, r in grp2.sort_values("book").iterrows()),
            "outcome_key": key,
        })
    if not events:
        return pd.DataFrame()
    return (pd.DataFrame(events)
            .sort_values(["n_books", "avg_move_pp"],
                         key=lambda s: s.abs() if s.name == "avg_move_pp" else s,
                         ascending=False))


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
    since, date = opt("--since"), opt("--date", "today")
    lake = opt("--lake")

    end = None
    if since:
        dates, start, label = None, since, since
    elif date in ("all", "any"):
        dates, start, label = None, None, "all"
    else:
        day = oddslib.resolve_date(date)
        # Quotes for a game are posted days ahead, so the scan starts earlier
        # than the game day itself. Anchored to the day in question, not to
        # today, so a date from a finished season still works.
        lookback = int(opt("--open-days", 3)) + 2
        start = pd.Timestamp(day).tz_localize("America/New_York") - \
            pd.Timedelta(days=lookback)
        end = pd.Timestamp(day).tz_localize("America/New_York") + \
            pd.Timedelta(days=1)
        dates, label = [day], day

    df = oddslib.load(league, start=start, end=end, dates=dates,
                      markets=markets, include_dead="--all-books" in argv,
                      lake=lake)

    stem = "moves-%s-%s" % (league, label)
    if markets:
        stem += "-" + oddslib.slugify(markets[0])
    head = ["# Line movement — %s%s" % (
        league.upper(), ", " + ", ".join(markets) if markets else ""),
        "", oddslib.freshness_line(league, lake), ""]

    if df.empty:
        oddslib.write_outputs({}, head + [
            "Nothing quoted for that window. Widen `--since`, or run "
            "`python3 ingest.py`."], stem)
        return

    moves = movements(df, open_days=float(opt("--open-days", 3)),
                      with_exchanges="--with-exchanges" in argv)
    hot = steam(df, window_min=int(opt("--window", 60)),
                min_books=int(opt("--min-books", 3)),
                min_move_pp=float(opt("--min-move", 2.0)))

    top = int(opt("--top", 15))
    body = list(head)

    if moves.empty:
        body += ["No open-to-close moves in range — the market hasn't had "
                 "time to move yet.", ""]
    else:
        big = moves.reindex(moves["move_pp"].abs().sort_values(
            ascending=False).index).head(top).copy()
        big["who"] = big["player"].where(big["player"].astype(bool), big["side"])
        big["move"] = ["%+.1f pp" % m for m in big["move_pp"]]
        big["drift"] = big["price_open"] + " → " + big["price_close"]
        body += ["## Biggest movers", "",
                 "Change in the chance the market gives each outcome, from the "
                 "opening quote to the last one before tipoff.", "",
                 oddslib.md_table(big, ["event_date", "event_name", "market",
                                        "who", "book", "drift", "move"]), ""]

        lines = moves[moves["line_move"].fillna(0) != 0]
        if not lines.empty:
            lines = lines.reindex(lines["line_move"].abs().sort_values(
                ascending=False).index).head(top).copy()
            lines["shift"] = (lines["line_open"].astype(str) + " → "
                              + lines["line_close"].astype(str))
            body += ["## Line moves", "",
                     "Where the number itself moved, not just the price.", "",
                     oddslib.md_table(lines, ["event_date", "event_name",
                                              "market", "side", "book",
                                              "shift", "price_close"]), ""]

    body += ["## Steam", ""]
    if hot.empty:
        body += ["No steam. No outcome had %s books move it the same way by "
                 "%s points inside %s minutes."
                 % (opt("--min-books", 3), opt("--min-move", 2.0),
                    opt("--window", 60))]
    else:
        show = hot.head(top).copy()
        show["who"] = show["player"].where(show["player"].astype(bool),
                                           show["side"])
        show["when"] = show["window_start"].dt.tz_convert(
            "America/New_York").dt.strftime("%m/%d %H:%M ET")
        show["avg"] = ["%+.1f pp" % m for m in show["avg_move_pp"]]
        body += ["Several books moving one outcome together, inside %s minutes."
                 % opt("--window", 60), "",
                 oddslib.md_table(show, ["when", "event_name", "market", "who",
                                         "direction", "n_books", "avg",
                                         "books"]), "",
                 "Priced-in detail for the top one:", "",
                 "> " + str(show.iloc[0]["detail"])]

    frames = {}
    if not moves.empty:
        frames["moves"] = moves[[
            "event_date", "event_name", "market", "side", "player", "book",
            "american_open", "american_close", "line_open", "line_close",
            "prob_open", "prob_close", "move_pp", "devigged", "ts_open",
            "ts_close", "outcome_key"]]
    if not hot.empty:
        frames["steam"] = hot

    oddslib.write_outputs(frames, body, stem)


if __name__ == "__main__":
    main()
