#!/usr/bin/env python3
"""
Slam Dunk Bets pick-card generator.

Renders a branded 1080x1350 (Instagram 4:5 portrait) pick card from a JSON
config, using headless Chrome to screenshot an HTML/CSS template.

Usage:
    python3 build_card.py cards/yordan-alvarez-2026-07-25.json
    python3 build_card.py cards/*.json          # batch
    python3 build_card.py cards/foo.json --open # reveal in Finder when done

Output lands in out/<slug>.png. See README.md for the config field reference.
"""
import base64
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(ROOT, "out")
FONTS = os.path.join(ROOT, "fonts.css")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

CARD_W, CARD_H = 1080, 1350

# Colorway for the pick/value elements. The brand lockup, the footer, and the
# card frame stay neon green on every card — those aren't up for grabs.
ACCENTS = ("green", "cyan", "pink")

# Shared by every template: photo framing / grading, tuned per photo.
PHOTO_DEFAULTS = {
    "photo_pos": "50% 12%",
    "photo_size": "cover",
    "photo_filter": "none",
}
PHOTO_FIELDS = ("photo_pos", "photo_size", "photo_filter")

# Source shapes, by aspect ratio. Which frames suit which shape is the whole
# subject of README "Tuning the photo"; this is that table in code.
SHAPES = ((0.00, 0.80, "tall"), (0.80, 1.20, "square"), (1.20, 9.99, "landscape"))

# Starting crops per (template, source shape). Set "photo_preset": "auto" on a
# config and the shape is measured from the file; name a key like
# "poster-tall" to force one. Explicit photo_pos/photo_size/photo_filter in the
# config always beat the preset, so this only ever fills in what you left out.
#
# These are opinionated starting points, not answers — the point is to make the
# FIRST render usually right, not to remove the look-at-it step. Presets are
# opt-in precisely so that every config already in cards/ renders byte-identical.
PHOTO_PRESETS = {
    # 530x930 portrait panel; a tall source barely crops, so zoom a little and
    # keep the head clear of the top edge.
    "poster-tall":       {"photo_pos": "50% 8%",  "photo_size": "125%"},
    "poster-square":     {"photo_pos": "50% 10%", "photo_size": "150%"},
    "poster-landscape":  {"photo_pos": "50% 22%", "photo_size": "185%"},
    # 462px circle. README: aim the face at roughly 50% 15%.
    "ticket-tall":       {"photo_pos": "50% 12%", "photo_size": "135%"},
    "ticket-square":     {"photo_pos": "50% 15%", "photo_size": "125%"},
    "ticket-landscape":  {"photo_pos": "50% 15%", "photo_size": "165%"},
    # ~1036x748 landscape band. A tall source needs zoom and a pull upward.
    "base-tall":         {"photo_pos": "50% 8%",  "photo_size": "118%"},
    "base-square":       {"photo_pos": "50% 18%", "photo_size": "112%"},
    "base-landscape":    {"photo_pos": "50% 28%", "photo_size": "cover"},
    # Full 1080x1350; the bottom 40% sits under the scrim, so put the subject high.
    "fullbleed-tall":    {"photo_pos": "50% 6%",  "photo_size": "cover"},
    "fullbleed-square":  {"photo_pos": "50% 10%", "photo_size": "cover"},
    "fullbleed-landscape": {"photo_pos": "50% 12%", "photo_size": "150%"},
    # ~592x1306 column, diagonal eats the lower left — keep the subject right.
    "split-tall":        {"photo_pos": "60% 10%", "photo_size": "cover"},
    "split-square":      {"photo_pos": "62% 14%", "photo_size": "130%"},
    "split-landscape":   {"photo_pos": "64% 20%", "photo_size": "165%"},
}

# Shared by every template: the pick itself. Templates may add to these, and
# `base` overrides most of them with its home-run copy.
COMMON_DEFAULTS = {
    "accent": "green",
    "league": "MLB",
    "tag_sub": "PLAYER PROP",
    "kicker": "TODAY'S PLAY",
    "pick_label": "THE PICK",
    "chip": "",
    "note": "",
}
COMMON_REQUIRED = ("slug", "photo", "name", "team", "jersey",
                   "proj", "book", "odds", "stake")
