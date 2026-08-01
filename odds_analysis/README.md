# Odds market analysis

Turns the odds scraper's CSV archive into things worth posting: what the market
moved on, who has the longest price, how a player's number has drifted, and
which book is actually worth betting at.

The archive is ~145k CSV files and ~26M rows spread across six leagues, so the
tools don't read it directly. `ingest.py` folds it into a parquet lake once,
then keeps up with the scraper incrementally. Everything else reads the lake.

No pip installs — pandas and pyarrow are already on the system Python, same
spirit as `../brand/make_social_posts`.

## Start here

```bash
python3 ingest.py
```

The first run reads the whole archive — 145k files, 28.3M rows, about 17
minutes. Every run after it picks up only what the scraper has written since,
which takes a few seconds. Run it before anything else; each tool prints how
current the lake is at the top of its output.

Then pick a question:

| Tool | Reach for it when |
|---|---|
| `line_moves.py` | "What did the market change its mind about?" Biggest open-to-close moves, and steam — several books moving one number together |
| `line_shop.py` | "Where's the best price right now?" The full board per outcome, the longest price, and how it compares to fair. Feeds the card generator |
| `prop_trends.py` | "What's this player's number been doing?" One player's price across days or across a single day |
| `book_holds.py` | "Which book should we be betting at?" Hold per book per market, and how often each has the best number |

Every tool writes tidy CSVs plus a markdown digest to `out/`, and prints the
digest. The CSVs are for digging further; the digest is close to post copy.

## Examples

```bash
python3 line_moves.py wnba --date today
```

```bash
python3 line_shop.py mlb --market "home run 1+" --books-only --card
```

```bash
python3 prop_trends.py mlb "Kyle Schwarber" --market "home run 1+" --since 14d
```

```bash
python3 book_holds.py wnba --since 30d
```

Each script's `--help` is its full option list. Run one with no arguments to
see the same thing.

## Making a card from a shopped price

`line_shop.py --card` writes a `build_card.py` config per outcome into
`out/cards/`, with the prices already in it — every book's number, best first,
and `"best": true` on the longest one.

It deliberately leaves four fields blank, because they aren't the market's to
fill: `photo`, `team`, `jersey`, and `proj` (your model's price — the card
compares it against the board, so it has to be yours). Fill them in and render:

```bash
python3 ../brand/make_social_posts/build_card.py out/cards/<slug>.json
```

`build_card.py` names exactly which fields are still missing if you try it
early, so there's no guessing.

## Common options

| Option | Default | Notes |
|---|---|---|
| `--date` | `today` | A game day: `today`, `2026-07-31`, or `all` |
| `--since` | varies | A lookback instead of a day: `24h`, `14d` |
| `--market` | every market | Exact market name, e.g. `moneyline`, `"home run 1+"`. Repeatable |
| `--all-books` | off | Include `espnbet` and `bet365`, which stopped reporting in 2025 |
| `--lake` | `./data` | Where the lake lives. Also `$ODDS_LAKE` |
| `--raw` | `~/Desktop/files/odds_getter` | The scraper's output. Also `$ODDS_RAW` |

Market names are exact and vary by league — `python3 ingest.py --status` shows
what's loaded, and the `_board.csv` any tool writes lists the real names.

## What the numbers mean

**Everything is measured in probability, not in the American number.** A
20-cent move on a favorite and the same move on a longshot are nothing alike,
and only the probability says so. `move_pp` and `edge_pp` are in probability
points: `+2.5 pp` means the implied chance rose two and a half points.

**"Fair" depends on the market**, and every output says which rule it used:

| Market | Reference price | Why |
|---|---|---|
| moneyline, spread, total | no-vig, two-way | Both sides exist, so the margin can be divided out |
| YRFI / NRFI | no-vig, pair | The same question filed under two names |
| first basket, outrights | no-vig, field | Exactly one entrant wins, so the field sums to 1 |
| home run 1+, top 10, make cut | market median, **vig included** | Standalone yes/no props. Several hitters can homer in one game, so these don't sum to anything — there's no margin to remove, only other books to compare against |
| first basket **by team** | market median, **vig included** | Looks like a field but is two of them, one per roster, summing to ~2.2 — and the scraper doesn't record which team a player is on for this market, so they can't be separated |

That last row is the one to be careful with. An edge measured against a
vig-inclusive median understates rather than flatters, which is the safe
direction, but it is not a true no-vig edge and shouldn't be posted as one.

**Hold vs overround.** Hold is the share of handle a book keeps — a −110/−110
total is 4.76% overround but the familiar 4.55% hold. Both are in the CSVs;
digests quote hold.

**Exchanges are separate.** `novig` and `kalshi` charge commission instead of
building a margin into the price, so they often hold the longest number on the
board and their hold reads near zero. That zero is the sanity check that the
book numbers are right. They're excluded from steam and from movers (a thin
order book drifts to absurd prices once the money leaves), and
`line_shop.py --books-only` drops them when a post needs a sportsbook.

