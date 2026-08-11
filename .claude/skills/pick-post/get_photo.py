#!/usr/bin/env python3
"""
get_photo.py — resolve a player photo, land it in photos/, record where it came from.

    python3 get_photo.py --player "Riley Greene"                 # official CDN, automatic
    python3 get_photo.py --player "Chelsea Gray" --page <url>     # scrape a page you found
    python3 get_photo.py --player "Chelsea Gray" --url  <img url> # take this exact image
    python3 get_photo.py --player "Chelsea Gray" --page <url> --list   # look, don't download
    python3 get_photo.py --check                                  # are the resolvers alive?

Writes photos/<slug>.<ext> and an entry in photos/sources.json. Everything else
it touches is read-only.

## Why there is no built-in image search

Discovery — "find me a photo of this player" — is deliberately NOT in this
script, because it cannot be done reliably from a plain HTTP client. Tested,
all of them failing in ways that would put the WRONG PLAYER on a card:

  * Sinclair station search (news3lv, wjla)  — client-rendered; the server
    returns a shell with no mention of the query, and the media2 URLs sitting
    in that shell belong to unrelated articles
  * Bing image search   — served results for an entirely different query
  * DuckDuckGo html     — HTTP 202 bot challenge
  * ESPN search API     — empty payload; ESPN news API — HTTP 403
  * wnba.com player page — headshot and sponsor logos only, no action photos

A scraper that quietly returns someone else's face is worse than no scraper,
so discovery stays with the caller, who has a real search tool. The moment a
URL exists, this script takes over: transform, fetch, validate, size, name,
cache, and record. That is the mechanical 90%.

## What IS automatic

MLB action shots are a solved problem — img.mlbstatic.com serves one per
player id, keyed off 02_curated/players/current.csv, and 404s cleanly on a bad
id so the status code can be trusted. That covers every MLB card with no
searching at all. WNBA has no action equivalent; the official CDN carries only
transparent headshot cutouts, so WNBA action shots need --page or --url.

Fetching shells out to `curl`: the system python3 has no CA bundle and urllib
dies with CERTIFICATE_VERIFY_FAILED on both CDNs, and `no pip installs` is a
house rule. Sizing uses `sips`, which build_card.py already depends on.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from find_plays import data_root, fold  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
PHOTOS = os.path.join(REPO, "brand", "make_social_posts", "photos")
MANIFEST = os.path.join(PHOTOS, "sources.json")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

MLB_ACTION = ("https://img.mlbstatic.com/mlb-photos/image/upload/"
              "w_{w},q_auto:good,f_jpg/v1/people/{pid}/action/{crop}/current")
WNBA_HEADSHOT = "https://cdn.wnba.com/headshots/wnba/latest/1040x760/{pid}.png"

EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}

# A photo below this is an icon or a tracking pixel, not a card photo.
MIN_BYTES = 20_000
MIN_EDGE = 400

# Aspect -> the frames that crop it well. Straight out of README.md "Tuning the
# photo": poster wants a tall portrait, ticket throws a busy background away,
# base wants a landscape band.
SHAPES = (
    (0.00, 0.80, "tall",      ("poster", "split", "fullbleed")),
    (0.80, 1.20, "square",    ("ticket", "fullbleed")),
    (1.20, 9.99, "landscape", ("base", "ticket")),
)

# Sinclair's CMS (news3lv and its sibling stations) serves any aspect and size
# from one source image:
#   /resources/media2/{aspect}/{srcW}/{outW}/{ox}x{oy}/{quality}/{uuid}.jpg
# The copy embedded in an article is a thumbnail; asking the same path for
# 4x3 at 2400 returns the full-resolution frame. This turned a 544x306 crop
# into 2400x1800 while building a card, which is the whole reason it is here.
SINCLAIR = re.compile(
    r"^(?P<host>https?://[^/]+)/resources/media2/"
    r"(?P<aspect>\d+x\d+)/(?P<srcw>\d+)/(?P<outw>\d+)/"
    r"(?P<off>\d+x\d+)/(?P<q>\d+)/(?P<uuid>[^/?#]+)$")


def die(msg):
    sys.exit("error: " + msg)


def slugify(name):
    """'Javier Báez' -> 'javier-baez', matching the existing photos/ filenames."""
    s = re.sub(r"[^a-z0-9]+", "-", fold(name))
    return s.strip("-")


def curl(url, dest=None, head=False, timeout=30):
    """(status, content_type, bytes_or_path). All network access goes through here.

    The body always goes to a file, never to stdout. Sharing stdout between the
    payload and curl's -w summary means an HTML body's own newlines get parsed
    as the status line, which fails loudly on some pages and silently on others.
    """
    import tempfile
    tmp = None
    if not head and dest is None:
        fd, tmp = tempfile.mkstemp(prefix="getphoto-")
        os.close(fd)
    target = dest or tmp or os.devnull

    cmd = ["curl", "-sL", "-A", UA, "--max-time", str(timeout),
           "-w", "%{http_code}\t%{content_type}\t%{size_download}",
           "-o", target]
    if head:
        cmd.append("-I")
    cmd.append(url)
    p = subprocess.run(cmd, capture_output=True)

    parts = p.stdout.decode("utf-8", "replace").strip().split("\t")
    if len(parts) < 3:
        tmp and os.path.exists(tmp) and os.remove(tmp)
        return 0, "", b""
    status = int(parts[0] or 0)
    ctype = (parts[1] or "").split(";")[0].strip()

    if head:
        return status, ctype, b""
    if dest:
        return status, ctype, dest
    with open(tmp, "rb") as fh:
        body = fh.read()
    os.remove(tmp)
    return status, ctype, body


def dimensions(path):
    out = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", path],
                         capture_output=True, text=True).stdout
    w = h = None
    for line in out.splitlines():
        if "pixelWidth:" in line:
            w = int(line.split(":")[1])
        if "pixelHeight:" in line:
            h = int(line.split(":")[1])
    return w, h


def shape_of(w, h):
    aspect = w / h if h else 0
    for lo, hi, name, frames in SHAPES:
        if lo <= aspect < hi:
            return name, frames, round(aspect, 3)
    return "unknown", (), round(aspect, 3)


# ------------------------------------------------------------ resolvers

def upscale(url, aspect="4x3", width=2400):
    """Ask a Sinclair CMS URL for its full-size frame instead of the thumbnail."""
    m = SINCLAIR.match(url.split("?")[0])
    if not m:
        return None
    g = m.groupdict()
    return "%s/resources/media2/%s/%s/%d/%s/90/%s" % (
        g["host"], aspect, g["srcw"], width, g["off"], g["uuid"])


def mlb_candidates(name):
    import pandas as pd
    root = data_root("mlb")
    if not root:
        return []
    df = pd.read_csv(os.path.join(root, "02_curated", "players", "current.csv"))
    hit = df[df["full_name"].map(fold).str.contains(fold(name), regex=False)]
    if hit.empty:
        return []
    exact = hit[hit["full_name"].map(fold) == fold(name)]
    hit = exact if len(exact) == 1 else hit
    if len(hit) > 1:
        die("'%s' matches %d MLB players: %s — be more specific"
            % (name, len(hit), ", ".join(hit["full_name"])))
    p = hit.iloc[0]
    pid = int(p["player_id"])
    return [
        {"url": MLB_ACTION.format(w=1200, pid=pid, crop="vertical"),
         "resolver": "mlb-cdn-action-vertical", "player": p["full_name"], "note": "tall action shot"},
        {"url": MLB_ACTION.format(w=2208, pid=pid, crop="hero"),
         "resolver": "mlb-cdn-action-hero", "player": p["full_name"], "note": "wide banner crop"},
    ]


def wnba_candidates(name):
    import pandas as pd
    root = data_root("wnba")
    if not root:
        return []
    df = pd.read_csv(os.path.join(root, "02_curated", "rosters", "current.csv"))
    hit = df[df["PLAYER_NAME"].map(fold).str.contains(fold(name), regex=False)]
    if hit.empty:
        return []
    p = hit.iloc[0]
    return [{"url": WNBA_HEADSHOT.format(pid=int(p["PLAYER_ID"])),
             "resolver": "wnba-cdn-headshot", "player": p["PLAYER_NAME"],
             "note": "transparent headshot cutout — NOT an action shot; "
                     "use --page/--url for action"}]


class _Images(HTMLParser):
    """Pull every plausible image URL out of a page: og:image, src, srcset."""

    def __init__(self):
        super().__init__()
        self.found = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and a.get("property") in ("og:image", "twitter:image"):
            if a.get("content"):
                self.found.append((a["content"], 0))
        if tag in ("img", "source"):
            if a.get("src"):
                self.found.append((a["src"], 0))
            for part in (a.get("srcset") or "").split(","):
                bits = part.strip().split()
                if bits:
                    w = 0
                    if len(bits) > 1 and bits[1].endswith("w"):
                        try:
                            w = int(bits[1][:-1])
                        except ValueError:
                            w = 0
                    self.found.append((bits[0], w))


def page_candidates(page_url, name):
    """Scrape one page the caller found. Ranked, deduped, Sinclair-upscaled."""
    status, ctype, body = curl(page_url)
    if status != 200:
        die("--page returned HTTP %d for %s" % (status, page_url))
    parser = _Images()
    parser.feed(body.decode("utf-8", "replace"))

    base = re.match(r"^(https?://[^/]+)", page_url)
    seen, out = set(), []
    for raw, hint in parser.found:
        url = raw.strip()
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/") and base:
            url = base.group(1) + url
        if not url.startswith("http") or url.split("?")[0].endswith(".svg"):
            continue
        big = upscale(url)
        url = big or url
        if url in seen:
            continue
        seen.add(url)
        # A filename carrying the player's surname is a strong relevance signal
        # on wire photos, whose names spell the subject out.
        surname = fold(name).split("-")[-1].split()[-1] if name else ""
        score = hint + (5000 if surname and surname in fold(url) else 0) \
            + (2000 if big else 0)
        out.append({"url": url, "resolver": "page-scrape" + ("+sinclair" if big else ""),
                    "score": score, "note": "from %s" % page_url})
    out.sort(key=lambda c: -c["score"])
    return out[:12]


# ------------------------------------------------------------ manifest

def load_manifest():
    try:
        with open(MANIFEST) as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_manifest(m):
    with open(MANIFEST, "w") as fh:
        json.dump(dict(sorted(m.items())), fh, indent=2)
        fh.write("\n")


def record(slug, filename, url, resolver, w, h):
    m = load_manifest()
    m[slug] = {"file": filename, "url": url, "resolver": resolver,
               "width": w, "height": h}
    save_manifest(m)


# ------------------------------------------------------------ fetch

def fetch(cand, slug, force=False):
    """Download one candidate into photos/, or explain why it isn't usable."""
    os.makedirs(PHOTOS, exist_ok=True)
    tmp = os.path.join(PHOTOS, ".%s.tmp" % slug)
    status, ctype, _ = curl(cand["url"], dest=tmp)
    if status != 200:
        os.path.exists(tmp) and os.remove(tmp)
        return None, "HTTP %d" % status
    ext = EXT.get(ctype)
    if not ext:
        os.remove(tmp)
        return None, "not an image (content-type %r)" % ctype
    if os.path.getsize(tmp) < MIN_BYTES:
        size = os.path.getsize(tmp)
        os.remove(tmp)
        return None, "only %d bytes — icon or placeholder, not a card photo" % size
    w, h = dimensions(tmp)
    if not w or not h or min(w, h) < MIN_EDGE:
        os.remove(tmp)
        return None, "too small at %sx%s" % (w, h)

    dest = os.path.join(PHOTOS, slug + ext)
    if os.path.exists(dest) and not force:
        os.remove(tmp)
        return dest, "already present — pass --force to replace"
    for stale in (slug + e for e in EXT.values()):
        p = os.path.join(PHOTOS, stale)
        if os.path.exists(p) and p != dest:
            os.remove(p)
    os.replace(tmp, dest)
    record(slug, os.path.basename(dest), cand["url"], cand["resolver"], w, h)
    return dest, None