# A superset is fine — substituting a token a template doesn't use is a no-op.
# The reverse (a token nothing substitutes) trips the drift guard in build().
COMMON_FIELDS = ("accent", "league", "tag_sub", "kicker", "name", "team",
                 "jersey", "pick_label", "chip_html", "pick_text", "note_html",
                 "proj", "book", "odds", "stake")

# Every frame takes one expected price (`proj`) and one available price
# (`odds`). They differ in how the photo is cropped and where the type sits.
TEMPLATES = {
    # Landscape photo band, name over it, three tiles across the bottom.
    "base": {
        "file": "card_base.html",
        "required": COMMON_REQUIRED,
        "defaults": {
            "tag_sub": "HOME RUN PROP",
            "pick_label": "THE PICK — TO GO YARD",
            "chip": "1+",
            "pick_text": "HOME RUN",
            "pick_sub": "Anytime home run · book line beats our model = value",
            "stake_sub": "units",
        },
        "fields": COMMON_FIELDS + ("pick_sub", "stake_sub"),
    },
    # Photo edge to edge, vertical rail down the left, one price bar.
    "fullbleed": {
        "file": "card_fullbleed.html",
        "required": COMMON_REQUIRED + ("pick_text",),
        "defaults": {"accent": "cyan"},
        "fields": COMMON_FIELDS,
    },
    # Type column on the left, angled photo column on the right.
    "split": {
        "file": "card_split.html",
        "required": COMMON_REQUIRED + ("pick_text",),
        "defaults": {"accent": "pink"},
        "fields": COMMON_FIELDS,
    },
    # Circular photo medallion over a bet-slip receipt.
    "ticket": {
        "file": "card_ticket.html",
        "required": COMMON_REQUIRED + ("pick_text",),
        "defaults": {"accent": "green"},
        "fields": COMMON_FIELDS,
    },
    # Contained portrait panel, the two prices flanking it left and right.
    "poster": {
        "file": "card_poster.html",
        "required": COMMON_REQUIRED + ("pick_text",),
        "defaults": {"accent": "green"},
        "fields": COMMON_FIELDS,
    },
    # RETIRED 2026-08-10 — the price-as-hero look buries the player behind a
    # graded photo. Kept so old configs still render; don't pick it for new
    # cards. See README §Frames.
    "bigprice": {
        "file": "card_bigprice.html",
        "required": COMMON_REQUIRED + ("pick_text",),
        "defaults": {"accent": "cyan"},
        "fields": COMMON_FIELDS,
    },
}

# Photos are JPEG unless the extension says otherwise.
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp"}


def die(msg):
    sys.exit("error: " + msg)


def wrap(css_class, value, tag="span"):
    """Markup for an optional bit of copy — nothing at all when it's empty."""
    if not value:
        return ""
    return '<%s class="%s">%s</%s>' % (tag, css_class, value, tag)


def to_decimal(odds, cfg_path):
    """American odds as a decimal payout multiplier, for comparing prices."""
    m = re.match(r"\s*([+-]?)(\d+(?:\.\d+)?)\s*$", str(odds))
    if not m or float(m.group(2)) == 0:
        die("%s: %r is not American odds (e.g. \"+325\" or \"-110\")"
            % (cfg_path, odds))
    num = float(m.group(2))
    return 100.0 / num + 1.0 if m.group(1) == "-" else num / 100.0 + 1.0


def pick_best_book(books, cfg_path):
    """Collapse a list of books down to the single best available price.

    An explicit "best": true wins; otherwise the longest price does.
    """
    if not isinstance(books, list) or not books:
        die("%s: `books` must be a non-empty list" % cfg_path)

    for b in books:
        missing = [k for k in ("book", "odds", "stake") if not b.get(k)]
        if missing:
            die("%s: book entry %r is missing %s"
                % (cfg_path, b.get("book", "?"), ", ".join(missing)))

    flagged = [b for b in books if b.get("best")]
    if len(flagged) > 1:
        die("%s: %d books are marked \"best\": true — mark one, or none and "
            "let the longest price win" % (cfg_path, len(flagged)))

    best = flagged[0] if flagged else max(
        books, key=lambda b: to_decimal(b["odds"], cfg_path))
    return {k: best[k] for k in ("book", "odds", "stake")}


