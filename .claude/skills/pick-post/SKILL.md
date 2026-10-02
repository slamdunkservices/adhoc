---
name: pick-post
description: Turn a Slam Dunk Bets pick alert into a finished social post — the branded 1080×1350 card PNG plus Instagram caption, hashtag comment, and tweet. Use whenever the user pastes an alert line like `Jhostynxon Garcia 1+ (proj +873): +1200 br (0.7u)` or asks for a social card, pick card, branded Instagram post, caption, tweet, or hashtag comment for a home run, stolen base, total bases, RBI, run scored, first basket, first-basket-exact, first-three, NFL anytime touchdown (1+ / 2+), or NFL first touchdown scorer play.
---

# Pick post

The repeated job in this repo: a pick alert goes in, a postable bundle comes out.

**Default deliverables** — card + Instagram caption + hashtag comment. Add a
tweet only when the request names one ("and a tweet", "twitter post"). Produce
what was asked; don't pad the set.

Paths below are relative to the repo root.

Mechanics of the generator live in `brand/make_social_posts/README.md` —
templates, config fields, accents, photo tuning, the market-copy table. Read it
for *how the card renders*. This skill is *how the post gets made*, and the
things that have gone wrong before.

Three scripts sit next to this file and do the mechanical work. Run them rather
than rediscovering what they know:

| Script | What it answers |
|---|---|
| `find_plays.py` | What's postable today, at what price, for what stake |
| `player_brief.py` | Everything true about the player, plus the caveats a caption must respect |
| `get_photo.py` | Fetch, validate, name and cache the photo; suggest the frame |

All three are read-only, take `--player` with or without accents, and speak
`--json`.

Brand voice, platform register, and truth rules live in
`brand/social_style_guide.md`. Sections 2 (voice), 4.1–4.2 (X and IG), and 6
(truth rules) are the ones that bind here.

---

## Hard rules

These are all things that were corrected after the fact. Get them right the
first time.

**1. The caption is about the player, not the bet.** This has been corrected
more than any other single thing — *"make the caption more about season
performance"*, *"not as much detail about the relative edge size"*, *"focus on
player performance information not bet information"*. Recent requests now say
it up front: *"use current season stats as focus, not the betting odds/difference."*

Lead with a real season number. Build the whole caption out of performance —
form, splits, role, matchup. The model-vs-book gap is **not** the story and
usually shouldn't be mentioned at all.

**The caption carries no price and no closing tag block.** It used to end on
two lines naming the play and the game; that was cut — *"no longer add that
last 2 line piece at the bottom (the play and the game), ppl know that
already."* The card is in the post with the market, the matchup, the book, the
odds and the stake already on it, so those lines spent the closing beat of the
copy on what the reader is looking at. End on performance and stop.

