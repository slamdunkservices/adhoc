# Slam Dunk Bets — pick card generator

Renders branded **1080×1350** (Instagram 4:5 portrait) pick cards from a JSON
config. Each card pairs a player photo with the model projection, the book
line, and the stake — with the book tile highlighted as the value play.

Cards are HTML/CSS screenshotted by headless Chrome, so tweaking the design
means editing CSS, not fighting an image editor.

Two templates, picked with the `template` field:

| `template` | Use when | Layout |
|---|---|---|
| `base` (default) | One book, one stake | Three tiles: model proj · the book · stake |
| `multibook` | The same pick shopped at 2–3 books | Model-proj bar, then one tile per book with its own price and stake |

## Usage

```bash
python3 build_card.py cards/yordan-alvarez-2026-07-25.json
```

Batch, and reveal the first result in Finder:

```bash
python3 build_card.py cards/*.json --open
```

Renders land in `out/<slug>.png`. The builder asserts the output is exactly
1080×1350 and fails loudly if the template and config drift apart.

## Making a new card

1. Drop the photo in `photos/`.
2. Copy an existing config in `cards/` and edit the fields.
3. Render, look at it, and tune the three `photo_*` fields (see below).

Step 3 is the only fiddly part — every photo crops differently.

## Config fields

Shared by both templates — required:

| Field | Example | Notes |
|---|---|---|
| `slug` | `yordan-alvarez-hr-2026-07-25` | Output filename, no extension |
| `photo` | `photos/yordan-alvarez.jpg` | Path relative to this folder; jpg/png/webp |
| `name` | `YORDAN ALVAREZ` | Uppercase. Fits ~16 chars before it crowds the frame |
| `team` | `HOUSTON` | City, not nickname |
| `jersey` | `44` | Used in the meta line *and* the big background watermark |
| `proj` | `+237` | Our model's fair odds |

Shared — optional:

| Field | Default | Notes |
|---|---|---|
| `template` | `base` | `base` or `multibook` |
| `league` | `MLB` / `WNBA` | Cyan pill, top right |
| `tag_sub` | `HOME RUN PROP` / `FIRST BASKET PROP` | Under the league pill |
| `kicker` | `TODAY'S PLAY` | Small green line above the name |
| `pick_label` | `THE PICK — TO GO YARD` / `… TO SCORE FIRST` | Magenta line above the pick |
| `chip` | `1+` / `1ST` | Green chip |
| `pick_text` | `HOME RUN` / `FIRST BASKET` | Big headline next to the chip |
| `photo_pos` | `50% 12%` | CSS `background-position` |
| `photo_size` | `cover` | CSS `background-size`; use e.g. `118%` to zoom in |
| `photo_filter` | `none` | CSS `filter` for per-photo grading |

### `base` only

| Field | Default | Notes |
|---|---|---|
| `book` | *required* | Book/exchange name, uppercase |
| `odds` | *required* | The price we're taking — rendered in the green value tile |
| `stake` | *required* | e.g. `0.8u` |
| `pick_sub` | `Anytime home run · …` | Grey explainer line |
| `stake_sub` | `units` | Small text under the stake |

### `multibook` only

`books` (required) is a list of 2–3 entries, rendered left to right. Each needs
`book`, `odds`, and `stake`; **exactly one** must carry `"best": true` — it gets
the green treatment and the `▲ BEST PRICE` ribbon. List them best price first.

```json
"books": [
  { "book": "BETRIVERS", "odds": "+800", "stake": "1.3u", "best": true },
  { "book": "BETMGM",    "odds": "+750", "stake": "1.0u" },
  { "book": "FANATICS",  "odds": "+700", "stake": "0.6u" }
]
```

| Field | Default | Notes |
|---|---|---|
| `proj_note` | `fair odds · every book below is priced *longer*` | Right side of the model bar; inline HTML allowed (`<em>` renders green) |
| `total_line` | Computed | Grey line under the tiles. Default sums the stakes: *Total exposure 2.9u across 3 books · take the best price you have* |

Book names run wide — `BETRIVERS` is about the limit at three across before the
label wraps.

### Tuning the photo

The hero window is landscape-ish — ~1036×748 on `base`, ~1036×648 on
`multibook`, which gives up height to the extra book tiles — so tall portrait
source photos get cropped to a horizontal band. `photo_pos`'s **second** value
picks the band: `0%` is the top of the photo, `100%` the bottom.

- **Tight headshot** (e.g. Mookie) — `cover` is usually right; nudge
  `photo_pos` until the face sits in the upper two-thirds.
- **Full-body action shot** (e.g. Yordan) — `cover` leaves the subject small
  with dead space under them. Zoom with `photo_size: 118%` and pull
  `photo_pos` toward `0%` to frame helmet-through-hips.
- **Busy or bright crowd** — dial it back with `photo_filter`, e.g.
  `brightness(0.88) saturate(0.90) contrast(1.07)`, so the white type and
  neon-green tile stay dominant. Also helps bury stadium ad boards, which the
  bottom scrim otherwise only partly hides.
- **Press-conference shot** (e.g. Caitlin Clark) — the sponsor backdrop sits
  right behind the subject's head where no scrim reaches. Zoom past it
  (`photo_size: 114%`) *and* grade it down; either alone leaves the logos
  fighting the headline.

## Brand

Pulled from the [site repo](../../slamdunkservices.github.io): neon green
`#39ff14`, electric cyan `#00e5ff`, magenta `#ff3ea5` on near-black `#0a0a0a`,
Barlow Condensed type, and the dashed home-run trajectory arc from the logo.

Every card carries `slamdunk.bet` and `21+ · Gamble responsibly ·
1-800-GAMBLER` in the footer — keep it there.

## Files

| Path | Purpose |
|---|---|
| `build_card.py` | The generator |
| `card_base.html` | Single-book template — `{{PLACEHOLDER}}` fields, plus `__FONTS__` / `__IMG__` injection points |
| `card_multibook.html` | Multi-book template; same conventions, plus `{{BOOK_TILES}}` |
| `fonts.css` | Barlow Condensed 500/600/700/800, base64-embedded |
| `fetch_fonts.sh` | Regenerates `fonts.css` (only needed to add weights) |
| `cards/*.json` | One config per card |
| `photos/` | Source photos |
| `out/` | Rendered PNGs |

Fonts are embedded as base64 so renders are deterministic and work offline —
no flash of fallback type mid-screenshot.

## Requirements

Google Chrome at `/Applications/Google Chrome.app`, Python 3, and `sips`
(macOS built-in). No pip installs.