def image_size(path):
    """(width, height) via sips, which this script already shells out to."""
    out = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", path],
                         capture_output=True, text=True).stdout
    w = h = None
    for line in out.splitlines():
        if "pixelWidth:" in line:
            w = int(line.split(":")[1])
        if "pixelHeight:" in line:
            h = int(line.split(":")[1])
    return w, h


def resolve_preset(cfg, kind, photo_path, cfg_path):
    """The photo_* values a preset contributes, or {} when none is asked for."""
    want = cfg.get("photo_preset")
    if not want:
        return {}
    if want != "auto":
        if want not in PHOTO_PRESETS:
            die("%s: unknown photo_preset %r — use \"auto\" or one of %s"
                % (cfg_path, want, ", ".join(sorted(PHOTO_PRESETS))))
        return dict(PHOTO_PRESETS[want])

    w, h = image_size(photo_path)
    if not w or not h:
        die("%s: photo_preset \"auto\" needs the photo's dimensions, and sips "
            "could not read %s" % (cfg_path, photo_path))
    aspect = w / h
    shape = next((s for lo, hi, s in SHAPES if lo <= aspect < hi), "landscape")
    key = "%s-%s" % (kind, shape)
    if key not in PHOTO_PRESETS:
        return {}
    return dict(PHOTO_PRESETS[key])


def build(cfg_path):
    with open(cfg_path) as fh:
        cfg = json.load(fh)

    kind = cfg.get("template", "base")
    if kind not in TEMPLATES:
        die("%s: unknown template %r — pick one of %s"
            % (cfg_path, kind, ", ".join(sorted(TEMPLATES))))
    tpl = TEMPLATES[kind]

    # Every frame renders one book. Hand it a shopped list and it takes the
    # best price, unless the card names a book/odds/stake explicitly.
    if cfg.get("books"):
        for key, val in pick_best_book(cfg["books"], cfg_path).items():
            cfg.setdefault(key, val)

    missing = [k for k in tpl["required"] if not cfg.get(k)]
    if missing:
        die("%s is missing required field(s): %s" % (cfg_path, ", ".join(missing)))

    photo = os.path.join(ROOT, cfg["photo"])
    if not os.path.exists(photo):
        die("photo not found: %s (referenced by %s)" % (photo, cfg_path))

    c = dict(PHOTO_DEFAULTS)
    c.update(COMMON_DEFAULTS)
    c.update(tpl["defaults"])
    # Between the template's defaults and the config's own values: a preset can
    # only fill in what the config left unset.
    c.update(resolve_preset(cfg, kind, photo, cfg_path))
    c.update(cfg)

    if c["accent"] not in ACCENTS:
        die("%s: unknown accent %r — pick one of %s"
            % (cfg_path, c["accent"], ", ".join(ACCENTS)))

    c["chip_html"] = wrap("chip", c["chip"])
    c["note_html"] = wrap("note", c["note"], "div")

    ext = os.path.splitext(photo)[1].lower()
    if ext not in MIME:
        die("unsupported photo type %r — use jpg, png, or webp" % ext)

    html_src = open(os.path.join(ROOT, tpl["file"])).read()
    html_src = html_src.replace("__FONTS__", open(FONTS).read())
    html_src = html_src.replace(
        "__IMG__",
        "data:%s;base64,%s" % (MIME[ext],
                               base64.b64encode(open(photo, "rb").read()).decode()),
    )
    for key in tpl["fields"] + PHOTO_FIELDS:
        html_src = html_src.replace("{{%s}}" % key.upper(), str(c[key]))

    if "{{" in html_src:
        die("unsubstituted placeholder left in %s — template/builder drift?"
            % tpl["file"])

    os.makedirs(OUT_DIR, exist_ok=True)
    html_path = os.path.join(OUT_DIR, c["slug"] + ".html")
    png_path = os.path.join(OUT_DIR, c["slug"] + ".png")
    with open(html_path, "w") as fh:
        fh.write(html_src)
    if os.path.exists(png_path):
        os.remove(png_path)

    if not os.path.exists(CHROME):
        die("Google Chrome not found at %s — needed to render the PNG" % CHROME)

    proc = subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         "--force-device-scale-factor=1",
         "--window-size=%d,%d" % (CARD_W, CARD_H),
         "--default-background-color=00000000",
         "--screenshot=" + png_path, "file://" + html_path],
        capture_output=True, text=True,
    )
    if not os.path.exists(png_path):
        die("Chrome failed to render %s\n%s" % (c["slug"], proc.stderr[-2000:]))

    # Confirm we got the exact pixel dimensions Instagram expects.
    dims = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", png_path],
                          capture_output=True, text=True).stdout
    w = h = None
    for line in dims.splitlines():
        if "pixelWidth:" in line:
            w = int(line.split(":")[1])
        if "pixelHeight:" in line:
            h = int(line.split(":")[1])
    if (w, h) != (CARD_W, CARD_H):
        die("rendered %sx%s, expected %sx%s" % (w, h, CARD_W, CARD_H))

    os.remove(html_path)  # intermediate; the PNG is the deliverable
    print("%s  (%dx%d)" % (os.path.relpath(png_path, ROOT), w, h))
    return png_path


