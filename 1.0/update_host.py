"""Tiny HTTP host serving the update payload for the Raspberry Pi.

The Pi's boot script polls http://<this-pc>:8000/VERSION at startup and
downloads 1.0/update_out/quizfabric-update.tar.gz when the version differs.

Run this on the Windows PC (or use send_update.bat which builds the
payload and starts this host). First run: allow python.exe through the
Windows Firewall when prompted, otherwise the Pi cannot reach port 8000.
"""
import http.server
import os
import socketserver

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "update_out")
PORT = 8000


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def log_message(self, *args):
        pass  # keep the console quiet


if not os.path.isdir(ROOT):
    print("No update_out folder yet - run build_update.py first.")
    raise SystemExit(1)

with socketserver.TCPServer(("0.0.0.0", PORT), Handler) as httpd:
    print(f"QuizFabric update host listening on port {PORT} (Ctrl+C to stop)")
    httpd.serve_forever()
