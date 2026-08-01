#!/usr/bin/env python3
"""
Shop the board: who has the longest price, and is it worth taking.

Usage:
    python3 line_shop.py wnba --market "first basket" --date today
    python3 line_shop.py mlb --market "home run 1+" --top 20
    python3 line_shop.py wnba --market "first basket" --player "Caitlin Clark" --card

For every outcome it lists each book's current price, flags the longest one,
and prices the market's own consensus with the vig stripped out. `edge_pp` is
how many probability points the best price beats that consensus by — the
number to lead a post with when it's positive.

Options:
    --date D        game day: today, 2026-07-31 (default: today)
    --market M      market name, e.g. moneyline, "home run 1+" (repeatable)
    --player NAME   only outcomes whose player matches (substring, any case)
    --team ABBR     only this team's games
    --top N         keep the N best edges (default 40)
    --min-books N   ignore outcomes priced by fewer than N books (default 2)
    --stale-min N   drop quotes older than N minutes (default 90)
    --card          also write build_card.py configs to out/cards/
    --books-only    drop the exchanges (novig, kalshi) from the board
    --all-books     include books that have stopped reporting

The exchanges charge commission instead of building a margin into the price,
so they often hold the longest number on the board. That's real, and worth
saying — but pass --books-only when the post needs a sportsbook.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

import oddslib

# The exchanges price without meaningful vig, so their number is shown as its
# own benchmark rather than mixed into the consensus of the books.
BENCHMARK = oddslib.EXCHANGES


def shop(league, dates=None, markets=None, player=None, team=None,
         min_books=2, stale_min=90, include_dead=False, books_only=False,
         lake=None):
    """The current board, best price per outcome, and the edge over fair."""
    # Two days of snapshots is plenty to find each book's latest quote. The
    # window is anchored to the newest data in the lake rather than to the
    # wall clock, so this still works on a league whose season is over.
    newest = oddslib.lake_freshness(league, lake)
    start = None if newest is None else newest - pd.Timedelta(hours=48)
    if dates:
        # Quotes for a game appear days ahead of it.
        start = pd.Timestamp(min(dates)).tz_localize(
            "America/New_York") - pd.Timedelta(days=3)
    # Deliberately loaded *unfiltered* by player: fair prices need the whole
    # field, and one player's slice of a first-basket market would devig to a
    # near-certainty.
    df = oddslib.load(league, start=start, dates=dates, markets=markets,
                      team=team, include_dead=include_dead, lake=lake)
    if df.empty:
        return df, df

    board = oddslib.latest_board(df, stale_min=stale_min)
    if books_only and not board.empty:
        board = board[~board["book"].isin(BENCHMARK)]
    if board.empty:
        return board, board

    # Fair price comes from the books only — folding the exchanges in would
    # double-count the very thing they're being used to check.
    from_books = board[~board["book"].isin(BENCHMARK)]
    # The board is already one row per book, so no time bucketing.
    fair = oddslib.fair_prices(from_books, bucket_min=None)
    if fair.empty:
        consensus = pd.DataFrame(columns=["outcome_key", "fair_prob", "basis"])
    else:
        consensus = (fair.groupby("outcome_key", as_index=False)
                     .agg(fair_prob=("fair_prob", "median"),
                          basis=("basis", "first")))

    exch = (board[board["book"].isin(BENCHMARK)]
            .groupby("outcome_key", as_index=False)["imp_prob"].median()
            .rename(columns={"imp_prob": "fair_exch"}))

    priced = oddslib.best_price(board)
    counts = (priced.groupby("outcome_key", as_index=False)["book"]
              .nunique().rename(columns={"book": "n_books"}))

    best = priced[priced["is_best"]].drop_duplicates("outcome_key")
    best = (best.merge(counts, on="outcome_key")
                .merge(consensus, on="outcome_key", how="left")
                .merge(exch, on="outcome_key", how="left"))
    best = best[best["n_books"] >= int(min_books)]
    # Now that fair prices are settled, narrow to the player asked about.
    if player is not None and not best.empty:
        who = best["player"].fillna("").str.lower()
        best = best[who.str.contains(str(player).lower(), regex=False)]
    if best.empty:
        return best, priced

    best["fair_american"] = [oddslib.prob_to_american(p)
                             for p in best["fair_prob"]]
    # Positive edge means the longest price implies a lower chance than the
    # market as a whole thinks — the price is longer than it should be.
    best["edge_pp"] = 100.0 * (best["fair_prob"] - best["imp_prob"])
    best["edge_exch_pp"] = 100.0 * (best["fair_exch"] - best["imp_prob"])
    return best.sort_values("edge_pp", ascending=False), priced


def card_for(row, board, league):
    """A build_card.py config for one outcome, with the prices filled in.

    The market numbers are all it knows, so `proj` — the model's own price —
    is left blank along with the photo and the team/jersey copy. build_card
    names exactly what's missing when you try to render it.
    """
    rows = board[board["outcome_key"] == row["outcome_key"]]
    market = row["market"]
    tag_sub, pick_text, chip = oddslib.MARKET_CARD.get(
        market, (str(market).upper(), str(market).upper(), ""))

    who = row["player"] or row["side"]
    date = row["event_date"]
    slug = "%s-%s-%s" % (oddslib.slugify(who), oddslib.slugify(market), date)

    note = "Shopped %d books · longest price on the board" % row["n_books"]
    if row.get("event_name"):
        note += " · %s" % row["event_name"]

    return {
        "template": "fullbleed",
        "slug": slug,
        "photo": "",
        "league": str(league).upper(),
        "tag_sub": tag_sub,
        "name": str(who).upper(),
        "team": "",
        "jersey": "",
        "pick_label": "THE PICK",
        "chip": chip,
        "pick_text": pick_text,
        "note": note,
        "proj": "",
        "books": oddslib.books_json(rows),
    }


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
    date = opt("--date", "today")
    dates = None if date in ("all", "any") else [oddslib.resolve_date(date)]

    best, board = shop(
        league, dates=dates, markets=markets, player=opt("--player"),
        team=opt("--team"), min_books=int(opt("--min-books", 2)),
        stale_min=int(opt("--stale-min", 90)),
        include_dead="--all-books" in argv,
        books_only="--books-only" in argv, lake=opt("--lake"))

    stem = "shop-%s-%s" % (league, date if date not in ("all", "any") else "all")
    if markets:
        stem += "-" + oddslib.slugify(markets[0])

    head = ["# Line shopping — %s%s" % (league.upper(),
                                        ", " + ", ".join(markets) if markets else ""),
            "", oddslib.freshness_line(league, opt("--lake")), ""]
    if best.empty:
        oddslib.write_outputs({}, head + [
            "No board found. Nothing is being quoted for that day and market, "
            "or the lake needs `python3 ingest.py`."], stem)
        return

    top = int(opt("--top", 40))
    show = best.head(top).copy()
    show["best"] = [oddslib.fmt_american(a) for a in show["american"]]
    show["fair"] = [oddslib.fmt_american(a) for a in show["fair_american"]]
    show["edge"] = ["%+.1f" % e for e in show["edge_pp"]]
    show["who"] = show["player"].where(show["player"].astype(bool),
                                       show["side"])

    bases = sorted(set(best["basis"].dropna()))
    body = head + [
        "%d outcomes priced by up to %d books. Positive edge means the best "
        "price pays more than the rest of the market implies."
        % (len(best), int(best["n_books"].max())),
        "",
        "_Reference price: %s._" % "; ".join(bases) if bases else "",
        "",
        oddslib.md_table(
            show, ["event_date", "event_name", "market", "who", "best",
                   "best_book", "fair", "edge", "n_books"]),
    ]

    keep = ["event_date", "event_name", "market", "subtype", "side", "player",
            "best_book", "american", "fair_american", "fair_prob", "imp_prob",
            "edge_pp", "edge_exch_pp", "basis", "n_books", "outcome_key"]
    frames = {"shop": best[[c for c in keep if c in best.columns]],
              "board": board[["event_date", "event_name", "market", "side",
                              "player", "book", "american", "line", "ts",
                              "outcome_key"]]}

    if "--card" in argv:
        made = [oddslib.write_card(card_for(r, board, league))
                for _, r in show.iterrows()]
        body += ["", "## Cards", "",
                 "Wrote %d card config(s) to out/cards/. Each needs a photo, "
                 "team, jersey, and your model's `proj` before it renders:"
                 % len(made), "",
                 "```bash",
                 "python3 ../brand/make_social_posts/build_card.py %s"
                 % os.path.relpath(made[0], oddslib.ROOT),
                 "```"]

    oddslib.write_outputs(frames, body, stem)


if __name__ == "__main__":
    main()
