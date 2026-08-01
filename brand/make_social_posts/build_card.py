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
    # The available price is the hero; the photo is atmosphere behind it.
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

    c = dict(PHOTO_DEFAULTS)
    c.update(COMMON_DEFAULTS)
    c.update(tpl["defaults"])
    c.update(cfg)

    if c["accent"] not in ACCENTS:
        die("%s: unknown accent %r — pick one of %s"
            % (cfg_path, c["accent"], ", ".join(ACCENTS)))

    c["chip_html"] = wrap("chip", c["chip"])
    c["note_html"] = wrap("note", c["note"], "div")

    photo = os.path.join(ROOT, c["photo"])
    if not os.path.exists(photo):
        die("photo not found: %s (referenced by %s)" % (photo, cfg_path))
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


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    reveal = "--open" in sys.argv[1:]
    if not args:
        sys.exit(__doc__.strip())

    made = [build(a) for a in args]
    if reveal and made:
        subprocess.run(["open", "-R", made[0]])


if __name__ == "__main__":
    main()
