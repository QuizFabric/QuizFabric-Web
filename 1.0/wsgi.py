"""WSGI entry point for production deployment"""
# IMPORTANT: Must monkey-patch BEFORE importing Flask
import gevent.monkey
gevent.monkey.patch_all()

import os
import datetime
from dotenv import load_dotenv
import multiprocessing
import queue

load_dotenv('.env.production')

from app import app, socketio, logger
import app as app_module

# Let's Encrypt (certbot) configuration
HTTPS_DOMAIN = "play.lucasgeitgey.com"
CERT_DIR = os.path.join(os.path.expanduser("~"), "quizfabric-certs", "live", HTTPS_DOMAIN)
ACME_WEBROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "certbot_webroot")

# The whole app (Flask + Socket.IO websockets) as one WSGI callable. Both
# servers below share this SAME object, so HTTPS players on 443 and the
# plain-HTTP 3DS consoles on 80 play the same games in one process.
from flask_socketio import _SocketIOMiddleware
sio_app = _SocketIOMiddleware(socketio.server, app)

def _dispatch_plain(env, start_response):
    """Port 80 entry: serve the ACME challenge for certbot renewals, pass
    the 3DS client straight through as plain HTTP (it has no TLS support
    and cannot follow redirects), and send every browser to HTTPS."""
    path = env.get('PATH_INFO', '')
    if path.startswith('/.well-known/acme-challenge/'):
        token = os.path.basename(path)
        fpath = os.path.join(ACME_WEBROOT, ".well-known", "acme-challenge", token)
        if os.path.isfile(fpath):
            with open(fpath, 'rb') as f:
                body = f.read()
            start_response('200 OK', [('Content-Type', 'application/octet-stream'),
                                      ('Content-Length', str(len(body)))])
            return [body]
    ua = env.get('HTTP_USER_AGENT', '')
    remote = env.get('REMOTE_ADDR', '')
    if ua.startswith('QuizFabric3DS') or remote in ('127.0.0.1', '::1'):
        # Plain HTTP + plain WebSocket, straight into the shared app
        # (the 3DS client has no TLS support; localhost stays plain too).
        return sio_app(env, start_response)
    start_response('302 Found', [('Location', f'https://{HTTPS_DOMAIN}{path}')])
    return [b'']

if __name__ == "__main__":
    multiprocessing.freeze_support()
    # Set up shared data for GUI - but since GUI is separate, use regular dicts
    app_module.active_clients = {}
    app_module.ip_to_sid = {}
    app_module.actions_queue = queue.Queue()

    keyfile = os.path.join(CERT_DIR, "privkey.pem")
    certfile = os.path.join(CERT_DIR, "fullchain.pem")

    if os.path.isfile(keyfile) and os.path.isfile(certfile):
        # Two servers, one process: HTTPS on 443 for browsers, and a plain
        # HTTP server on 80 that serves ACME challenges, hands the 3DS
        # client straight to the app, and redirects everyone else.
        from gevent import pywsgi
        from geventwebsocket.handler import WebSocketHandler
        import logging as _logging
        # Internet scanners hit the public 443 with garbage TLS constantly;
        # keep gevent's per-connection tracebacks out of the console.
        _gevent_log = _logging.getLogger('quizfabric-gevent')
        _gevent_log.setLevel(_logging.CRITICAL)

        tls_server = pywsgi.WSGIServer(('0.0.0.0', 443), sio_app,
                                       keyfile=keyfile, certfile=certfile,
                                       handler_class=WebSocketHandler, log=_gevent_log)
        plain_server = pywsgi.WSGIServer(('0.0.0.0', 5710), _dispatch_plain,
                                         handler_class=WebSocketHandler, log=_gevent_log)
        plain_server.start()
        logger.info("Port 5710: ACME challenge + 3DS pass-through, browsers redirected to HTTPS")
        logger.info(f"Starting QuizFabric production server on port 443 (https://{HTTPS_DOMAIN})...")
        try:
            tls_server.serve_forever()
        finally:
            plain_server.stop()
    else:
        # No certificate yet: fall back to plain HTTP on port 80 so the
        # server stays up (and can serve the ACME challenge) until certbot
        # has issued the first certificate.
        logger.info("No SSL certificate found - serving plain HTTP on port 5710")
        socketio.run(app, debug=False, host="0.0.0.0", port=5710)
else:
    # For gunicorn WSGI deployment
    logger.info("QuizFabric WSGI application loaded for gunicorn deployment")
