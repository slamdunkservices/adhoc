# Slam Dunk Bets — pick card generator

Renders branded **1080×1350** (Instagram 4:5 portrait) pick cards from a JSON
config. Every card takes the same three things — a source photo, the price our
model expects, and the price actually on the board — and stages them
differently.

Cards are HTML/CSS screenshotted by headless Chrome, so tweaking the design
means editing CSS, not fighting an image editor.

## Frames

Six of them, picked with the `template` field. They all render one book; they
differ in how the photo is cropped and where the type sits, so a week of posts
doesn't read as one template on repeat.

| `template` | Layout | Reach for it when |
|---|---|---|
| `base` (default) | Landscape photo band, name over it, three tiles across the bottom | The house look. Action shots with room on either side |
| `fullbleed` | Photo edge to edge; pick label runs bottom-to-top up a rail on the left; one horizontal price bar | Tall or full-body shots that a landscape band would waste |
| `split` | Type column on the left, photo column on the right cut on a diagonal; prices stacked, not tiled | Portrait crops, and when you want the copy to lead |
| `ticket` | Circular photo medallion over a bet-slip receipt with a torn perforation | Junk backgrounds — the circle throws the background away |
| `bigprice` | The available price at 300px, photo graded down to a backdrop behind it | The number is the story |
| `poster` | Photo *contained* in a 530×930 portrait panel, name over its lower edge, model price left and available price right | Tall full-body shots the other frames would crop the head off — the panel matches the photo's own aspect instead of forcing 4:5 |

## Usage

```bash
python3 build_card.py cards/yordan-alvarez-2026-07-25.json
```

Batch, and reveal the first result in Finder:

```bash
python3 build_card.py cards/*.json --open
```

Renders land in `out/<slug>.png`. The builder asserts the output is exactly
1080×1350 and fails loudly if a template and the builder drift apart.

## Making a new card

1. Drop the photo in `photos/`.
2. Copy an existing config in `cards/` and edit the fields.
3. Render, look at it, and tune the three `photo_*` fields (see below).

Step 3 is the only fiddly part — every photo crops differently, and each frame
crops to a different shape.

## Config fields

Required on every frame:

| Field | Example | Notes |
|---|---|---|
| `slug` | `yordan-alvarez-hr-2026-07-25` | Output filename, no extension |
| `photo` | `photos/yordan-alvarez.jpg` | Path relative to this folder; jpg/png/webp |
| `name` | `YORDAN ALVAREZ` | Uppercase. ~16 chars before it crowds `base`; the other frames wrap |
| `team` | `HOUSTON` | City, not nickname |
| `jersey` | `44` | Used in the meta line *and* the big background watermark |
| `proj` | `+237` | Our model's fair odds — the **expected** price |
| `book` | `KALSHI` | Book/exchange name, uppercase |
| `odds` | `+257` | The **available** price we're taking |
| `stake` | `0.8u` | |

Also required on `fullbleed`, `split`, `ticket`, `bigprice` and `poster`:

| Field | Example | Notes |
|---|---|---|
| `pick_text` | `HOME RUN` | The headline. Required so the copy is deliberate per sport — `HOME RUN`, `FIRST BASKET`, `3+ THREES`. `base` defaults it to `HOME RUN` |

Optional on every frame:

| Field | Default | Notes |
|---|---|---|
| `template` | `base` | One of the six above |
| `accent` | `green` (`cyan` on `fullbleed`/`bigprice`, `pink` on `split`) | `green`, `cyan` or `pink` — see below |
| `league` | `MLB` | Cyan pill, top right. `NBA`, `WNBA`, whatever |
| `tag_sub` | `PLAYER PROP` (`HOME RUN PROP` on `base`) | Under the league pill |
| `kicker` | `TODAY'S PLAY` | Small accent line above the name |
| `pick_label` | `THE PICK` (`THE PICK — TO GO YARD` on `base`) | The `--accent2` line above the pick |
| `chip` | none (`1+` on `base`) | Filled accent chip next to the pick text. Leave it out and nothing renders — no empty box |
| `note` | none | Optional grey explainer line under the pick. Not used by `bigprice`'s layout |
| `photo_pos` | `50% 12%` | CSS `background-position` |
| `photo_size` | `cover` | CSS `background-size`; use e.g. `118%` to zoom in |
| `photo_filter` | `none` | CSS `filter` for per-photo grading |

### `base` only

| Field | Default | Notes |
|---|---|---|
| `pick_sub` | `Anytime home run · …` | Grey explainer line (the `note` equivalent) |
| `stake_sub` | `units` | Small text under the stake |

### Shopping multiple books

Every frame renders one book. If the pick was shopped, hand the config a
`books` list instead of `book`/`odds`/`stake` and the builder collapses it to
the single best price:

```json
"books": [
  { "book": "BETRIVERS", "odds": "+800", "stake": "1.3u", "best": true },
  { "book": "BETMGM",    "odds": "+750", "stake": "1.0u" },
  { "book": "FANATICS",  "odds": "+700", "stake": "0.6u" }
]
```

Mark one entry `"best": true` to force it. Mark none and the longest price
wins, compared on decimal odds — so `+800` beats `+750`, and `-110` beats
`-140`. An explicit `book`/`odds`/`stake` on the config always overrides the
list.