def describe(path):
    w, h = dimensions(path)
    shape, frames, aspect = shape_of(w, h)
    return ("  %s\n  %dx%d · aspect %.2f · %s source\n  suggested template: %s"
            % (os.path.relpath(path, REPO), w, h, aspect, shape,
               ", ".join(frames) or "n/a"))


# ------------------------------------------------------------ check

def check():
    """Ping every resolver so rot is found deliberately, not mid-post."""
    print("Resolver health\n")
    ok = True
    for label, url in (
        ("mlb-cdn-action-vertical", MLB_ACTION.format(w=1200, pid=592450, crop="vertical")),
        ("mlb-cdn-action-hero", MLB_ACTION.format(w=2208, pid=592450, crop="hero")),
        ("wnba-cdn-headshot", WNBA_HEADSHOT.format(pid=203833)),
    ):
        status, ctype, _ = curl(url, head=True, timeout=20)
        good = status == 200 and ctype.startswith("image/")
        ok &= good
        print("  %-26s %s  HTTP %d %s" % (label, "OK  " if good else "DEAD", status, ctype))

    bogus, _, _ = curl(MLB_ACTION.format(w=1200, pid=99999999, crop="vertical"),
                       head=True, timeout=20)
    print("  %-26s %s  HTTP %d (a bad id must 404, or every lookup silently "
          "'succeeds')" % ("mlb-cdn 404 behaviour", "OK  " if bogus == 404 else "DEAD", bogus))
    ok &= bogus == 404

    for lg in ("mlb", "wnba"):
        root = data_root(lg)
        good = bool(root and os.path.isdir(root))
        ok &= good
        print("  %-26s %s  %s" % ("%s data root" % lg, "OK  " if good else "DEAD",
                                  root or "no ~/.%s_jobs.env" % lg))
    print("\n  page-scrape / --url are caller-driven; there is nothing to ping.")
    print("\n%s" % ("all resolvers healthy" if ok else
                    "SOMETHING IS DEAD — fix before relying on the automatic path"))
    return 0 if ok else 1


