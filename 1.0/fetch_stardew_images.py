# One-off helper: downloads one wiki image per question of the Stardew Valley
# quiz into UploadedImages/Stardew Valley Quiz/qNN.png and writes the
# <quiz>_images.json metadata the server reads when hosting.
#
# Exact filenames are resolved through the MediaWiki API (the wiki has no
# predictable names: underscores, no spaces, variants), then fetched with curl
# from the API-provided direct URL.
#
# Run with the server's venv python:
#   .venv\Scripts\python fetch_stardew_images.py

import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

API = "https://stardewvalleywiki.com/mediawiki/api.php"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "UploadedImages", "Stardew Valley Quiz")
META = os.path.join(HERE, "non_static", "question", "Stardew Valley Quiz_images.json")

# question index -> search prefixes to try, in order
IMAGES = {
    0:  ["Daffodil"],
    1:  ["Luau"],
    2:  ["Star Token", "Fair"],
    3:  ["Spirit's Eve", "Spirit"],
    4:  ["Egg Festival", "Egg"],
    5:  ["Feast of the Winter Star", "Feast"],
    6:  ["Night Market", "Submarine"],
    7:  ["Festival of Ice", "Ice"],
    8:  ["Parsnip Seeds", "Parsnip"],
    9:  ["Corn"],
    10: ["Sweet Gem", "Rare Seed"],
    11: ["Scarecrow"],
    12: ["Common Mushroom", "Mushroom"],
    13: ["Greenhouse"],
    14: ["Cranberr"],
    15: ["The Mines", "Mines"],
    16: ["Stardrop"],
    17: ["Skull Key"],
    18: ["Rusty Key"],
    19: ["Prismatic"],
    20: ["Rusty Sword"],
    21: ["Dwarvish"],
    22: ["Cinder"],
    23: ["Legend"],
    24: ["Mutant Carp"],
    25: ["Iridium Rod"],
    26: ["Trap Bobber", "Bobber"],
    27: ["Pig"],
    28: ["Blue Chicken"],
    29: ["Stable"],
    30: ["Rabbit"],
    31: ["Mermaid"],
    32: ["Robin"],
    33: ["Lewis"],
    34: ["Gus"],
    35: ["Wizard"],
    36: ["Salad"],
    37: ["Amethyst"],
    38: ["Beer"],
    39: ["Coffee"],
    40: ["Poppy"],
    41: ["Purple Mushroom"],
    42: ["Void Egg"],
    43: ["Parsnip"],
    44: ["Chocolate Cake"],
    45: ["Leek"],
    46: ["Crispy"],
    47: ["Peach"],
    48: ["Coconut"],
    49: ["Duck Feather"],
    50: ["Roasted"],
    51: ["Catfish"],
    52: ["Battery"],
    53: ["Cactus"],
    54: ["Krobus"],
    55: ["Statue Of Uncertainty"],
    56: ["Mr"],
    57: ["Qi"],
    58: ["Journey", "Junimo Kart"],
    59: ["Bus", "Calico"],
    60: ["Minecart"],
    61: ["Quarry", "The Quarry"],
    62: ["Pierre"],
    63: ["Backpack"],
    64: ["Statue Of Perfection"],
    65: ["Iridium Ore"],
    66: ["Wilderness"],
    67: ["Bouquet"],
    68: ["Old Mariner"],
    69: ["Energy"],
}

os.makedirs(OUT, exist_ok=True)


def curl(args):
    return subprocess.run(
        ["curl", "-sS", "--max-time", "30",
         "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"] + args,
        capture_output=True, text=True)


def api_first_image(prefix):
    qs = urllib.parse.urlencode({
        "action": "query", "list": "allimages", "aiprefix": prefix,
        "ailimit": "1", "format": "json", "formatversion": "2",
    })
    # The wiki intermittently 403s (rate limiting); retry politely.
    for attempt in range(4):
        r = curl([f"{API}?{qs}"])
        try:
            doc = json.loads(r.stdout)
            imgs = doc.get("query", {}).get("allimages", [])
            if imgs:
                return imgs[0]["url"], imgs[0]["name"]
            return None, None  # clean answer, just no matches
        except Exception:
            time.sleep(1.5)
    return None, None


def fetch(url, dst):
    for attempt in range(4):
        r = curl(["-L", url, "-o", dst])
        if r.returncode == 0 and os.path.exists(dst):
            with open(dst, "rb") as f:
                data = f.read()
            if data.startswith(b"\x89PNG") and len(data) >= 100:
                return True
        time.sleep(1.5)
    return False


meta = {}
failures = []
for idx in sorted(IMAGES):
    dst = os.path.join(OUT, "q%02d.png" % idx)
    for prefix in IMAGES[idx]:
        url, name = api_first_image(prefix)
        if url and fetch(url, dst):
            meta[str(idx)] = ["q%02d.png" % idx]
            print(f"q{idx:02d} <- {name} ({os.path.getsize(dst)} bytes)")
            break
        print(f"    '{prefix}': no good image")
    else:
        failures.append(idx)
        print(f"q{idx:02d} FAILED")

with open(META, "w", encoding="utf-8") as f:
    json.dump(meta, f, indent=1)

print(f"\n{len(meta)}/70 downloaded, meta written to {META}")
if failures:
    print("missing:", failures)
    sys.exit(1)
