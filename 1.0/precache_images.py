"""Pre-convert every question image to the 3DS .565 format.

Run on the server (Windows or Pi) after deploying or adding images so the
3DS "cache all quizzes" flow only reads ready-made files instead of
waiting for slow on-demand conversions.
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from non_static.imgconv import convert_on_demand, converted_path

UPLOAD_FOLDER = os.path.join(ROOT, "UploadedImages")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")

count = 0
errors = 0
for root, _dirs, files in os.walk(UPLOAD_FOLDER):
    for f in sorted(files):
        if f.lower().startswith("banner"):
            continue
        if f.lower().endswith(IMAGE_EXTS):
            rel = os.path.relpath(os.path.join(root, f), UPLOAD_FOLDER)
            print(f"[.] {rel}", flush=True)
            try:
                # The 3DS derives the .565 name from the image path itself
                # (converted_path strips the extension), so convert to that
                # exact name - not image.jpg.565.
                convert_on_demand(converted_path(os.path.join(root, f)))
                count += 1
            except Exception as e:
                errors += 1
                print(f"[!] {rel}: {e}", flush=True)
        elif f.endswith(".565"):
            count += 1  # already converted

print(f"[OK] pre-cache done: {count} images, {errors} errors", flush=True)