## Accents

Neon green `#39ff14`, electric cyan `#00e5ff` and magenta `#ff3ea5` are all
brand colors; `accent` picks which one carries the pick and the value price on
a given card. Each accent is paired with a contrasting partner used for the
`pick_label` line, so no card ever comes out monochrome:

| `accent` | Pick / value / kicker / chip / glows | `pick_label` |
|---|---|---|
| `green` | `#39ff14` | magenta |
| `cyan` | `#00e5ff` | magenta |
| `pink` | `#ff3ea5` | cyan |

Three things never change color: the `SLAM DUNK [BETS]` lockup, the
`slamdunk.bet` wordmark in the footer, and the card frame. Those stay neon
green so a pink card still reads as ours.

## Tuning the photo

Each frame crops to a different window, so a `photo_pos` tuned for one frame is
usually wrong for another:

| Frame | Photo window | What to watch |
|---|---|---|
| `base` | ~1036×748 landscape band | Tall sources get cropped to a horizontal band. `photo_pos`'s **second** value picks it: `0%` is the top of the photo, `100%` the bottom |
| `fullbleed` | the full 1080×1350 | Wide sources get cropped hard on the sides. `cover` is almost always right; the bottom 40% sits under the scrim so put the subject high |
| `split` | ~592×1306 portrait column | Narrow. Keep the subject right of centre — the diagonal eats the lower-left corner of the photo, and the left edge is veiled dark |
| `ticket` | 462×462 circle | Square crop. Aim the face at roughly `50% 15%`; everything outside the circle is gone, which is the point |
| `poster` | 530×930 panel (aspect ≈0.57) | Sized for a tall portrait source, so `cover` crops almost nothing. Aim `photo_pos` near `50% 6%` and check the head clears the top edge; the bottom ~20% sits under the name scrim. Sides of the card show the same photo blurred, so a busy crowd still reads as texture, not detail |
| `bigprice` | the full 1080×1350, then graded | The template already applies `grayscale(.55) brightness(.52)` on top of your `photo_filter` — don't darken it twice or the photo disappears |

General guidance, still true:

- **Tight headshot** (e.g. Mookie) — `cover` is usually right; nudge
  `photo_pos` until the face sits in the upper two-thirds.
- **Full-body action shot** (e.g. Yordan) — on `base`, `cover` leaves the
  subject small with dead space under them; zoom with `photo_size: 118%` and
  pull `photo_pos` toward `0%`. On `fullbleed` the tall window handles it
  without zooming.
- **Busy or bright crowd** — dial it back with `photo_filter`, e.g.
  `brightness(0.88) saturate(0.90) contrast(1.07)`, so the white type and the
  accent stay dominant. Also helps bury stadium ad boards, which the bottom
  scrim otherwise only partly hides.
- **Press-conference shot** (e.g. Caitlin Clark) — the sponsor backdrop sits
  right behind the subject's head where no scrim reaches. `ticket` solves this
  outright by cropping to a circle; on the other frames, zoom past the backdrop
  *and* grade it down.

If a percentage `photo_size` looks like it's repeating, it isn't — every frame
sets `background-repeat:no-repeat`. It's cropping.

## Brand

Pulled from the [site repo](../../slamdunkservices.github.io): neon green
`#39ff14`, electric cyan `#00e5ff`, magenta `#ff3ea5` on near-black `#0a0a0a`,
Barlow Condensed type, and the dashed trajectory arc from the logo.

Every card carries `slamdunk.bet` and `21+ · Gamble responsibly ·
1-800-GAMBLER` in the footer — keep it there.

## Files

| Path | Purpose |
|---|---|
| `build_card.py` | The generator |
| `card_base.html` | Default frame — `{{PLACEHOLDER}}` fields, plus `__FONTS__` / `__IMG__` injection points |
| `card_fullbleed.html` | Edge-to-edge photo, vertical rail, one price bar |
| `card_split.html` | Diagonal split, type left / photo right |
| `card_ticket.html` | Circular medallion over a bet-slip receipt |
| `card_bigprice.html` | Price-as-hero over a graded backdrop |
| `card_poster.html` | Contained portrait panel, prices flanking it left and right |
| `fonts.css` | Barlow Condensed 500/600/700/800, base64-embedded |
| `fetch_fonts.sh` | Regenerates `fonts.css` (only needed to add weights) |
| `cards/*.json` | One config per card |
| `photos/` | Source photos |
| `out/` | Rendered PNGs |

Fonts are embedded as base64 so renders are deterministic and work offline —
no flash of fallback type mid-screenshot. Only 500/600/700/800 are available;
a template asking for 400 or 900 gets a synthesized weight that renders soft.

### Adding a frame

Write `card_<name>.html` next to the others, copying the `:root` palette, the
`.accent-*` blocks, the `.frame` and the footer from an existing one. Register
it in `TEMPLATES` in `build_card.py` with `file` / `required` / `defaults` /
`fields`. Tokens are `{{UPPERCASE}}` versions of the lowercase keys in
`fields` — `COMMON_FIELDS` covers the shared set, and any `{{TOKEN}}` the
builder doesn't substitute trips the drift guard at render time.

## Requirements

Google Chrome at `/Applications/Google Chrome.app`, Python 3, and `sips`
(macOS built-in). No pip installs.
