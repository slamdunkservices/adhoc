#!/bin/bash
# Regenerate fonts.css — Barlow Condensed, latin subset, base64-embedded.
#
# You only need this to add/change weights. fonts.css is committed and
# self-contained, so normal card rendering never touches the network.
#
# Usage: ./fetch_fonts.sh [weights]   (default: 500;600;700;800)
set -euo pipefail
cd "$(dirname "$0")"

WEIGHTS="${1:-500;600;700;800}"
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

curl -sf -A "$UA" \
  "https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@${WEIGHTS}&display=swap" \
  -o "$TMP/barlow.css"

WEIGHTS="$WEIGHTS" TMP="$TMP" python3 - <<'PY'
import base64, os, re, subprocess, sys

tmp = os.environ["TMP"]
css = open(os.path.join(tmp, "barlow.css")).read()

faces = []
for block in re.findall(r"@font-face\s*\{[^}]*\}", css):
    # Keep only the basic-latin subset; skip cyrillic/vietnamese/etc.
    if "U+0000-00FF" not in block:
        continue
    weight = re.search(r"font-weight:\s*(\d+)", block)
    url = re.search(r"url\((https://[^)]+\.woff2)\)", block)
    if not (weight and url):
        continue
    dest = os.path.join(tmp, "bc-%s.woff2" % weight.group(1))
    subprocess.run(["curl", "-sf", "-o", dest, url.group(1)], check=True)
    data = open(dest, "rb").read()
    if len(data) < 500:
        sys.exit("suspiciously small woff2 for weight " + weight.group(1))
    faces.append(
        "@font-face{font-family:'Barlow Condensed';font-style:normal;"
        "font-weight:%s;font-display:block;"
        "src:url(data:font/woff2;base64,%s) format('woff2');}"
        % (weight.group(1), base64.b64encode(data).decode())
    )
    print("embedded weight %s (%d bytes)" % (weight.group(1), len(data)))

if not faces:
    sys.exit("no latin @font-face blocks found — did the Google Fonts API change?")

open("fonts.css", "w").write("\n".join(faces))
print("wrote fonts.css with %d faces" % len(faces))
PY
