# Slam Dunk Bets — pick card generator

Renders branded **1080×1350** (Instagram 4:5 portrait) pick cards from a JSON
config. Every card takes the same three things — a source photo, the price our
model expects, and the price actually on the board — and stages them
differently.

Cards are HTML/CSS screenshotted by headless Chrome, so tweaking the design
means editing CSS, not fighting an image editor.

## Frames

Five of them, picked with the `template` field. They all render one book; they
differ in how the photo is cropped and where the type sits, so a week of posts
doesn't read as one template on repeat.

| `template` | Layout | Reach for it when |
|---|---|---|
| `base` (default) | Landscape photo band, name over it, three tiles across the bottom | The house look. Action shots with room on either side |
| `fullbleed` | Photo edge to edge; pick label runs bottom-to-top up a rail on the left; one horizontal price bar | Tall or full-body shots that a landscape band would waste |
| `split` | Type column on the left, photo column on the right cut on a diagonal; prices stacked, not tiled | Portrait crops, and when you want the copy to lead |
| `ticket` | Circular photo medallion over a bet-slip receipt with a torn perforation | Junk backgrounds — the circle throws the background away |
| `poster` | Photo *contained* in a 530×930 portrait panel, name over its lower edge, model price left and available price right | Tall full-body shots the other frames would crop the head off — the panel matches the photo's own aspect instead of forcing 4:5 |

### Retired: `bigprice`

`card_bigprice.html` still builds, but **don't post cards with it.** It stages
the available price at 300px over a photo graded to `grayscale(.55)
brightness(.52)`, which turns the player into atmosphere and throws away the
action shot the rest of this workflow exists to crop well. Fighting the grading
back with `photo_filter: brightness(2.5)` gets you a legible jersey on a murky
background, not a good card. Retired 2026-08-10. If the price really is the
story, use a frame that keeps the player legible and let the copy carry it.

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

### Reviewing a batch

```bash
python3 build_card.py cards/a.json cards/b.json cards/c.json --contact-sheet
```

Tiles the cards it just built into `out/_contact.png`, three across, each
labelled with its slug. At a third scale the crop, the scrim and the type are
all still judgeable, so a five-card night is one image to look at instead of
five. Rendered through the same headless Chrome as the cards, so it adds no
dependency.

## Photos

`photos/` is gitignored except for `sources.json`, which records where each
photo came from — the URL, the resolver that found it, and its dimensions.
Fetch them with the skill's helper rather than by hand:

```bash
python3 ../../.claude/skills/pick-post/get_photo.py --player "Riley Greene"
```

MLB action shots resolve automatically from the player id. WNBA needs a page or
image URL, because the official CDN only carries headshot cutouts. See
`.claude/skills/pick-post/SKILL.md` step 3.

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

Also required on `fullbleed`, `split`, `ticket` and `poster`:

| Field | Example | Notes |
|---|---|---|
| `pick_text` | `HOME RUN` | The headline. Required so the copy is deliberate per sport — `HOME RUN`, `FIRST BASKET`, `3+ THREES`. `base` defaults it to `HOME RUN` |

Optional on every frame:

| Field | Default | Notes |
|---|---|---|
| `template` | `base` | One of the six above |
| `accent` | `green` (`cyan` on `fullbleed`, `pink` on `split`) | `green`, `cyan` or `pink` — see below |
| `league` | `MLB` | Cyan pill, top right. `NBA`, `WNBA`, whatever |
| `tag_sub` | `PLAYER PROP` (`HOME RUN PROP` on `base`) | Under the league pill |
| `kicker` | `TODAY'S PLAY` | Small accent line above the name |
| `pick_label` | `THE PICK` (`THE PICK — TO GO YARD` on `base`) | The `--accent2` line above the pick |
| `chip` | none (`1+` on `base`) | Filled accent chip next to the pick text. Leave it out and nothing renders — no empty box |
| `note` | none | Optional grey explainer line under the pick |
| `photo_pos` | `50% 12%` | CSS `background-position` |
| `photo_size` | `cover` | CSS `background-size`; use e.g. `118%` to zoom in |
| `photo_filter` | `none` | CSS `filter` for per-photo grading |
| `photo_preset` | none | `auto`, or a `<template>-<shape>` key — see below |

### `photo_preset`

Every frame crops to a different window, so the same photo needs different
`photo_pos`/`photo_size` in each one — which is why a new card usually costs a
render or two before the crop is right. `photo_preset` supplies a sane starting
point for that combination:

```json
"photo_preset": "auto"
```

`auto` measures the photo and classifies it **tall** (aspect < 0.80), **square**
(0.80–1.20) or **landscape** (> 1.20), then applies the preset for that shape
and the chosen template. Name a key directly — `poster-tall`, `ticket-landscape`
— to force one.

A preset only ever fills in what the config leaves out: an explicit
`photo_pos`, `photo_size` or `photo_filter` always wins. It is also opt-in, so
every config written before it existed renders byte-for-byte identically.

It gets the first render close. It does not replace looking at the result.

`get_photo.py` prints the shape and the frames that suit it when it saves a
photo, so the template choice and the preset agree by construction.

### `base` only

| Field | Default | Notes |
|---|---|---|
| `pick_sub` | `Anytime home run · …` | Grey explainer line (the `note` equivalent) |
| `stake_sub` | `units` | Small text under the stake |

### Market copy

No frame knows what market it's rendering — `tag_sub`, `pick_label`, `chip` and
`pick_text` carry that, and they only read as a set if we write them the same
way every time. The first-events markets we post most:

| Market | `tag_sub` | `pick_label` | `chip` | `pick_text` |
|---|---|---|---|---|
| First basket (whole game) | `FIRST BASKET PROP` | `THE PICK — TO SCORE FIRST` | `1ST` | `FIRST BASKET` |
| First basket by team | `FIRST BASKET PROP` | `THE PICK — <CITY>'S FIRST` | `1ST` | `FIRST BASKET` |
| First basket by team **exact** | `FIRST BASKET EXACT` | `THE PICK — <CITY>'S FIRST, ON A <METHOD>` | `2PT` / `3PT` / `LAYUP` / `FT` | `FIRST BASKET` |
| First three by team | `FIRST THREE PROP` | `THE PICK — <CITY>'S FIRST THREE` | `1ST 3` | `FIRST THREE` |

The exact-method markets are the reason `chip` exists as a separate field —
the method belongs in the chip, not welded into `pick_text`, so the headline
stays the same size card to card while the method reads as the qualifier it is.

The `note` line carries the model price — **the model the market actually
settles on**, which is not the same one every time:

- **Points-settled** markets (plain first basket, first basket by team) can be
  won on a free throw, so they carry both: `Model: +961 points · +888 FG`,
  points first.
- **Exact-method** markets (`fg2`, `fg3`) only settle on a made field goal, so
  the points model is noise. Carry the FG number alone: `Model: +653`. Quoting
  a points price next to it invites the reader to compare two numbers only one
  of which grades the bet.

`stake` is not something to derive here — take the recommended units straight
from the model outputs for that play and market, and quote the model that
matches it.

Add `· DK is the only board posting it` (or `· best of 8 books`) when the book
count is itself part of the story.

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
| `card_bigprice.html` | Price-as-hero over a graded backdrop — **retired**, see Frames |
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
