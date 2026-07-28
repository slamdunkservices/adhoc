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
import html
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

# Shared by every template: photo framing / grading, tuned per photo.
PHOTO_DEFAULTS = {
    "photo_pos": "50% 12%",
    "photo_size": "cover",
    "photo_filter": "none",
}
PHOTO_FIELDS = ("photo_pos", "photo_size", "photo_filter")

# One entry per template. `required` is validated against the raw config;
# `defaults` fills the rest; `fields` is what gets substituted into the HTML.
TEMPLATES = {
    # Single book — one price, one stake.
    "base": {
        "file": "card_base.html",
        "required": ("slug", "photo", "name", "team", "jersey",
                     "proj", "book", "odds", "stake"),
        "defaults": {
            "league": "MLB",
            "tag_sub": "HOME RUN PROP",
            "kicker": "TODAY'S PLAY",
            "pick_label": "THE PICK — TO GO YARD",
            "chip": "1+",
            "pick_text": "HOME RUN",
            "pick_sub": "Anytime home run · book line beats our model = value",
            "stake_sub": "units",
        },
        "fields": ("league", "tag_sub", "kicker", "name", "team", "jersey",
                   "pick_label", "chip", "pick_text", "pick_sub", "proj",
                   "book", "odds", "stake", "stake_sub"),
    },
    # Same price shopped at 2–3 books, each with its own stake.
    "multibook": {
        "file": "card_multibook.html",
        "required": ("slug", "photo", "name", "team", "jersey", "proj", "books"),
        "defaults": {
            "league": "WNBA",
            "tag_sub": "FIRST BASKET PROP",
            "kicker": "TODAY'S PLAY",
            "pick_label": "THE PICK — TO SCORE FIRST",
            "chip": "1ST",
            "pick_text": "FIRST BASKET",
            "proj_note": "fair odds · every book below is priced <em>longer</em>",
            "total_line": None,  # computed from the stakes when omitted
        },
        "fields": ("league", "tag_sub", "kicker", "name", "team", "jersey",
                   "pick_label", "chip", "pick_text", "proj", "proj_note",
                   "book_tiles", "total_line"),
    },
}

# Photos are JPEG unless the extension says otherwise.
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp"}


def die(msg):
    sys.exit("error: " + msg)


def render_books(books, cfg_path):
    """Build the book-tile markup and the total-stake line for a multibook card."""
    if not isinstance(books, list) or not 2 <= len(books) <= 3:
        die("%s: `books` must be a list of 2 or 3 entries" % cfg_path)

    best = [b for b in books if b.get("best")]
    if len(best) != 1:
        die("%s: exactly one book must be marked \"best\": true (found %d)"
            % (cfg_path, len(best)))

    tiles, total = [], 0.0
    for b in books:
        missing = [k for k in ("book", "odds", "stake") if not b.get(k)]
        if missing:
            die("%s: book entry %r is missing %s"
                % (cfg_path, b.get("book", "?"), ", ".join(missing)))

        units = re.match(r"\s*([0-9]*\.?[0-9]+)", str(b["stake"]))
        if not units:
            die("%s: stake %r is not a number of units (e.g. \"1.3u\")"
                % (cfg_path, b["stake"]))
        total += float(units.group(1))

        esc = {k: html.escape(str(b[k])) for k in ("book", "odds", "stake")}
        tiles.append(
            '<div class="tile%s">\n'
            '        %s\n'
            '        <div class="t-label">%s</div>\n'
            '        <div class="t-val">%s</div>\n'
            '        <div class="t-stake">%s</div>\n'
            '      </div>' % (
                " play" if b.get("best") else "",
                '<div class="ribbon">▲ BEST PRICE</div>' if b.get("best") else "",
                esc["book"], esc["odds"], esc["stake"],
            )
        )

    total_line = ("Total exposure <b>%su</b> across %d books · take the best "
                  "price you have" % (("%.2f" % total).rstrip("0").rstrip("."),
                                      len(books)))
    return "\n      ".join(tiles), total_line


def build(cfg_path):
    with open(cfg_path) as fh:
        cfg = json.load(fh)

    kind = cfg.get("template", "base")
    if kind not in TEMPLATES:
        die("%s: unknown template %r — pick one of %s"
            % (cfg_path, kind, ", ".join(sorted(TEMPLATES))))
    tpl = TEMPLATES[kind]

    missing = [k for k in tpl["required"] if not cfg.get(k)]
    if missing:
        die("%s is missing required field(s): %s" % (cfg_path, ", ".join(missing)))

    c = dict(PHOTO_DEFAULTS)
    c.update(tpl["defaults"])
    c.update(cfg)

    if kind == "multibook":
        tiles, total_line = render_books(cfg["books"], cfg_path)
        c["book_tiles"] = tiles
        c["total_line"] = c.get("total_line") or total_line

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