**2. The photo must show the player on their current team.** Corrected twice
(*"use a pic of him on the rangers please"*, *"ok got one in an As uniform,
final answer"*). A stale-uniform shot gets rejected — verify the team against
the roster before spending time tuning the crop.

**3. Quote the model that actually grades the market.** Corrected on DeWanna
Bonner: *"this market only cares about field goals not points, please remove
the points number."* Exact-method markets (`fg2`/`fg3`) settle on a made field
goal only → carry the FG number alone (`Model: +916`). Points-settled markets
(plain first basket, first basket by team) can be won on a free throw → carry
both, points first (`Model: +961 points · +888 FG`). README §"Market copy"
has the full table.

The MLB version of the same rule: home runs price off `p_hr_cal` and steals off
`p_sb_cal`, two different columns of the same sim snapshot. A steal card that
quotes the HR model — or a HR card that quotes the steal one — is quoting a
number that does not grade the bet. Each edge row names its own basis in
`probability_source` (`model_p_sb_cal`, or `model_p_sb` when the calibrator
wasn't on disk for that run); `find_plays.py` already picks the right one.

The batter props follow suit, each off its own uncalibrated column: total bases
2+ on `p_2tb`, RBI on `p_rbi`, run scored on `p_run` (`probability_source`
`model_p_2tb` / `model_p_rbi` / `model_p_run`). `player_brief.py` carries them as
`model_price_tb` / `model_price_rbi` / `model_price_run` — never quote
`model_price` (the HR one) on one of these cards.

The NFL version: anytime TD 1+ prices off `any_td_full`, 2+ off
`two_plus_td_full`, first touchdown off `first_td_full` — three rows per player
in `td_edges_current.csv`, keyed by `market` (`1+` / `2+` / `FTD`), each with
its own `projection`. `player_brief.py` carries them as `model_price_td` /
`model_price_td2` / `model_price_ftd`. A first-TD card quoting the anytime
price (+319 vs +1824 for the same player, same night) is off by a factor of six.

**4. The stake is the unit size the Discord alert recommends — always.**
Corrected on Bonner: *"use the recommended stake from our outputs, which for
bonner is 0.9 for the +800 odds at dk."* That number is **standardized**, and
the raw half-Kelly sitting in the tables is not it:

| Source | Column to use | Why |
|---|---|---|
| MLB `home_runs_edges_current.csv` (+ archive) | `units_std` | Already standardized; the feed posts this |
| MLB `stolen_bases_edges_current.csv` (+ archive) | `units_std` | Same column, different sizing — see below |
| MLB `total_bases_edges` / `rbi_edges` / `runs_edges` `_current.csv` (+ archive) | `units_std` | Same column; each market has its own norm |
| NFL `td_edges_current.csv` (+ archive) | `units_std` | Half-Kelly ÷ 5.0 since 2026-09-11 (`jobs/odds/src/py/nfl_units.py`); one column for 1+, 2+ and FTD |
| WNBA `plays_*.csv` (today) | `<code>_units` **÷ the per-market norm** | This column is RAW half-Kelly — posting it overstates the stake |
| WNBA `tracking/<market>/<date>/` (past) | `units_standardized` | What was actually alerted that day |

The WNBA norms are per market and they **change** — `first_basket_by_team_exact`
was 1.38 in June and 1.31 by August. `find_plays.py` reads them live from
`first_event_tracking_avg_units()` in
`$MODELS_ROOT/shared/basketball/R/first_event_market_config_utils.R` and prints
which source it used. Don't hardcode them, and don't re-derive a historical
stake from raw units — the norm in force then is baked into the stored
`units_standardized`, and nowhere else.

**A unit means something different in each MLB market.** From game date
2026-09-12 every MLB product stakes quarter-Kelly divided by *its own* measured
norm (`jobs/odds/src/py/mlb_units.py`: HR 0.95, SB 0.98, RBI 1.61, runs 1.94,
total bases 2.0), so "1u" means *an average play in that market*. Before
09-12 HR was half-Kelly over a norm, while SB and the three batter props posted
raw quarter-Kelly with no norm (1u = literally 1% of bankroll). Read `units_std`
off the row either way — the stored value already carries the sizing in force
that day — and nothing in a caption or card should describe one market's stake
as big or small relative to another's.

Never round or eyeball a unit size.

**5. Don't repeat the last card's look.** Check the newest few configs in
`cards/` and pick a different `template`+`accent` than the recent ones — five
frames and three accents exist so a week of posts doesn't read as one template
on repeat. Frame choice is driven by the photo (README §"Tuning the photo"),
but among workable frames, take the one used least recently.

**`bigprice` is retired — never pick it.** The rotation is `base`,
`fullbleed`, `split`, `ticket`, `poster`. Corrected on Tatis: *"the tatis frame
was crappy... i don't like the 'GIANT NUMBER over faint image' design moving
forward."* The template grades the photo to `grayscale(.55) brightness(.52)`
behind a 300px price, so the player stops being the subject and the action shot
you just spent time sourcing is wasted. Cards already rendered with it stand;
don't build new ones.

**6. Never invent a number.** Style guide §6. Every stat in the caption traces
to a file you actually read. If a stat isn't available, cut the line.

**7. One or two emoji, or none at all.** Corrected against the old house
format: *"not use so many emoji. 1-2 max, and its ok to not use any."* The
shape being cut is the decorated one — an emoji in front of every stat line,
six or seven in a caption that already had real numbers doing the work. **One
or two for the entire caption**, chosen because they carry something the words
don't, and zero is a perfectly good answer. The same ceiling applies to the
tweet and to the hashtag comment. Style guide §2 "Emoji lexicon" has the
vocabulary if you do use one.

**8. No stats or highlights on the card itself.** The only text the image
carries: the player, the team, the matchup, the market, the odds, the model
(projected) odds, the units, and the book(s). Stats, streaks, exit velos,
"scored in 8 of her last 10" lines — all of that belongs in the caption, never
in the PNG. In config terms:

- `kicker` stays generic — leave it at the `TODAY'S PLAY` default. Past cards
  put a performance stat there; don't.
- `note` / `pick_sub` carries only the matchup, the model price, and a
  book-count/availability tail (`vs CLE · Model: +618 · best of 7 books`). The
  `base` frame has no `note` slot — `pick_sub` is its note line, and a `note`
  set there renders nowhere.
- `pick_label`, `chip`, `pick_text` carry market copy per README §"Market
  copy" — nothing about performance.

---

## Step 1 — Get the play

**Default: find it yourself.** `find_plays.py` (next to this file) reads the
live edge tables and prints the same alert-format lines the Discord feed posts,
so nobody has to paste numbers:

```bash
python3 .claude/skills/pick-post/find_plays.py
```

```
python3 .claude/skills/pick-post/find_plays.py --player "Kiah Stokes"
python3 .claude/skills/pick-post/find_plays.py --league wnba --date 2026-08-01
python3 .claude/skills/pick-post/find_plays.py --json
python3 .claude/skills/pick-post/find_plays.py --market steal
python3 .claude/skills/pick-post/find_plays.py --market tb      # also: rbi, runs
```

It covers MLB home runs, stolen bases, total bases 2+, RBI 1+ and run scored
1+, the WNBA first-event markets, and NFL touchdown scorers (`--league nfl`,
or `--market td` for anytime 1+/2+, `--market ftd` for first touchdown). It applies the same alert floors (SB
rides an unbroken 2% from 2026-08-30; 3% on 08-29, when DraftKings was the only
book posting the prop; the three batter props 3% from 2026-09-10, 2% at their
09-08 launch), drops the exchange books the feed never posts, and picks the
right model per market (rule 3). Past dates work too — MLB from
`edges/archive/`, WNBA from `tracking/<market>/<date>/`. WNBA output also
carries the jersey number and team city straight off the roster, which are two
of the card's required fields.

**NFL** differs in three ways. With no `--date` it shows the **whole priced
slate** (Thursday through Monday), not just today; `--date` narrows to one game
day and reads `edges/archive/` for past ones. It applies the feed's floors
(anytime 2% before 2026-09-11, 2.5% after; first TD 2%) **and the usage gate**:
a row needs `expected_total_opportunities` ≥ 3.5 (carries + targets + ¼ ×
returns) or the feed never alerted it, whatever its edge — the note line says
how many rows the gate dropped. D/ST rows are exempt from the gate; they have
no player, so a D/ST play is a card question to ask the user, not a default.
The alert feeds are `NFL TD UPDATES` (1+/2+) and `NFL FIRST TD UPDATES`
(`FTD`), each in its own channel. `opp N.N` on each line is that usage number. **MLB has no jersey number anywhere in the
curated data** — look that one up.

It is strictly read-only. Do not instead run the alert scripts in
`../jobs/odds/src/py/` to get this: their `main()` writes the day-peak state
behind the 🚨/🚀/⬇️ emoji in the real Discord feed, unconditionally, even with
the webhook unset. Running one corrupts live alerting.

When a play looks postable, ask which one to build rather than picking for
them — several qualify most nights.

**When the user pastes a line instead**, parse it, then confirm it with
`--player` before building. The tables move during the day, so a pasted alert
and the live table can legitimately disagree; the paste is what was alerted,
the table is what's true now. If they differ, say so and ask which to post.

Pasted requests arrive in Discord-alert shape, sometimes with a game-context line:

```
TEX (Eovaldi, proj) @ HOU (Brown, proj) · 8:15 pm ET · 73°F, roof closed

* Jake Burger 1+ (proj +435): +525 ftics (0.8u), +506 dk (0.7u), +500 fd/hrb (0.6u)
```

- A known football player's name, or an `NFL TD UPDATES` / `NFL FIRST TD
  UPDATES` header → NFL. There `1+` / `2+` are anytime-TD rungs and `FTD` is
  first touchdown; an `opp 7.6` tail is the usage number. Confirm with
  `find_plays.py --league nfl --player`.
- `1+` → **home run, stolen base, RBI or run scored**. All four MLB feeds call
  their rung `1+`, each in its own Discord channel, so a pasted `1+` line is
  ambiguous on its own. `2+` is ambiguous too: **home run 2+ or total bases
  2+**. `find_plays.py` names every non-HR market (`steal 1+`, `rbi 1+`,
  `run scored 1+`, `total bases 2+`); a bare-rung paste should be confirmed
  against every table that uses that rung (`--market hr/steal/rbi/runs` for
  `1+`, `--market hr/tb` for `2+`) before anything is built — the proj price
  usually settles it — and asked about if more than one hits. `first basket`,
  `first basket exact`, `2 point field goal ... for first fg by team market` →
  the basketball first-event markets.
- `(proj +435)` is the model price. `(proj +984 points, +974 fg)` gives both —
  see rule 3 for which survives.
- Each `+525 ftics (0.8u)` is price / book code / recommended units.
- `fd/hrb` means both books at that price.

Book codes (canonical map: `jobs/odds/src/py/mlb_home_runs_alerts.py`
`BOOK_CODES`; card configs spell them out uppercase):

| Code | `book` value | Code | `book` value |
|---|---|---|---|
| `dk` | `DRAFTKINGS` | `ftics` | `FANATICS` |
| `fd` | `FANDUEL` | `csr` | `CAESARS` |
| `mgm` | `BETMGM` | `hrb` | `HARD ROCK BET` |
| `br` | `BETRIVERS` | `nvg` | `NOVIG` |
| `tsb` | `THESCORE BET` | `ksh` | `KALSHI` |
| `b365` | `BET365` | | |

`nvg` and `ksh` are prediction-market exchanges. The alert feed never posts
them and `find_plays.py` drops them, so they shouldn't headline a card — the
price isn't broadly available. If one is the only board, that's a reason not to
post, not a card.

**One book or many.** *"just use the draftkings line"* / *"just use his top
edge at tsb"* → set `book`/`odds`/`stake` directly. Several prices with no
instruction → hand the builder a `books[]` list, let it collapse to the best
price, and add `· best of N books` to the `note`. If only one book is on the
board and that's notable, say so instead: `· DK is the only board posting it`.

## Step 2 — The player behind it

**Default: one call.** `player_brief.py` reads every table below and returns
the caption-ready block — season line, recent form, career profile, tonight's
sim, team context — so the schemas don't get rediscovered every session:

```bash
python3 .claude/skills/pick-post/player_brief.py --player "Javier Báez"
```

```
python3 .claude/skills/pick-post/player_brief.py --player "Chelsea Gray" --json
python3 .claude/skills/pick-post/player_brief.py --player "Kiah Stokes" --date 2026-08-04
```

**Read the `caveats` block before writing a word.** It catches the things that
have produced captions needing correction, and each one changes what the
caption may claim:

| Caveat | What it means for the copy |
|---|---|
| `projected_lineup` | The lineup is a projection, not posted — flag it to the user before posting |
| `career_vs_window` | The projection rides career profile; **do not write a recent-form power line** |
| `thin_power_sample` | There is no season power number to lead with — lead on contact or role |
| `layoff` | Season totals average across a gap; use the since-return split instead |
| `part_time` | Season rates describe a part-time role |
| `postseason` | WNBA playoffs are under way. Season and Team are regular season only — call them that. "Last game" / recent form come from the tagged Last 10 (`PO` rows count), and the series record is in the `postseason` block |
| `thin_volume_superlative` | A "team best" is false at that volume floor — qualify it or drop it |
| `steal_drought` | Steal card: the season SB total is real, but he hasn't run in a week — don't write it as current form |
| `low_attempt_rate` | Steal card: he's on base far more often than he goes — not a constant threat |
| `lineup_slot_change` | Batting somewhere other than his usual spot tonight — season R/RBI were built elsewhere in the order; don't write them as tonight's role |

**For a steal card, read the `## Baserunning` block, not the batted-ball one.**
`player_brief.py` carries the season steal line (SB, CS, success rate, steals
of second vs third), the attempt rate against how often he was actually on a
stealable base, his league rank on credited steals, and Savant sprint speed
with its percentile. Exit velo and barrel rate say nothing about a steal, and a
steal caption built out of them is a caption about the wrong skill (rule 1).
`tonight` carries `p_sb` / `p_sb_cal` / `exp_sb` alongside the HR numbers —
take `model_price_sb`.

**For a total bases, RBI or run-scored card, read `## Production` and
`## Lineup`.** These markets settle per game, so the numbers that fit them
are game counts: 2+ total bases in N of G games, a run in N, an RBI in N. Those
come as season, starts-only and last-15 rows, plus AVG/SLG/XBH by pitcher hand
and a per-club line when a trade split his season. `## Lineup` gives tonight's
batting slot and the opposing starter with his hand (RotoWire). The props lean
on different skills:

- **Total bases** — slugging and extra-base hits, split against tonight's
  starter's hand. Batted-ball quality supports it.
- **Run scored** — on-base skill and a top-of-order slot. The runs count comes
  from the boxscore, not from what he hit.
- **RBI** — the slot plus the hitters in front of him. A leadoff hitter's RBI
  line is thin by construction.

Runs and RBI follow the slot more than the hitter, which is why
`lineup_slot_change` exists.

**For an NFL touchdown card, read the whole brief — there is no single
block.** `player_brief.py --league nfl` returns the regular-season line off
play-by-play (carries, yards, targets, TDs, red-zone touches, inside-10/5
carries), last season's line, the last six games (snap share, carries,
targets, red-zone and goal-line work, TDs), career game-first and team-first
TD counts, the consensus depth chart and injury report, and the opponent's
rush/pass TDs allowed. Postseason games are excluded everywhere. Jersey number,
team and position come straight off the roster — no lookup needed.

What an anytime-TD caption leans on is **the role near the goal line**:
red-zone and goal-line touches, who gets the carries inside the 5, TDs scored.
Yards per carry is a between-the-20s number and mostly beside the point. A
first-TD caption adds the first-TD history and the opening-script role. NFL
caveats:

| Caveat | What it means for the copy |
|---|---|
| `thin_sample` | Two or three games in — quote counts ("scored in week 2"), never per-game averages |
| `backup_role` | Not the consensus starter — don't call him the lead back; write the role he has |
| `injury_status` | He's on the injury report — flag it to the user before posting |
| `depth_injury` | A same-position teammate is hurt — the usage tonight may be the story, but say it only if the report says it |
| `team_change` | Last season was with another club — those numbers weren't in this uniform (and rule 2 for the photo) |
| `below_usage_gate` | Under the feed's 3.5-opportunity gate — never alerted; don't post it |
| `not_priced` | No edge row — nothing to quote |

The `team.ranks` block is volume-guarded on purpose: rate-stat leaders are
computed over a minimum-attempts floor, and anyone who beats the number below
that floor is named. A 2-for-2 bench player leads the roster in free-throw
percentage otherwise, and quoting it as team-best is a false claim (rule 6).

MLB has no jersey number in any curated file. `player_brief.py` reports
`jersey: null` and says so; look it up once, then add it to `jerseys.json` and
it never costs a lookup again. **Only add a number you actually looked up** — a
wrong jersey goes straight onto a card.

### The tables underneath

`find_plays.py` and `player_brief.py` already read these. Go to them directly
for a market they don't cover, or to check their work.

```bash
set -a; source ~/.wnba_jobs.env; source ~/.mlb_jobs.env; source ~/.nfl_jobs.env; set +a
```

**Never recompute a stake from a projection and a price.** See rule 4 for which
column each source uses. Both leagues have the same trap in different clothes:
the tables hold raw half-Kelly, the alert posts it divided by a
standardization norm, and that norm moves.

On the MLB side, `kelly_units()` in `jobs/odds/src/py/mlb_units.py` returns
*raw* quarter-Kelly at its default `scale=1.0` — each feed passes its own
`*_UNIT_SCALE` (1 / that market's norm). Calling it bare mis-sizes every market
by a different factor, and the norms and the Kelly fraction have both changed
at cutovers (HR alone went 1.39 → 1.98 → 0.95), so there is no single right
multiplier for older dates. Every row already carries the `units_std` that was
actually alerted, alongside the `unit_norm` that produced it. Use the column.

| Need | Path (under the data root) |
|---|---|
| WNBA model prices | `$WNBA_DATA_ROOT/02_curated/wnba_first_to_score/model_odds/model_odds_<market>.csv` |
| WNBA book prices | `.../wnba_first_to_score/book_odds/book_odds_<market>.csv` |
| WNBA edges + **units** | `.../wnba_first_to_score/plays/plays_<market>.csv` — `<code>_units` per book |
| WNBA player stats | `$WNBA_DATA_ROOT/02_curated/player_game_logs/current.csv.gz` |
| WNBA rosters | `$WNBA_DATA_ROOT/02_curated/rosters/current.csv` (confirms current team, rule 2) |
| MLB HR edges + **units** | `$MLB_DATA_ROOT/02_curated/edges/home_runs_edges_current.csv` — `units_std`, `projection` |
| MLB SB edges + **units** | `$MLB_DATA_ROOT/02_curated/edges/stolen_bases_edges_current.csv` — `units_std`, `projection`, `expected_stolen_bases` |
| MLB batter-prop edges + **units** | `$MLB_DATA_ROOT/02_curated/edges/{total_bases,rbi,runs}_edges_current.csv` — `units_std`, `projection`, `expected_{total_bases,rbi,runs}` |
| MLB runs / RBI / batting slot per game | `$MLB_DATA_ROOT/01_raw/boxscores/<season>.csv` — `batting_order` is slot×100, +1.. for subs |
| MLB tonight's lineup + starters | `$MLB_DATA_ROOT/01_raw/rotowire_lineups/<date>.csv` |
| MLB steal events / chances | `$MLB_DATA_ROOT/02_curated/baserunning/steal_events/<season>.parquet`, `steal_opportunities/<season>.parquet` |
| MLB sprint speed | `$MLB_DATA_ROOT/01_raw/savant_sprint_speed/<season>.csv` |
| MLB players | `$MLB_DATA_ROOT/02_curated/players/current.csv` |
| MLB HR performance | `$MLB_DATA_ROOT/02_curated/performance/home_runs/metrics.csv` |
| NFL TD edges + **units** | `$NFL_DATA_ROOT/02_curated/edges/td_edges_current.csv` — `market` (`1+`/`2+`/`FTD`), `projection`, `units_std`, `expected_total_opportunities` |
| NFL play-by-play | `$NFL_DATA_ROOT/01_raw/pbp/<season>.csv` — filter `season_type == "REG"` |
| NFL per-game usage | `$NFL_DATA_ROOT/02_curated/touchdowns/player_game_logs/player_game_segments.parquet` — `segment == "full"` for game rows; weeks > 18 are playoffs |
| NFL first TDs | `$NFL_DATA_ROOT/02_curated/touchdowns/first_touchdowns/first_touchdowns.parquet` |
| NFL depth charts / injuries | `$NFL_DATA_ROOT/02_curated/depth_charts/{consensus,injury_status}_current.parquet` — one depth row per scope (RB, KR, PR…); use the position's |
| NFL rosters (team, jersey) | `$NFL_DATA_ROOT/01_raw/rosters/<season>.csv` — latest `week` row |
| NFL schedule (kickoff, spread) | `$NFL_DATA_ROOT/01_raw/schedules/<season>.csv` |

Market slugs: `first_basket`, `first_basket_by_team`,
`first_basket_by_team_exact`, `first_basket_exact`, `first_three_by_team`,
`first_team`, `first_team_exact`, `first_basket_type`.

Season stats for the caption come from these same roots. Say in your response
which file each number came from — past sessions did, and it's what makes the
caption checkable.

## Step 3 — Get the photo

`get_photo.py` does the fetching, validating, naming, sizing and caching. It
also reports the source's shape and which frames suit it.

**MLB is fully automatic** — img.mlbstatic.com serves an action shot per player
id, so there is nothing to search:

```bash
python3 .claude/skills/pick-post/get_photo.py --player "Riley Greene"
```

**Stolen bases are the MLB exception** — the CDN's action shot is almost
always a plate appearance (bat on shoulder, mid-swing, follow-through), and a
hitting photo over a steal pick reads as the wrong bet. Steal cards want the
player *on the bases*: a lead off the bag, a jump, a slide into second, a
head-first dive, a pop-up at the bag. So for a steal, treat MLB like WNBA below
— find a base-running frame on a page and pass it in:

```bash
python3 .claude/skills/pick-post/get_photo.py --player "Elly De La Cruz" --page "<article url>" --list
```

Only fall back to the CDN action shot if no base-running frame can be found,
and say so when handing the card over. A batting photo is not a reason to skip
the pick — it's a reason to keep looking first.

Total bases, RBI and run scored are plate-appearance markets, so the CDN's
batting shot is the right photo for them. Just check the uniform (rule 2).

**WNBA action shots need a URL from you.** The official CDN carries only
transparent headshot cutouts, and there is no action equivalent — so find a
page with a real photo (web search), then hand it over:

```bash
python3 .claude/skills/pick-post/get_photo.py --player "Chelsea Gray" --page "<article url>" --list
```

**NFL is the same as WNBA** — `get_photo.py` falls back to the roster's
static.nfl.com studio headshot, which is a last resort. Team sites
(`philadelphiaeagles.com` and siblings) and nfl.com video pages carry game
photos as `static.clubs.nfl.com` / `static.www.nfl.com` Cloudinary URLs; the
page scrape finds them at 1600px or smaller, but the CDN serves any size —
replace the transform segment with `f_jpg,q_auto,w_2400` and pass it as
`--url`. For a TD card the best frame is the player *scoring*: crossing the
goal line, diving for the pylon. A highlight page for his last TD usually has
exactly that as its thumbnail.

`--list` ranks the candidates without downloading; then re-run with
`--url "<the one you want>"`. Images on the Sinclair station CMS (news3lv and
siblings) are **auto-upscaled** — hand it an 800px thumbnail and it fetches the
2400px frame from the same path.

There is deliberately no built-in image search. Sinclair's search is
client-rendered, Bing serves a different query's results, DuckDuckGo returns a
bot challenge, and ESPN's APIs are empty or 403 — a scraper built on any of
them returns *another player's face*, which is the one failure worse than doing
it by hand. `--check` pings every resolver so rot is found deliberately.

Still true, and still your judgment:

- **Current uniform** (rule 2) — check the roster file if the trade/signing is recent.
- The WNBA headshot cutout floats cleanly on `fullbleed` against the near-black
  card, but `cover` blows it up and leaves a hard chest edge mid-card; give it
  an explicit pixel `photo_size` that lands the cut inside the scrim.
- **Avoid**: heavy dark area along the bottom (rejected once — the scrim plus a
  dark bottom kills the price row), sponsor backdrops behind the head (use
  `ticket`, which crops them away), low-res upscales.
- The user often supplies the photo (*"image is on the desktop"*) — use theirs.

## Step 4 — Build and render

Config at `cards/<player-slug>-<market>-<YYYY-MM-DD>.json`, matching the
existing slug style (`kiah-stokes-first-basket-exact-2026-08-04`,
`jhostynxon-garcia-hr-2026-08-04` — `hr` for home runs, `sb` for steals, `tb` /
`rbi` / `runs` for the batter props, spelled-out market for basketball, `td` /
`td2` / `ftd` for the NFL anytime 1+, 2+ and first-touchdown markets). `slug` matches the filename minus `.json`.

Set `"photo_preset": "auto"` on a new config and the crop starts from a sane
place for that frame and that photo's shape, instead of from the default. It
only fills in what you leave unset — an explicit `photo_pos`/`photo_size`
always wins — so it makes the *first* render usually right without taking the
decision away.

```bash
cd brand/make_social_posts && python3 build_card.py cards/<slug>.json
```

Building more than one? Add `--contact-sheet` and review them together in
`out/_contact.png` instead of opening each PNG.

Then **look at the render** and tune `photo_pos` / `photo_size` /
`photo_filter` until the crop is right — head clear of the top edge, subject out
from under the scrim, type legible over the photo. Don't hand over a card you
haven't viewed.

Delete any trial configs/photos you made along the way.

## Step 5 — Write the copy

**Instagram caption.** Rule 1 governs. First line stands alone before the fold
— a number or the player's name, never "we found value." Then a few short
performance lines, then the matchup. That is the end of it — no price line and
no closing tag block (rule 1). Let the line breaks and the numbers do the
structuring that emoji used to — rule 7 caps the whole caption at one or two,
and none is fine. No URLs in IG captions.

**Tweet** (when asked). Same facts, X register — tighter, more inside-baseball,
2–5 hashtags inline per style guide §2. Rule 7 applies here too.

**Hashtag comment.** A separate comment, not part of the caption — that's why
these run 20–30 tags where style guide §4.2 caps in-caption tags at 5–10.
Instagram's hard limit is 30; stay under it and say the count. Mix: league +
market + team/community + player name + book + brand (`#SlamDunkBets`).

Deliver captions as markdown blockquotes and the hashtag block in a fenced
code block, so the user can copy them cleanly.

---

## The fast path

Three scripts do the mechanical work; what's left is the part that needs
judgment. Spending a large model on `ls` and column-name discovery is the waste
this layout exists to remove.

| Step | Costs | Why |
|---|---|---|
| `find_plays` → `player_brief` → `get_photo` → write config → `build_card` | Cheap — it's four commands | Deterministic once the scripts exist. No model judgment at all |
| Pick among photo candidates; verify the crop on the render | Middling — needs vision | A narrow perceptual call: head clipped? subject under the scrim? type legible? |
| Captions, and the post/don't-post call | Expensive, and worth it | Where the hard rules live, and where the `caveats` block turns into prose or into a warning to the user |

**Several cards in one night** — run the script chain per play in parallel, then
write all the captions together at the end. Two cards built in sequence take
twice as long for no reason, and captions written in one pass don't
accidentally rhyme with each other.

**Re-check the price before posting.** Both leagues' tables move during the
day. A card built at 11am can be wrong by 4pm — a book drops off, a better
price appears, the model shifts. Re-run `find_plays.py --player` right before
the post goes out, not just when the work starts.

## Before you hand it over

- [ ] Caption leads with performance, not the edge (rule 1)?
- [ ] Every stat traced to a file you read (rule 6)?
- [ ] Every `caveats` entry from `player_brief.py` either respected in the copy
      or surfaced to the user?
- [ ] Current-team photo (rule 2)?
- [ ] Steal pick → base-running photo, not a plate appearance?
- [ ] Steal caption built from the baserunning block, not batted-ball power?
- [ ] TB / RBI / runs caption built from `## Production` game counts and tonight's slot?
- [ ] NFL TD caption built from goal-line role and TDs, respecting `backup_role` / `thin_sample`?
- [ ] NFL play passed the 3.5-opportunity usage gate (it's on the `find_plays` line)?
- [ ] No stake compared across markets — each market's unit is its own (rule 4)?
- [ ] Right model quoted for this market (rule 3)?
- [ ] Stake straight from `*_units` / `units_std`, never recomputed (rule 4)?
- [ ] Price re-checked against the live table if the alert was hours old?
- [ ] Frame + accent different from the last few cards (rule 5)?
- [ ] No stat/highlight text anywhere on the card — `kicker` generic, `note`
      matchup/model/books only (rule 8)?
- [ ] At most 1–2 emoji across the whole caption (rule 7)?
- [ ] Caption ends on performance — no price line, no closing tag block (rule 1)?
- [ ] Player name spelled right, card odds formatted `+1200` / `0.7u`?
- [ ] Rendered PNG actually viewed?
- [ ] Trial files cleaned up?

Flag anything the user should know before posting — an unflattering underlying
stat the caption works around, a lone-book price that may move, a note line
doing double duty. Past sessions surfaced these and it was the right call.
