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

if __name__ == "__main__":
    multiprocessing.freeze_support()
    # Set up shared data for GUI - but since GUI is separate, use regular dicts
    app_module.active_clients = {}
    app_module.ip_to_sid = {}
    app_module.actions_queue = queue.Queue()
    logger.info("Starting QuizFabric production server on port 80...")
    socketio.run(app, debug=False, host="0.0.0.0", port=80)
else:
    # For gunicorn WSGI deployment
    logger.info("QuizFabric WSGI application loaded for gunicorn deployment")