# ------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--player", help="name; accents optional")
    ap.add_argument("--page", help="a page to scrape for images (you found it, this reads it)")
    ap.add_argument("--url", help="an exact image URL; Sinclair URLs are auto-upscaled")
    ap.add_argument("--slug", help="output stem (default: slugified player name)")
    ap.add_argument("--list", action="store_true", help="show candidates, download nothing")
    ap.add_argument("--check", action="store_true", help="ping the resolvers and exit")
    ap.add_argument("--force", action="store_true", help="replace an existing photo")
    args = ap.parse_args()

    if args.check:
        sys.exit(check())
    if not args.player:
        ap.error("--player is required (or use --check)")

    cands = []
    if args.url:
        big = upscale(args.url)
        cands.append({"url": big or args.url, "score": 9999,
                      "resolver": "manual-url" + ("+sinclair" if big else ""),
                      "note": "upscaled from the Sinclair CMS" if big else "as given"})
    if args.page:
        cands += page_candidates(args.page, args.player)
    if not cands:
        cands = mlb_candidates(args.player) + wnba_candidates(args.player)
    if not cands:
        die("no candidates for %r. MLB/WNBA rosters have no such player, and no "
            "--page or --url was given." % args.player)

    # Name the file after the player the roster resolved, not the string that
    # was typed: `--player Baez` must still land javier-baez.jpg, because the
    # card config and every later lookup key off the full-name slug.
    resolved = next((c["player"] for c in cands if c.get("player")), None)
    slug = args.slug or slugify(resolved or args.player)

    if args.list:
        print("Candidates for %s (slug: %s)\n" % (args.player, slug))
        for i, c in enumerate(cands, 1):
            print("%2d. [%s] %s" % (i, c["resolver"], c["url"]))
            if c.get("note"):
                print("    %s" % c["note"])
        print("\nDownload one with:  --url '<the url>'")
        return

    problems = []
    for c in cands:
        path, why = fetch(c, slug, force=args.force)
        if path:
            print("%s via %s" % ("kept" if why else "saved", c["resolver"]))
            if why:
                print("  %s" % why)
            print(describe(path))
            if c["resolver"] == "wnba-cdn-headshot":
                print("\n  NOTE: this is a headshot cutout, not an action shot. For "
                      "action,\n  find a page and re-run with --page <url>.")
            return
        problems.append("  [%s] %s — %s" % (c["resolver"], c["url"][:80], why))
    die("every candidate failed:\n" + "\n".join(problems))


if __name__ == "__main__":
    main()
