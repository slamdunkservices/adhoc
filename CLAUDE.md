# adhoc

One-off analysis and brand tooling for SDBS. Nothing here is scheduled — the
cron jobs live in `../jobs` and the models in `../models`. This repo is where
things get looked at once, or made once.

Three subprojects, each with its own README:

| Directory | What it is |
|---|---|
| `odds_analysis/` | Turns the odds scraper's CSV archive into a parquet lake, then answers questions about it: line moves, best price, prop trends, book holds |
| `brand/make_social_posts/` | `build_card.py` renders a JSON config into a 1080×1350 PNG post card via one of the `card_*.html` templates |
| `bet_tracking/` | R scripts over the tracked-bet spreadsheets in `NBA/` and `WNBA/` |

## Data roots

**Data lives outside this repo.** Do not commit data files, and do not hardcode
absolute paths — read them from the environment.

Every league's paths are defined in a host-local env file, `~/.<league>_jobs.env`,
sourced by the job scripts in `../jobs`. These files are not in git and are the
single source of truth for where data lives on this machine. To pick up a
league's paths in a shell:

```bash
set -a; source ~/.wnba_jobs.env; set +a
```

The env files present here:

| File | Defines |
|---|---|
| `~/.atp_jobs.env` | `ATP_DATA_ROOT` |
| `~/.f1_jobs.env` | `F1_DATA_ROOT`, `MODELS_ROOT` |
| `~/.mlb_jobs.env` | `MLB_DATA_ROOT`, `MODELS_ROOT` |
| `~/.nascar_jobs.env` | `NASCAR_DATA_ROOT` |
| `~/.nfl_jobs.env` | `NFL_DATA_ROOT`, `MODELS_ROOT` |
| `~/.nhl_jobs.env` | `NHL_DATA_ROOT`, `MODELS_ROOT` |
| `~/.pga_jobs.env` | `PGA_DATA_ROOT`, `MODELS_ROOT` |
| `~/.wnba_jobs.env` | `WNBA_DATA_ROOT`, `MODELS_ROOT`, `PATH_ACCESS_KEY_ODDS_GETTER` |
| `~/.odds_jobs.env` | scraper session secrets only — no roots |

NBA follows the same convention (`~/.nba_jobs.env` → `NBA_DATA_ROOT`), but that
file does not exist on this machine yet; `../jobs/nba/_lib.sh` skips it when
absent. `../jobs/<league>/.env.example` documents each league's full variable set.

These files also carry secrets — GitHub tokens, Discord webhooks, service-account
paths. Never echo one wholesale, paste its contents into a file, or commit it.

Each `*_DATA_ROOT` mirrors its `gs://sdbs_<league>` bucket. `MODELS_ROOT` points
at the models repo.

### Paths this repo uses

`odds_analysis/` reads the scraper archive, which is *not* a `*_DATA_ROOT` — it
has its own variables:

- `$ODDS_RAW` — scraper output, default `~/Desktop/files/odds_getter`. Also `--raw`
- `$ODDS_LAKE` — the parquet lake, default `./data` (gitignored). Also `--lake`

`bet_tracking/` reads spreadsheets under `bet_tracking/NBA/` and
`bet_tracking/WNBA/` by repo-relative path; run the R scripts with
`bet_tracking/` as the working directory.

`brand/make_social_posts/` is self-contained — configs in `cards/`, output in
`out/`.

## Conventions

- No pip installs. `odds_analysis/` and `build_card.py` run on the system
  `python3` against the pandas/pyarrow already there.
- `data/` and `out/` directories are rebuildable and gitignored.
- Changing anything about odds math, keys, or CSV parsing means changing
  `odds_analysis/oddslib.py` — run `python3 oddslib.py` after; it self-tests.
