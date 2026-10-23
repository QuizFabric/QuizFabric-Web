# One-off helper: converts the quiz PNGs to the 3DS format (tiled RGBA8) the
# client can upload with no decoder. The conversion logic lives in
# non_static/imgconv.py (the server also uses it for on-demand conversion of
# newly uploaded images).
#
# Run with the server's venv python:
#   .venv\Scripts\python convert_stardew_images.py

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from non_static.imgconv import png_to_rgb8

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "UploadedImages", "Stardew Valley Quiz")

count = 0
for f in sorted(os.listdir(OUT)):
    if not f.endswith(".png"):
        continue
    src = os.path.join(OUT, f)
    dst = os.path.join(OUT, f[:-4] + ".565")
    if png_to_rgb8(src, dst):
        count += 1
        print(f"{f} -> {os.path.basename(dst)} ({os.path.getsize(dst)} bytes)")
    else:
        print(f"{f} FAILED to convert")

print(f"\nconverted {count} images")