CONTACT_TILE_W = 360           # a third of the card's 1080, so 3 fit across
CONTACT_GAP = 16
CONTACT_LABEL_H = 30


def contact_sheet(pngs):
    """Tile rendered cards into one reviewable image.

    Looking at a card is not optional — but looking at five of them one PNG at a
    time is five full-size images to pull in. At a third scale the crop, the
    scrim, and the type are all still judgeable, and it is one image instead of
    five. Rendered through the same headless Chrome the cards use, so this adds
    no dependency.
    """
    if not pngs:
        return None
    cols = min(3, len(pngs))
    rows = (len(pngs) + cols - 1) // cols
    tile_h = int(CONTACT_TILE_W * CARD_H / CARD_W)
    width = cols * CONTACT_TILE_W + (cols + 1) * CONTACT_GAP
    height = rows * (tile_h + CONTACT_LABEL_H) + (rows + 1) * CONTACT_GAP

    tiles = []
    for p in pngs:
        uri = "data:image/png;base64," + base64.b64encode(open(p, "rb").read()).decode()
        tiles.append(
            '<figure><img src="%s" width="%d" height="%d"><figcaption>%s</figcaption></figure>'
            % (uri, CONTACT_TILE_W, tile_h, os.path.basename(p)[:-4]))

    html = """<!doctype html><meta charset="utf-8"><style>
      html,body{margin:0;background:#0a0a0a}
      body{display:grid;grid-template-columns:repeat(%d,%dpx);gap:%dpx;padding:%dpx}
      figure{margin:0}
      img{display:block;border-radius:6px}
      figcaption{font:11px ui-monospace,Menlo,monospace;color:#8a8a8a;
                 padding-top:7px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    </style>%s""" % (cols, CONTACT_TILE_W, CONTACT_GAP, CONTACT_GAP, "".join(tiles))

    html_path = os.path.join(OUT_DIR, "_contact.html")
    png_path = os.path.join(OUT_DIR, "_contact.png")
    with open(html_path, "w") as fh:
        fh.write(html)
    if os.path.exists(png_path):
        os.remove(png_path)

    subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         "--force-device-scale-factor=1", "--window-size=%d,%d" % (width, height),
         "--default-background-color=ff0a0a0a",
         "--screenshot=" + png_path, "file://" + html_path],
        capture_output=True, text=True)
    os.remove(html_path)
    if not os.path.exists(png_path):
        die("Chrome failed to render the contact sheet")
    print("%s  (%d card%s)"
          % (os.path.relpath(png_path, ROOT), len(pngs), "" if len(pngs) == 1 else "s"))
    return png_path


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    flags = sys.argv[1:]
    reveal = "--open" in flags
    if not args:
        sys.exit(__doc__.strip())

    made = [build(a) for a in args]
    if "--contact-sheet" in flags:
        sheet = contact_sheet(made)
        if reveal and sheet:
            subprocess.run(["open", "-R", sheet])
            return
    if reveal and made:
        subprocess.run(["open", "-R", made[0]])


if __name__ == "__main__":
    main()