## The lake

`data/` — gitignored and rebuildable. 3.4 GB of CSV compresses to about 94 MB.
Hive-partitioned by league and month:

```
data/manifest.parquet              one row per source file ingested
data/lake/league=wnba/ym=2026-07/part-<batch>-<n>.parquet
```

One row is one book's quote on one outcome at one scrape. The columns that
matter:

| Column | Notes |
|---|---|
| `ts` | When the scraper looked. Always UTC, always reliable |
| `event_start` | When the game starts, normalized to UTC |
| `event_date` | The ET calendar date — the game day a bettor means |
| `market`, `side`, `line`, `subtype` | `side` is the selection: a team, `over`/`under`, or a player. `line` is the number on a spread or total |
| `american`, `decimal`, `imp_prob` | The price, three ways |
| `event_key` | `wnba\|2026-07-31\|SEA@ATL` — one contest, agreeing across books |
| `outcome_key` | `event_key` + market + side + subtype — one bettable outcome |
| `src_file` | Which archive CSV the row came from |

`outcome_key` deliberately leaves the line out, so a spread drifting 12.5 → 13
stays the same tracked outcome at a new number. That's what lets `line_moves.py`
separate a price move from a line move. Group on `(outcome_key, line)` when you
want the series for one specific number.

## Things the archive does that will bite you

All handled, but worth knowing when a number looks wrong:

- **The league in a filename is the real one.** `wnba/archive/` predates the
  per-sport split and still holds ~12k `nba` and `mlb` files. Reading it as
  WNBA silently mixes three sports together.
- **Filename timestamps are unpadded** — `202659224` is 2026-05-09 02:24. They
  can't be parsed or sorted. The `ts` column inside the file is the truth.
- **`event_start` arrives four ways**: ISO with a Z, naive (UTC), an ET offset,
  and epoch milliseconds in the oldest files.
- **hardrockbet named MLB games backwards** until 2026-07-29 — `Athletics vs
  Red Sox`, home team first, nicknames. Normalized onto everyone else's order.
- **MLB teams have two naming eras**: abbreviations in 2025, full club names
  since. Both fold onto the abbreviation.
- **kalshi's clock can be hours off.** Event identity and closing prices use
  the modal start time across books, never one book's.
- **The scraper keeps running during games.** A home-run price lengthens every
  inning the batter doesn't go deep, so the last quote of a day is a live
  number, not the close. Anything comparing days cuts at the start time first.
- **Spreads and totals arrive as a ladder.** One snapshot can hold 24 alternate
  spreads, from DAL -15.5 (+575) to +8.5 (-525), all sharing an `outcome_key`
  because the key ignores the number. The tools cut to the main line — the rung
  whose two sides are priced most evenly — before shopping or measuring
  movement. Skip that and the longest alternate looks like the best price on
  the board.
- **Devigging needs the whole field.** One player's slice of a first-basket
  market sums to about a tenth, and normalizing it alone would price him at
  even money. Filter to a player *after* computing fair, never before.
- **Coverage gaps**: nothing in May–June 2025. MLB's own directory starts
  2026-06-29, though the 2025 files misfiled under `wnba/` reach back further.
- **`*_current.csv` files are byte-identical copies** of the newest archive
  snapshot, so ingest skips them.

## Troubleshooting

**"no data for X — run: python3 ingest.py"** — that league isn't in the lake
yet. `python3 ingest.py --league X`.

**A digest says the lake is stale.** The scraper writes every 30 minutes; run
`python3 ingest.py` again. Only WNBA and MLB are live — the other leagues are
between seasons and their archives are closed.

**"No board found"** — nothing is being quoted for that day and market. Check
the market name (they're exact), or try `--date all`.

**An ingest run was interrupted.** Nothing to clean up. Fragments carry the id
of the run that wrote them, and a run the manifest never recorded is swept on
the next start. Re-run it.

**A file won't parse.** It's recorded as `status=error` in the manifest and
skipped, and the batch around it is retried a file at a time so one bad file
can't take down a backfill. `python3 ingest.py --status` shows the count;
`--strict` makes it stop on the first one instead.

**Changing how rows are parsed** means the lake is stale in a way ingest can't
detect — it tracks source files, not code. Delete `data/` and re-ingest.

## Layout

| File | What it is |
|---|---|
| `oddslib.py` | Everything shared: odds math, the CSV normalizer, the keys, devigging, the lake reader. `python3 oddslib.py` runs its self-test |
| `ingest.py` | Archive → lake, incremental and crash-safe |
| `line_moves.py`, `line_shop.py`, `prop_trends.py`, `book_holds.py` | The four tools |
| `data/`, `out/` | Lake and outputs. Both gitignored |

Changing anything about prices, keys, or parsing means changing `oddslib.py`.
Run `python3 oddslib.py` after — it checks the odds math round-trips, that all
four `event_start` formats land on the same instant, that MLB's naming eras
collapse to one key, and that the devig rules do what they claim.
