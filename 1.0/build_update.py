"""Build the update payload (tar.gz + VERSION) that the Raspberry Pi pulls.

Run from the Windows PC whenever you want to send an update to the Pi.
The payload lives in 1.0/update_out/ and is served by update_host.py.
"""
import os
import tarfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))  # the 1.0 folder
OUT = os.path.join(ROOT, "update_out")

# Never send these to the Pi
EXCLUDES = {'.venv', 'logs', 'certbot_webroot', 'certbot_config', 'certbot_work',
            'certbot_logs', 'update_out', 'quiz-banners', '__pycache__', '.git'}


def _keep(ti):
    parts = ti.name.split(os.sep)
    if any(part in EXCLUDES for part in parts):
        return None
    return ti


def main():
    os.makedirs(OUT, exist_ok=True)
    version = time.strftime('%Y%m%d%H%M%S')
    with open(os.path.join(OUT, "VERSION"), "w", encoding="utf-8") as f:
        f.write(version)

    tarpath = os.path.join(OUT, "quizfabric-update.tar.gz")
    with tarfile.open(tarpath, "w:gz") as tar:
        tar.add(ROOT, arcname="1.0", filter=_keep)

    size_mb = os.path.getsize(tarpath) / (1024 * 1024)
    print(f"[OK] Update payload ready: version {version} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
