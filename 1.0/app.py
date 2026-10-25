import os
import json
import random
import datetime
import html
import re
import sys
import threading
import logging
import time
import queue
import multiprocessing
import io
import shutil
import urllib.parse
import urllib.request
import secrets

from flask import Flask, abort, render_template, jsonify, request, session as flask_session, redirect, url_for, send_file
from werkzeug.security import check_password_hash

from flask_socketio import SocketIO, emit, join_room, leave_room

try:
    import tkinter as tk
    from tkinter import messagebox, simpledialog
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False

from non_static.quiz import read_quiz, get_random_question, validate_answer, correct_answer_text, question_payload  # adjust imports as needed

from non_static.utils import read_uploaded_json
from non_static.accounts import (
    AccountValidationError,
    RegistrationRateLimitError,
    add_owned_quiz,
    get_account,
    get_owned_quizzes,
    register_account,
    remove_owned_quiz,
    rename_owned_quiz,
)

try:
    from colorama import init, Fore, Back, Style
    init()
    COLORAMA_AVAILABLE = True
except ImportError:
    COLORAMA_AVAILABLE = False

app = Flask(__name__, template_folder="templates")


def _load_session_secret():
    configured_key = os.environ.get("FLASK_SECRET_KEY")
    if configured_key:
        return configured_key

    key_path = os.path.join(os.path.dirname(__file__), "data", "session_secret.key")
    os.makedirs(os.path.dirname(key_path), exist_ok=True)
    try:
        with open(key_path, "x", encoding="utf-8") as key_file:
            key = secrets.token_hex(32)
            key_file.write(key)
            key_file.flush()
            os.chmod(key_path, 0o600)
            return key
    except FileExistsError:
        while not os.path.getsize(key_path):
            time.sleep(0.05)
        with open(key_path, "r", encoding="utf-8") as key_file:
            return key_file.read().strip()


app.secret_key = _load_session_secret()
app.config.update(
    SESSION_COOKIE_NAME="quizfabric_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=30),
)
ACCOUNT_STORE = os.path.join(os.path.dirname(__file__), "data", "logins.json")

socketio = SocketIO(app, async_mode='gevent')

# Globals for IP monitoring. Initialised here so every entry point works
# the same: dev (python app.py), production (wsgi.py) and gunicorn workers.
active_clients = {}
ip_to_sid = {}
actions_queue = queue.Queue()

# Globals for IP monitoring

from werkzeug.utils import secure_filename

# Configuration for file uploads
ALLOWED_EXTENSIONS = {'.png', '.webp', '.jpg', '.jpeg', '.gif'}
MAX_IMAGES_PER_QUIZ = 100
from werkzeug.utils import secure_filename

# Configuration for file uploads
ALLOWED_EXTENSIONS = {'.png', '.webp', '.jpg', '.jpeg', '.gif'}
MAX_FILE_SIZE = 300 * 1024 * 1024  # 300MB
MAX_IMAGES_PER_QUIZ = 100
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "UploadedImages")

# Maps HTTP Content-Type values to file extensions for URL-based image uploads
CONTENT_TYPE_EXTENSIONS = {
    'image/png': '.png',
    'image/jpeg': '.jpg',
    'image/webp': '.webp',
    'image/gif': '.gif',
    'image/apng': '.png',  # animated PNG: saved as .png, converted as APNG
}

# Create uploads folder if it doesn't exist
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
# Ensure logs directory exists and set up log filename
logs_dir = os.path.join(os.path.dirname(__file__), "logs")
if not os.path.exists(logs_dir):
    os.makedirs(logs_dir)

# Quiz plays tracking
plays_file = os.path.join(os.path.dirname(__file__), "data", "quiz-plays.json")

def load_plays():
    if os.path.exists(plays_file):
        with open(plays_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_plays(plays):
    with open(plays_file, 'w', encoding='utf-8') as f:
        json.dump(plays, f, indent=4)

# Minecraft-style colored logging setup
class MinecraftFormatter(logging.Formatter):
    """Custom formatter with Minecraft-inspired colors"""

    COLORS = {
        'DEBUG': Fore.CYAN + Style.DIM,      # §b (aqua) for debug
        'INFO': Fore.GREEN,                  # §a (green) for info
        'WARNING': Fore.YELLOW,              # §e (yellow) for warnings
        'ERROR': Fore.RED,                   # §c (red) for errors
        'CRITICAL': Fore.RED + Back.WHITE    # §c with white background for critical
    }

    def format(self, record):
        if COLORAMA_AVAILABLE:
            # Add color to level name
            level_color = self.COLORS.get(record.levelname, Fore.WHITE)
            colored_levelname = f"{level_color}{record.levelname}{Style.RESET_ALL}"

            # Add timestamp in gold color (like Minecraft chat)
            timestamp = datetime.datetime.fromtimestamp(record.created).strftime('%H:%M:%S')
            colored_timestamp = f"{Fore.YELLOW}{timestamp}{Style.RESET_ALL}"

            # Get the message (ANSI codes should work directly with colorama)
            message = record.getMessage()

            # Manual formatting to preserve colors
            return f"{colored_timestamp} [{colored_levelname}] {message}"
        else:
            return super().format(record)

# Set up logger
logger = logging.getLogger('QuizFabric')
logger.setLevel(logging.INFO)

# Create console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# Create file handler
log_filename = os.path.join(logs_dir, f"log_{datetime.datetime.now().strftime('%Y%m%d')}.txt")
file_handler = logging.FileHandler(log_filename, encoding='utf-8')
file_handler.setLevel(logging.INFO)

# Create formatter
formatter = MinecraftFormatter('%(timestamp)s [%(levelname)s] %(message)s')
console_handler.setFormatter(formatter)

# Plain formatter for file (no colors)
file_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
file_handler.setFormatter(file_formatter)

# Add handlers to logger
logger.addHandler(console_handler)
logger.addHandler(file_handler)

# Log server startup
logger.info("QuizFabric server starting up...")

def get_client_ip():
    return request.remote_addr or "unknown"

def is_mobile():
    user_agent = request.headers.get('User-Agent', '').lower()
    mobile_keywords = ['mobile', 'android', 'iphone', 'ipad', 'ipod', 'blackberry', 'windows phone']
    return any(keyword in user_agent for keyword in mobile_keywords)

@app.before_request
def before_request():
    log_connection()
    track_page()


@app.before_request
def require_account():
    public_endpoints = {
        "login", "register", "static", "robots", "get_avatars",
        "get_avatar_image", "get_icon", "acme_challenge", "join_game",
        "api_searchkahoot", "get_quiz_data", "list_quizzes",
        "api_quizbanner", "api_kahootcover",
    }
    admin_endpoints = {
        "ban_ip", "unban_ip", "log_command", "kick", "gui_data",
        "gui_action", "view_logs", "logs_content", "view_times",
        "view_times_file",
    }
    if (request.endpoint in public_endpoints
            or request.path.startswith("/.well-known/acme-challenge/")):
        return None
    username = flask_session.get("username")
    account = get_account(ACCOUNT_STORE, username) if username else None
    if account:
        if request.endpoint in admin_endpoints and account.get("role") != "admin":
            return jsonify({"error": "Administrator access required"}), 403
        return None
    flask_session.clear()
    if request.path.startswith("/api/") or request.is_json:
        return jsonify({"error": "Authentication required"}), 401
    return redirect(url_for("login", next=request.full_path.rstrip("?")))


def _safe_next_url(target):
    if (target and target.startswith("/") and not target.startswith("//")
            and "\\" not in target and "\r" not in target and "\n" not in target):
        return target
    return url_for("home")


@app.route("/register", methods=["GET", "POST"])
def register():
    message = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        password_confirmation = request.form.get("password_confirmation", "")
        legacy_quizzes = read_uploaded_json().get(get_client_ip(), [])
        admin_username = os.environ.get("QUIZFABRIC_ADMIN_USERNAME", "").strip().lower()
        admin_password = os.environ.get("QUIZFABRIC_ADMIN_PASSWORD")
        try:
            account_name = register_account(
                ACCOUNT_STORE, get_client_ip(), username, password,
                legacy_quizzes=[os.path.splitext(q)[0] for q in legacy_quizzes],
                admin_username=admin_username, admin_password=admin_password,
                password_confirmation=password_confirmation)
        except RegistrationRateLimitError as error:
            message = str(error)
            status_code = 429
        except AccountValidationError as error:
            message = str(error)
            status_code = 400
        else:
            flask_session.clear()
            flask_session["username"] = account_name
            flask_session.permanent = True
            logger.info(f"Registered account '{account_name}'")
            return redirect(_safe_next_url(
                request.args.get("next") or request.form.get("next")))

    return render_template(
        "account.html", mode="register", message=message,
            next_url=request.args.get("next", "")), (status_code if message else 200)


@app.route("/login", methods=["GET", "POST"])
def login():
    message = None
    next_url = request.args.get("next") or request.form.get("next", "")
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        account = get_account(ACCOUNT_STORE, username)
        if (account and len(password) <= 128
                and check_password_hash(account.get("password_hash", ""), password)):
            flask_session.clear()
            flask_session["username"] = username
            flask_session.permanent = True
            logger.info(f"Account '{username}' logged in")
            return redirect(_safe_next_url(next_url))
        message = "Incorrect username or password."

    return render_template(
        "account.html", mode="login", message=message, next_url=next_url)


@app.route("/logout", methods=["POST"])
def logout():
    flask_session.clear()
    return redirect(url_for("login"))


def _canonical_quiz_name(name):
    return name[:-4] if name.endswith(".txt") else name


def _safe_quiz_name(name):
    return bool(name and name not in {".", ".."}
                and "/" not in name and "\\" not in name and "\0" not in name)


def _owns_quiz(name):
    if not _safe_quiz_name(name):
        return False
    return _canonical_quiz_name(name) in get_owned_quizzes(
        ACCOUNT_STORE, flask_session["username"])


def _socket_has_account():
    username = flask_session.get("username")
    return bool(username and get_account(ACCOUNT_STORE, username))


def log_connection():
    ip = get_client_ip()
    # Log every request to the console (the Pi's HDMI screen via tty1),
    # except home-page hits (they poll constantly), cover fetches and
    # uploaded images (the CVR/IMG lines in those routes are the nicer
    # replacements).
    if (request.path == '/' or request.path == '/api/kahootcover'
            or request.path.startswith('/UploadedImages/')):
        return
    query = ('?' + request.query_string.decode('utf-8', 'replace')) if request.query_string else ''
    logger.info(f"REQ {ip} {request.method} {request.path}{query}")

def track_page():
    if active_clients is None:
        return
    # Exclude static assets, favicon, robots, and uploaded images
    if (request.path.startswith('/static/') or 
        request.path in ['/favicon.ico', '/robots.txt'] or 
        request.path.startswith('/data/') or 
        request.path.startswith('/UploadedImages/')):
        return
    ip = get_client_ip()
    active_clients[ip] = {
        'page': request.path,
        'last_seen': time.time(),
        'sid': ip_to_sid.get(ip) if ip_to_sid else None
    }


@app.after_request
def log_disconnection(response):
    # Removed verbose connection logging
    return response

# Ensure the data directory exists.
data_dir = os.path.join(os.path.dirname(__file__), "data")
if not os.path.exists(data_dir):
    os.makedirs(data_dir)
banned_file = os.path.join(data_dir, "banned.txt")

def load_banned_ips():
    banned = {}
    if os.path.exists(banned_file):
        with open(banned_file, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split(' ', 1)
                if len(parts) == 2:
                    banned_ip, reason = parts
                    banned[banned_ip.strip()] = reason.strip()
    return banned

def save_banned_ips(banned):
    with open(banned_file, "w", encoding="utf-8") as f:
        for ip, reason in banned.items():
            f.write(f"{ip.strip()} {reason}\n")

# Global dictionary to track banned IPs.
banned_ips = load_banned_ips()

# Global dictionary for Kahoot-style sessions: code -> {'host_sid': sid, 'players': [sids], 'quiz_name': name, 'questions': [], 'current_q': 0}
sessions = {}
control_to_game = {}



@app.before_request
def check_banned_ip():
    ip = get_client_ip()
    # Allow log-related endpoints even if banned.
    allowed_paths = ['/logs', '/logs-content', '/unban-ip', '/log-command']
    if request.path in allowed_paths:
        return
    if ip in banned_ips:
        reason = banned_ips[ip]
        logger.warning(f"Blocked banned IP {ip}: {reason}")
        return render_template("error.html", message=f"IP {ip} banned: {reason}"), 403


@app.route('/ban-ip', methods=['GET', 'POST'])
def ban_ip():
    ip = get_client_ip()
    if request.method == "GET":
        protocol = request.environ.get("SERVER_PROTOCOL", "HTTP/1.1")
        message = f"GET {request.path} {protocol} Not Allowed: Use POST"
        return render_template("error.html", message=message), 405
    data = request.get_json()
    ip_to_ban = data.get('ip', '').strip()
    reason = data.get('reason', 'No reason provided.')
    if ip_to_ban:
        banned_ips[ip_to_ban] = reason
        save_banned_ips(banned_ips)
        logger.warning(f"Admin {ip} banned IP {ip_to_ban}: {reason}")
        return render_template("error.html", message=f"IP {ip_to_ban} banned: {reason}")
    return jsonify({'error': 'No IP provided.'}), 400

@app.route('/unban-ip', methods=['GET', 'POST'])
def unban_ip():
    ip = get_client_ip()
    if request.method == "GET":
        protocol = request.environ.get("SERVER_PROTOCOL", "HTTP/1.1")
        message = f"GET {request.path} {protocol} Not Allowed: Use POST"
        return render_template("error.html", message=message), 405
    data = request.get_json() or {}
    ip_to_unban = data.get('ip', '').strip()
    if ip_to_unban in banned_ips:
        del banned_ips[ip_to_unban]
        save_banned_ips(banned_ips)
        logger.info(f"Admin {ip} unbanned IP {ip_to_unban}")
        return jsonify({'status': f'IP {ip_to_unban} unbanned.'})
    else:
        return jsonify({'error': 'IP not found in banned list.'}), 404

@app.route('/log-command', methods=['GET', 'POST'])
def log_command():
    ip = get_client_ip()
    if request.method == "GET":
        protocol = request.environ.get("SERVER_PROTOCOL", "HTTP/1.1")
        message = f"GET {request.path} {protocol} Not Allowed: Use POST"
        return render_template("error.html", message=message), 405
    data = request.get_json()
    command = data.get('command', '').strip()
    if command:
        logger.info(f"Command from {ip}: {command}")
    return jsonify({'status': 'logged'})

@app.route('/kick', methods=['POST', 'GET'])
def kick():
    if request.method == 'GET':
        return render_template("error.html", message="Error: Use POST"), 405
    
    data = request.get_json() or {}
    target_ip = data.get("ip", "").strip()
    if not target_ip:
        return jsonify({'error': 'Target IP must be provided.'}), 400
    reason = data.get('reason', 'Kicked for 5 seconds.')
    banned_ips[target_ip] = reason
    save_banned_ips(banned_ips)
    logger.warning(f"Kicked IP {target_ip} for 5 seconds: {reason}")

    # Schedule to unban the target IP after 5 seconds.
    def unban_after_kick():
        if target_ip in banned_ips and banned_ips[target_ip] == reason:
            del banned_ips[target_ip]
            save_banned_ips(banned_ips)
            logger.info(f"Auto-unbanned IP {target_ip} after kick timeout")
    timer = threading.Timer(5.0, unban_after_kick)
    timer.start()

    return jsonify({'status': f'IP {target_ip} kicked for 5 seconds.'})

@app.route('/gui_data')
def gui_data():
    if active_clients is None:
        return jsonify([])
    now = time.time()
    data = [{'ip': ip, 'page': data['page'], 'last_seen': data['last_seen']} for ip, data in active_clients.items() if now - data['last_seen'] < 300]
    return jsonify(data)

@app.route('/gui_action', methods=['POST'])
def gui_action():
    if actions_queue is None:
        return jsonify({'status': 'no queue'})
    data = request.json
    actions_queue.put(data)
    return jsonify({'status': 'ok'})

@app.route('/')
def home():
    client_ip = get_client_ip()
    # Home-page visits aren't logged (they poll constantly and would drown
    # the console; see log_connection).
    try:
        quiz_dir = os.path.join(os.path.dirname(__file__), "non_static", "quiz")
        quizzes = []
        if os.path.exists(quiz_dir):
            for file in os.listdir(quiz_dir):
                if file.endswith(".txt"):
                    quiz_name = os.path.splitext(file)[0]
                    quizzes.append(quiz_name)
        user_uploaded = get_owned_quizzes(
            ACCOUNT_STORE, flask_session["username"])
        
        # Load plays
        plays = load_plays()
        for q in quizzes:
            if q not in plays:
                plays[q] = 0
        save_plays(plays)
        
        # Sort quizzes by plays descending
        quizzes_sorted = sorted(quizzes, key=lambda x: plays.get(x, 0), reverse=True)
        
        banners = {q: banner_image_path(q) for q in quizzes_sorted}
        return render_template('home.html', quizzes=quizzes_sorted, user_uploaded=user_uploaded,
                               plays=plays, banners=banners,
                               session_username=flask_session["username"]), 200
    except Exception as e:
        logger.error(f"Error in home route: {str(e)}")
        return f"Error in home route: {str(e)}", 500

@app.route('/robots.txt')
def robots():
    return '''<!DOCTYPE html>
<html>
<head>
    <style>
        body {
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            font-family: Arial, sans-serif;
        }
        h1 {
            font-size: 15rem;
            color: white;
            text-shadow: 5px 5px 20px rgba(0,0,0,0.5);
            margin: 0;
            text-align: center;
        }
    </style>
</head>
<body>
    <h1>LOL BOT</h1>
</body>
</html>''', 200, {'Content-Type': 'text/html; charset=utf-8'}

@app.route('/get_avatars', methods=['GET'])
def get_avatars():
    avatars_dir = os.path.join(os.path.dirname(__file__), "data", "avatars")
    avatars = []
    if os.path.exists(avatars_dir):
        for file in os.listdir(avatars_dir):
            if file.lower().endswith('.png'):
                avatar_name = os.path.splitext(file)[0]
                avatars.append(avatar_name)
    return jsonify({'avatars': sorted(avatars)})

@app.route('/data/avatars/<avatar_name>.png')
def get_avatar_image(avatar_name):
    avatars_dir = os.path.join(os.path.dirname(__file__), "data", "avatars")
    avatar_file = os.path.join(avatars_dir, f"{avatar_name}.png")
    if os.path.exists(avatar_file):
        return send_file(avatar_file, mimetype='image/png')
    else:
        return "Avatar not found", 404

@app.route('/data/icon.png')
def get_icon():
    # The file may be icon.png or icon.PNG depending on where it was
    # uploaded from - check both (Linux filesystems are case-sensitive).
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    for name in ("icon.png", "icon.PNG"):
        icon_file = os.path.join(data_dir, name)
        if os.path.exists(icon_file):
            return send_file(icon_file, mimetype='image/png')
    return "Icon not found", 404

@app.route('/deletequiz', methods=['GET', 'POST'])
def deletequiz():
    if request.method == "GET":
        quiz = (request.args.get("quiz") or "").strip()
        if not _owns_quiz(quiz):
            abort(404)
        logger.info(f"Account {flask_session['username']} opened delete confirmation for {quiz}")
        return render_template("deletequiz_confirm.html", quiz=quiz)

    quiz_to_delete = (request.form.get("quiz") or "").strip()
    if not _owns_quiz(quiz_to_delete):
        abort(404)

    base_name = _canonical_quiz_name(quiz_to_delete)
    quiz_dir = os.path.join(os.path.dirname(__file__), "non_static", "quiz")
    quiz_file = os.path.join(quiz_dir, base_name + ".txt")
    if os.path.exists(quiz_file):
        os.remove(quiz_file)
    remove_owned_quiz(ACCOUNT_STORE, flask_session["username"], base_name)
    logger.info(f"Account {flask_session['username']} deleted quiz: {base_name}")

    try:
        images_dir = os.path.join(UPLOAD_FOLDER, secure_filename(base_name))
        if os.path.isdir(images_dir):
            shutil.rmtree(images_dir)
        meta_file = os.path.join(
            os.path.dirname(__file__), "non_static", "question",
            f"{base_name}_images.json")
        if os.path.exists(meta_file):
            os.remove(meta_file)
    except OSError as error:
        logger.warning(f"Could not fully clean up quiz files for {base_name}: {error}")

    return redirect(url_for("home"))

@app.route('/addquiz', methods=['GET', 'POST'])
def addquiz():
    client_ip = get_client_ip()
    if request.method == 'GET':
        logger.info(f"{client_ip} accessed add quiz page")
    if request.method == 'POST':
        uploaded_file = request.files.get("quiz_file")
        if uploaded_file:
            safe_filename = secure_filename(uploaded_file.filename or "")
            if not safe_filename.lower().endswith(".txt"):
                return render_template(
                    "addquiz.html", message="Please upload a .txt quiz file."), 400
            quiz_name = os.path.splitext(safe_filename)[0]
            if not quiz_name:
                return render_template(
                    "addquiz.html", message="The uploaded filename is not valid."), 400
            if os.path.exists(os.path.join(
                    os.path.dirname(__file__), "non_static", "quiz",
                    safe_filename)) and not _owns_quiz(quiz_name):
                return render_template(
                    "addquiz.html", message="A quiz with that name already exists."), 409
            content = uploaded_file.read().decode("utf-8")
            # Normalize line endings so Windows text-mode writing doesn't
            # double CRLF uploads into '\r\r\n' on disk.
            content = content.replace('\r\n', '\n').replace('\r', '\n')
            # Validate and save the file
            quiz_dir = os.path.join(os.path.dirname(__file__), "non_static", "quiz")
            if not os.path.exists(quiz_dir):
                os.makedirs(quiz_dir)
            file_path = os.path.join(quiz_dir, safe_filename)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

            add_owned_quiz(
                ACCOUNT_STORE, flask_session["username"], quiz_name)
            # Initialize play count for uploaded quiz
            plays = load_plays()
            if quiz_name not in plays:
                plays[quiz_name] = 0
                save_plays(plays)

            # Optional banner image shown next to the quiz in the lists.
            banner_file = request.files.get("banner")
            if banner_file and banner_file.filename:
                banner_ext = os.path.splitext(secure_filename(banner_file.filename))[1].lower()
                if banner_ext in ALLOWED_EXTENSIONS:
                    quiz_image_dir = os.path.join(UPLOAD_FOLDER, secure_filename(quiz_name))
                    if not os.path.exists(quiz_image_dir):
                        os.makedirs(quiz_image_dir)
                    with open(os.path.join(quiz_image_dir, f'banner{banner_ext}'), 'wb') as bf:
                        shutil.copyfileobj(banner_file, bf)

            return render_template("addquiz.html", message="Quiz uploaded successfully!")
    return render_template("addquiz.html")

def clean_kahoot_text(text):
    """Strip HTML tags and unescape entities from Kahoot question text."""
    text = str(text)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    return text.strip().replace('\n', ' ')

def _kahoot_abs_url(u):
    """Normalises a Kahoot-relative cover path to an absolute URL."""
    u = (u or '').strip()
    if not u:
        return ''
    if not urllib.parse.urlparse(u).netloc:
        u = 'https://media.kahoot.it' + (u if u.startswith('/') else '/' + u)
    return u

def _kahoot_cover_url(data):
    """Best-effort cover image URL from Kahoot card/search JSON. The cover
    is normally a plain media.kahoot.it URL string, but older payloads nest
    it as a dict with a 'url' member."""
    if not isinstance(data, dict):
        return ''
    cover = data.get('cover')
    if not isinstance(cover, (dict, str)):
        card = data.get('card')
        cover = card.get('cover') if isinstance(card, dict) else None
    if isinstance(cover, str):
        return _kahoot_abs_url(cover)
    if isinstance(cover, dict):
        return _kahoot_abs_url(str(cover.get('url') or ''))
    return ''

def fetch_kahoot_quiz(uuid):
    """Fetch a public Kahoot quiz by UUID and return its raw JSON.

    Uses the same unauthenticated REST API that browser scrapers rely on.
    Only works for public quizzes; Kahoot may change this at any time.
    """
    urls = [
        f"https://create.kahoot.it/rest/kahoots/{uuid}/card/?includeKahoot=true",
        f"https://create.kahoot.it/rest/kahoots/{uuid}",
    ]
    last_error = None
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'QuizFabric/1.0'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            last_error = e
    raise RuntimeError(f"Could not fetch Kahoot quiz: {last_error}")

def _find_uuid(obj, depth=0):
    """Recursively find the first GUID-looking 'uuid' value in a nested dict/list."""
    if depth > 6 or obj is None:
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == 'uuid' and isinstance(v, str) and re.match(r'^[0-9a-fA-F-]{10,}$', v):
                return v
        for v in obj.values():
            found = _find_uuid(v, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_uuid(item, depth + 1)
            if found:
                return found
    return None

def search_kahoot_many(query, cursor=0, limit=20):
    """Search Kahoot for public quizzes. Returns ([(uuid, title, cover), ...],
    next_cursor). The unauthenticated discover REST API pages by offset."""
    params = urllib.parse.urlencode({
        'query': query,
        'limit': limit,
        'cursor': cursor,
        'searchCluster': 1,
        'includeExtendedCounters': 'false',
        'inventoryItemId': 'ANY',
    })
    url = f"https://create.kahoot.it/rest/kahoots/?{params}"
    req = urllib.request.Request(url, headers={'User-Agent': 'QuizFabric/1.0'})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    results = []
    for entity in (data.get('entities') or []):
        card = entity.get('card') or {}
        uuid = card.get('uuid') or _find_uuid(entity)
        title = card.get('title') or 'Kahoot quiz'
        cover = card.get('cover')
        if isinstance(cover, str):
            cover_url = _kahoot_abs_url(cover)
        elif isinstance(cover, dict):
            cover_url = _kahoot_abs_url(str(cover.get('url') or ''))
        else:
            cover_url = ''
        if uuid:
            results.append((uuid, str(title), cover_url))
    return results, cursor + len(results)

def search_kahoot(query):
    """Search Kahoot for public quizzes and return the UUID of the first result.

    Uses the unauthenticated discover REST API; returns None when the
    search yields nothing usable.
    """
    results, _ = search_kahoot_many(query, 0, 1)
    return results[0][0] if results else None

def fetch_blooket_game(game_id):
    """Fetch a public Blooket game by ID and return its raw JSON."""
    urls = [
        f"https://api.blooket.com/api/games?gameId={game_id}",
        f"https://play.blooket.com/api/gamequestionsets?gameId={game_id}",
    ]
    last_error = None
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'QuizFabric/1.0'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            last_error = e
    raise RuntimeError(f"Could not fetch Blooket game: {last_error}")

def blooket_to_quizfabric(data):
    """Convert Blooket game JSON into QuizFabric .txt content.

    Multiple-choice ('mc', 't/f') questions become multiple choice,
    typing ('typing', 'input') questions become type-answer. Unsupported
    question types are skipped.
    Returns (title, content, kept_count, question_images) where
    question_images is a list of (final_question_index, image_url).
    """
    game = data.get('game', data)
    title = clean_kahoot_text(game.get('title') or 'Untitled Blooket') or 'Untitled Blooket'
    questions = game.get('questions') or []

    blocks = []
    question_images = []
    kept = 0
    for q in questions:
        q_type = str(q.get('questionType') or '').lower()
        question = clean_kahoot_text(q.get('question') or '')
        if not question:
            continue
        answers = [clean_kahoot_text(a or '') for a in (q.get('answers') or [])]
        answers = [a for a in answers if a]
        correct = [clean_kahoot_text(a or '') for a in (q.get('correctAnswers') or [])]
        correct = [a for a in correct if a]
        lines = []
        if q_type in ('typing', 'input'):
            # Type-answer question: keep a single correct answer
            answer = correct[0] if correct else (answers[0] if answers else '')
            if not answer:
                continue
            lines = ['*' + answer]
        elif answers:
            # Multiple choice / true-false: mark the correct answers with '*'
            for a in answers:
                lines.append('*' + a if a in correct else a)
        else:
            continue
        # read_quiz expects a question block, then the answers as the next block
        blocks.append(question + '\n\n' + '\n'.join(lines))
        img = q.get('questionImage')
        if isinstance(img, str) and img.strip():
            question_images.append((kept, img.strip()))
        kept += 1

    return title, '\n\n'.join(blocks), kept, question_images

def _steal_blooket(quiz_input):
    """Fetches and converts a Blooket game. Returns
    (title, content, kept, question_images). Raises ValueError with a
    user-facing message.

    Blooket has no anonymous search (its discover page redirects to the
    login page), so only a game ID or link can be used."""
    # Accept links like https://dashboard.blooket.com/set/<id>,
    # https://www.blooket.com/play?id=<id>, or just the game ID.
    m = re.search(r'([0-9a-fA-F]{20,})', quiz_input)
    game_id = m.group(1) if m else ''
    if not game_id:
        raise ValueError("That doesn't look like a Blooket game ID or link.")
    try:
        data = fetch_blooket_game(game_id)
        title, content, kept, question_images = blooket_to_quizfabric(data)
    except Exception as e:
        raise ValueError(f"Could not fetch that Blooket game: {e}")
    if kept == 0:
        raise ValueError("That Blooket game has no supported questions (multiple choice or type answer), so there is nothing to import.")
    return title, content, kept, question_images

def _num(d, *keys):
    """Return the first numeric value found under any of the given keys."""
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None

def _fmt_num(x):
    """Format a float without a trailing .0 when it is a whole number."""
    return str(int(x)) if x == int(x) else str(x)

def kahoot_to_quizfabric(data):
    """Convert Kahoot quiz JSON into QuizFabric .txt content.

    Supported question types: multiple choice ('quiz'), type answer
    ('open_ended'), puzzle ('jumble') and slider ('slider'). Other types
    (survey, content, ...) are skipped because QuizFabric cannot
    represent them.
    Returns (title, content, kept_count, question_images) where
    question_images is a list of (final_question_index, image_url).
    """
    # With the /card/?includeKahoot=true endpoint the quiz lives under 'kahoot'
    kahoot = data.get('kahoot', data)
    title = clean_kahoot_text(kahoot.get('title') or 'Untitled Kahoot') or 'Untitled Kahoot'
    questions = kahoot.get('questions') or []

    blocks = []
    question_images = []
    kept = 0
    for q in questions:
        q_type = q.get('type')
        if q_type not in ('quiz', 'open_ended', 'jumble', 'slider'):
            continue  # QuizFabric doesn't support this question type
        question = clean_kahoot_text(q.get('question') or '')
        if not question:
            continue
        choices = q.get('choices') or []
        lines = []
        if q_type == 'slider':
            # Kahoot slider: start/end/correct (and optional tolerance),
            # possibly nested in a range object depending on the API version.
            q_min = _num(q, 'start', 'min')
            q_max = _num(q, 'end', 'max')
            q_correct = _num(q, 'correct', 'correctAnswer', 'answer')
            q_tolerance = _num(q, 'tolerance')
            range_obj = q.get('range') or q.get('choiceRange') or {}
            if isinstance(range_obj, dict):
                if q_min is None: q_min = _num(range_obj, 'start', 'min')
                if q_max is None: q_max = _num(range_obj, 'end', 'max')
                if q_correct is None: q_correct = _num(range_obj, 'correct', 'answer')
                if q_tolerance is None: q_tolerance = _num(range_obj, 'tolerance')
            if q_correct is None:
                for c in choices:
                    if c.get('correct'):
                        try:
                            q_correct = float(c.get('answer'))
                        except (TypeError, ValueError):
                            pass
                        break
            if q_min is None or q_max is None or q_correct is None:
                continue
            lines = [_fmt_num(q_min), _fmt_num(q_max), '*' + _fmt_num(q_correct)]
            if q_tolerance:
                lines.append('~' + _fmt_num(q_tolerance))
            question = '[SLIDER]' + question
        elif q_type == 'jumble':
            # The order in the Kahoot choices array IS the correct order
            for c in choices:
                answer = clean_kahoot_text(c.get('answer') or '')
                if not answer:
                    continue
                lines.append(answer)
            if len(lines) < 2:
                continue
            question = '[PUZZLE]' + question
        elif q_type == 'open_ended':
            # Type-answer question: keep a single correct answer
            correct = [c.get('answer') for c in choices if c.get('correct')]
            answer = clean_kahoot_text(correct[0] if correct else (choices[0].get('answer') if choices else ''))
            if not answer:
                continue
            lines = ['*' + answer]
        else:
            # Multiple choice: mark the correct answers with '*'
            for c in choices:
                answer = clean_kahoot_text(c.get('answer') or '')
                if not answer:
                    continue
                lines.append('*' + answer if c.get('correct') else answer)
            if not lines:
                continue
        # read_quiz expects a question block, then the answers as the next block
        blocks.append(question + '\n\n' + '\n'.join(lines))
        img = q.get('image')
        if isinstance(img, str) and img.strip():
            question_images.append((kept, img.strip()))
        kept += 1

    return title, '\n\n'.join(blocks), kept, question_images

def _steal_kahoot(quiz_input, search_query):
    """Fetches and converts a Kahoot quiz. Returns
    (title, content, kept, question_images). Raises ValueError with a
    user-facing message."""
    if search_query:
        logger.info(f"SRH {get_client_ip()} is searching \"{search_query}\"")
        try:
            uuid = search_kahoot(search_query)
        except Exception as e:
            raise ValueError(f"Could not search Kahoot: {e}")
        if not uuid:
            raise ValueError(f'No public Kahoot quizzes found for "{search_query}".')
    else:
        # Accept full links like https://create.kahoot.it/details/<slug>/<uuid>
        # or just the UUID; anything else is treated as a search term.
        uuid = quiz_input.rstrip('/').split('/')[-1].split('?')[0]
        if not re.match(r'^[0-9a-fA-F-]{10,}$', uuid):
            logger.info(f"SRH {get_client_ip()} is searching \"{quiz_input}\"")
            try:
                uuid = search_kahoot(quiz_input)
            except Exception as e:
                raise ValueError(f"Could not search Kahoot: {e}")
            if not uuid:
                raise ValueError(f'No public Kahoot quizzes found for "{quiz_input}".')
    try:
        data = fetch_kahoot_quiz(uuid)
        title, content, kept, question_images = kahoot_to_quizfabric(data)
        cover_url = _kahoot_cover_url(data)
    except Exception as e:
        raise ValueError(f"Could not fetch that Kahoot quiz: {e}")
    if kept == 0:
        raise ValueError("That Kahoot quiz has no multiple-choice or type-answer questions, so there is nothing to import.")
    return title, content, kept, question_images, cover_url

def _save_stolen_quiz(owner, title, content, question_images, source='Kahoot', cover_url=''):
    """Saves a stolen quiz like an upload and steals question images plus the
    cover banner (best-effort). Returns the quiz name."""
    stem = secure_filename(title)[:80] or f'{source.lower()}-quiz'
    quiz_dir = os.path.join(os.path.dirname(__file__), "non_static", "quiz")
    if not os.path.exists(quiz_dir):
        os.makedirs(quiz_dir)
    filename = stem + ".txt"
    suffix = 2
    while os.path.exists(os.path.join(quiz_dir, filename)):
        filename = f"{stem}-{suffix}.txt"
        suffix += 1
    with open(os.path.join(quiz_dir, filename), "x", encoding="utf-8") as f:
        f.write(content)

    quiz_name = os.path.splitext(filename)[0]
    add_owned_quiz(ACCOUNT_STORE, owner, quiz_name)
    plays = load_plays()
    if quiz_name not in plays:
        plays[quiz_name] = 0
        save_plays(plays)

    images_stolen = 0
    for q_index, img_url in question_images:
        try:
            image_data, file_ext = download_image_from_url(img_url)
            basename = urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(img_url).path)) or f'kahoot-q{q_index + 1}{file_ext}'
            stem, _ = os.path.splitext(basename)
            _store_quiz_image(quiz_name, image_data, f'{stem}{file_ext}', q_index, owner)
            images_stolen += 1
        except Exception as e:
            logger.warning(f"Could not steal question image for '{title}' Q{q_index + 1}: {e}")

    if cover_url:
        try:
            cover_data, cover_ext = download_image_from_url(cover_url)
            quiz_image_dir = os.path.join(UPLOAD_FOLDER, secure_filename(quiz_name))
            if not os.path.exists(quiz_image_dir):
                os.makedirs(quiz_image_dir)
            with open(os.path.join(quiz_image_dir, f'banner{cover_ext}'), 'wb') as bf:
                shutil.copyfileobj(cover_data, bf)
        except Exception as e:
            logger.warning(f"Could not steal quiz banner for '{title}': {e}")

    logger.info(f"{client_ip} stole {source} quiz '{title}' ({images_stolen} images) as '{filename}'")
    return quiz_name

# Production download cap: 1 quiz per 10 minutes and 10 per day, per IP.
# Only active when FLASK_ENV == production (wsgi.py loads .env.production
# before importing this module); the dev server stays unlimited.
STEAL_RATE_LIMIT = (os.environ.get('FLASK_ENV', '') or '').lower() == 'production'
STEAL_LIMITS_FILE = os.path.join(os.path.dirname(__file__), "data", "steal_limits.json")

def _steal_limit_ok(ip):
    if not STEAL_RATE_LIMIT:
        return True, ''
    now = time.time()
    try:
        with open(STEAL_LIMITS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        data = {}
    times = [t for t in data.get(ip, []) if now - t < 86400]
    if any(now - t < 600 for t in times):
        return False, 'Only 1 quiz download every 10 minutes'
    if len(times) >= 10:
        return False, 'Only 10 quiz downloads per day'
    return True, ''

def _steal_limit_record(ip):
    if not STEAL_RATE_LIMIT:
        return
    now = time.time()
    try:
        with open(STEAL_LIMITS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        data = {}
    times = [t for t in data.get(ip, []) if now - t < 86400]
    times.append(now)
    data[ip] = times
    try:
        with open(STEAL_LIMITS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning(f"Could not save download limits: {e}")

@app.route('/stealquiz', methods=['GET', 'POST'])
def stealquiz():
    """Fetch a Kahoot quiz by ID/URL and convert it to QuizFabric format."""
    client_ip = get_client_ip()
    if request.method == 'GET':
        return render_template("stealquiz.html")

    quiz_input = (request.form.get('quiz_id') or '').strip()
    search_query = (request.form.get('search') or '').strip()
    if not quiz_input and not search_query:
        return render_template("stealquiz.html", error="Please enter a Kahoot quiz ID, link, or search term.")
    ok, limit_msg = _steal_limit_ok(client_ip)
    if not ok:
        return render_template("stealquiz.html", error=limit_msg)

    try:
        title, content, kept, question_images, cover_url = _steal_kahoot(quiz_input, search_query)
    except ValueError as e:
        return render_template("stealquiz.html", error=str(e))

    quiz_name = _save_stolen_quiz(flask_session["username"], title, content, question_images, cover_url=cover_url)
    _steal_limit_record(client_ip)
    return redirect(url_for('edit_quiz', quiz_name=quiz_name))

@app.route('/stealblooket', methods=['GET', 'POST'])
def stealblooket():
    """Fetch a Blooket game by ID/URL and convert it to QuizFabric format."""
    client_ip = get_client_ip()
    if request.method == 'GET':
        return render_template("stealblooket.html")

    quiz_input = (request.form.get('quiz_id') or '').strip()
    if not quiz_input:
        return render_template("stealblooket.html", error="Please enter a Blooket game ID or link.")
    ok, limit_msg = _steal_limit_ok(client_ip)
    if not ok:
        return render_template("stealblooket.html", error=limit_msg)

    try:
        title, content, kept, question_images = _steal_blooket(quiz_input)
    except ValueError as e:
        return render_template("stealblooket.html", error=str(e))

    quiz_name = _save_stolen_quiz(flask_session["username"], title, content, question_images, source='Blooket')
    _steal_limit_record(client_ip)
    return redirect(url_for('edit_quiz', quiz_name=quiz_name))

@app.route('/api/searchkahoot', methods=['POST'])
def api_searchkahoot():
    """Paged Kahoot search for the 3DS steal flow."""
    q = (request.form.get('q') or '').strip()
    try:
        cursor = int(request.form.get('cursor', '0'))
        count = int(request.form.get('count', '20'))
    except ValueError:
        cursor, count = 0, 20
    if not q:
        return jsonify({'error': 'No search term'}), 400
    logger.info(f"SRH {get_client_ip()} is searching \"{q}\"")
    count = max(1, min(count, 50))
    try:
        results, next_cursor = search_kahoot_many(q, cursor, count)
    except Exception as e:
        return jsonify({'error': f'Could not search Kahoot: {e}'}), 502
    return jsonify({'results': [{'uuid': u, 'title': t, 'cover': c}
                                for u, t, c in results],
                    'next_cursor': next_cursor})

@app.route('/api/myquizzes', methods=['GET'])
def api_myquizzes():
    """Quizzes owned by the authenticated account."""
    names = sorted(get_owned_quizzes(
        ACCOUNT_STORE, flask_session["username"]))
    return jsonify({'quizzes': names})

@app.route('/api/stealquiz', methods=['POST'])
def api_stealquiz():
    """JSON Kahoot stealer for the 3DS: one field accepting an ID, link, or
    search term. Returns the saved quiz name."""
    client_ip = get_client_ip()
    value = (request.form.get('input') or '').strip()
    if not value:
        return jsonify({'error': 'Enter a search query'}), 400
    ok, limit_msg = _steal_limit_ok(client_ip)
    if not ok:
        return jsonify({'error': limit_msg}), 429
    try:
        title, content, kept, question_images, cover_url = _steal_kahoot(value, '')
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    quiz_name = _save_stolen_quiz(flask_session["username"], title, content, question_images, cover_url=cover_url)
    _steal_limit_record(client_ip)
    return jsonify({'quiz_name': quiz_name})

@app.route('/api/stealblooket', methods=['POST'])
def api_stealblooket():
    """JSON Blooket stealer for the 3DS: one field accepting a game ID or
    link. Returns the saved quiz name."""
    client_ip = get_client_ip()
    value = (request.form.get('input') or '').strip()
    if not value:
        return jsonify({'error': 'Enter a Blooket game ID or link'}), 400
    ok, limit_msg = _steal_limit_ok(client_ip)
    if not ok:
        return jsonify({'error': limit_msg}), 429
    try:
        title, content, kept, question_images = _steal_blooket(value)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    quiz_name = _save_stolen_quiz(flask_session["username"], title, content, question_images, source='Blooket')
    _steal_limit_record(client_ip)
    return jsonify({'quiz_name': quiz_name})

@app.route('/api/renamequiz', methods=['POST'])
def api_renamequiz():
    """JSON rename for the 3DS steal flow (same moves as the editor rename)."""
    client_ip = get_client_ip()
    quiz_name = (request.form.get('quiz_name') or '').strip()
    new_name = (request.form.get('new_name') or '').strip()
    if not _owns_quiz(quiz_name):
        return jsonify({'error': 'Quiz not found or not owned by this account'}), 404
    quiz_name = _canonical_quiz_name(quiz_name)
    if not _safe_quiz_name(new_name) or new_name == quiz_name:
        return jsonify({'error': 'Invalid new quiz name'}), 400

    quiz_dir = os.path.join(os.path.dirname(__file__), "non_static", "quiz")
    q_dir = os.path.join(os.path.dirname(__file__), "non_static", "question")
    quiz_file = os.path.join(quiz_dir, f"{quiz_name}.txt")
    new_quiz_file = os.path.join(quiz_dir, f"{new_name}.txt")
    if not os.path.exists(quiz_file):
        return jsonify({'error': 'Quiz not found'}), 404
    if os.path.exists(new_quiz_file):
        return jsonify({'error': 'A quiz with that name already exists'}), 400

    try:
        os.rename(quiz_file, new_quiz_file)
        old_question = os.path.join(q_dir, f"{quiz_name}.json")
        new_question = os.path.join(q_dir, f"{new_name}.json")
        if os.path.exists(old_question):
            os.rename(old_question, new_question)
        old_images_meta = os.path.join(q_dir, f"{quiz_name}_images.json")
        new_images_meta = os.path.join(q_dir, f"{new_name}_images.json")
        if os.path.exists(old_images_meta):
            os.rename(old_images_meta, new_images_meta)
        old_images_dir = os.path.join(UPLOAD_FOLDER, secure_filename(quiz_name))
        new_images_dir = os.path.join(UPLOAD_FOLDER, secure_filename(new_name))
        if os.path.exists(old_images_dir):
            os.rename(old_images_dir, new_images_dir)
    except Exception as e:
        logger.warning(f"Rename failed for {client_ip}: {e}")
        return jsonify({'error': 'Rename failed on the server'}), 500

    rename_owned_quiz(
        ACCOUNT_STORE, flask_session["username"], quiz_name, new_name)

    logger.info(f"{client_ip} renamed quiz '{quiz_name}' to '{new_name}'")
    return jsonify({'quiz_name': new_name})

@app.route('/api/deletequiz', methods=['POST'])
def api_deletequiz():
    """JSON delete for the 3DS (same cleanup as the web delete page)."""
    quiz_to_delete = (request.form.get('quiz_name') or '').strip()
    if not quiz_to_delete:
        return jsonify({'error': 'No quiz name given'}), 400
    if not _owns_quiz(quiz_to_delete):
        return jsonify({'error': 'Quiz not found or not owned by this account'}), 404

    base_name = _canonical_quiz_name(quiz_to_delete)

    quiz_dir = os.path.join(os.path.dirname(__file__), "non_static", "quiz")
    quiz_file = os.path.join(quiz_dir, base_name + ".txt")
    if os.path.exists(quiz_file):
        os.remove(quiz_file)
    remove_owned_quiz(ACCOUNT_STORE, flask_session["username"], base_name)
    try:
        images_dir = os.path.join(UPLOAD_FOLDER, secure_filename(base_name))
        if os.path.isdir(images_dir):
            shutil.rmtree(images_dir, ignore_errors=True)
        meta_file = os.path.join(os.path.dirname(__file__), "non_static", "question", f"{base_name}_images.json")
        if os.path.exists(meta_file):
            os.remove(meta_file)
    except Exception as e:
        logger.warning(
            f"Could not fully clean up quiz files for "
            f"{flask_session['username']}: {e}")

    logger.info(f"Account {flask_session['username']} deleted quiz: {base_name} (3DS)")
    return jsonify({'status': 'deleted'})

def _store_quiz_image(quiz_name, image_file, display_name, question_index, owner):
    """Validate, save, and register an image for a quiz.

    Works with any seekable file-like object (Werkzeug uploads or BytesIO).
    Returns the stored filename. Raises ValueError with a user-facing
    message when validation fails.
    """
    if not _owns_quiz(quiz_name):
        raise ValueError("Quiz not found or not owned by this account")
    quiz_name = _canonical_quiz_name(quiz_name)

    # Get file extension
    filename = secure_filename(display_name or 'image')
    file_ext = os.path.splitext(filename)[1].lower()

    # Validate file extension
    if file_ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f'File type not allowed. Only {", ".join(ALLOWED_EXTENSIONS)} are supported')

    # Check file size before saving
    image_file.seek(0, os.SEEK_END)
    file_size = image_file.tell()
    image_file.seek(0)

    if file_size > MAX_FILE_SIZE:
        raise ValueError('File too large. Maximum size is 300MB')

    # Create quiz-specific directory
    quiz_image_dir = os.path.join(UPLOAD_FOLDER, secure_filename(quiz_name))
    if not os.path.exists(quiz_image_dir):
        os.makedirs(quiz_image_dir)

    # Check image count (ignore the 3DS .565 conversion copies)
    existing_images = [f for f in os.listdir(quiz_image_dir)
                       if not f.endswith(".565")]
    if len(existing_images) >= MAX_IMAGES_PER_QUIZ:
        raise ValueError(f'Maximum {MAX_IMAGES_PER_QUIZ} images per quiz reached')

    # Generate unique filename
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    new_filename = f"{timestamp}{file_ext}"
    file_path = os.path.join(quiz_image_dir, new_filename)

    # Save file
    with open(file_path, 'wb') as f:
        shutil.copyfileobj(image_file, f)

    # Pre-convert for the 3DS client (best-effort; the serving route
    # also converts on demand if this ever fails).
    try:
        from non_static.imgconv import png_to_rgb8, converted_path
        png_to_rgb8(file_path, converted_path(file_path))
    except Exception:
        pass

    # Update per-question images metadata if question_index provided
    images_meta_dir = os.path.join(os.path.dirname(__file__), "non_static", "question")
    if not os.path.exists(images_meta_dir):
        os.makedirs(images_meta_dir)
    images_meta_file = os.path.join(images_meta_dir, f"{quiz_name}_images.json")

    question_key = ''
    try:
        if os.path.exists(images_meta_file):
            with open(images_meta_file, 'r', encoding='utf-8') as mf:
                images_meta = json.load(mf)
        else:
            images_meta = {}
    except Exception:
        images_meta = {}

    if question_index is not None and question_index != '':
        question_key = str(int(question_index))
        images_meta.setdefault(question_key, [])
        images_meta[question_key].append(new_filename)
        try:
            with open(images_meta_file, 'w', encoding='utf-8') as mf:
                json.dump(images_meta, mf)
        except Exception as e:
            logger.warning(f"Failed to update images metadata for {owner}: {e}")

    # Also maintain a human-readable image-names.json inside the quiz upload folder
    try:
        uploaded_names_file = os.path.join(quiz_image_dir, 'image-names.json')
        if os.path.exists(uploaded_names_file):
            with open(uploaded_names_file, 'r', encoding='utf-8') as uf:
                uploaded_names = json.load(uf)
        else:
            uploaded_names = {}
    except Exception:
        uploaded_names = {}

    # Use the original filename as display name (before timestamping)
    display_name = filename
    uploaded_names.setdefault(question_key, [])
    uploaded_names[question_key].append({'filename': new_filename, 'name': display_name})
    try:
        with open(uploaded_names_file, 'w', encoding='utf-8') as uf:
            json.dump(uploaded_names, uf)
    except Exception as e:
        logger.warning(f"Failed to update image metadata for {owner}: {e}")

    return new_filename


def download_image_from_url(image_url):
    """Download an image from a URL into memory.

    Returns (file-like BytesIO, file_ext). Raises ValueError with a
    user-facing message on failure. Relative paths are resolved against
    media.kahoot.it (used for Kahoot question images).
    """
    parsed = urllib.parse.urlparse(image_url)
    if parsed.scheme not in ('http', 'https'):
        if image_url.startswith('/'):
            return download_image_from_url('https://media.kahoot.it' + image_url)
        raise ValueError('Only http:// and https:// URLs are supported')

    # Guess the extension from the URL path first; fall back to the
    # Content-Type header of the response.
    file_ext = os.path.splitext(urllib.parse.unquote(parsed.path))[1].lower()

    req = urllib.request.Request(image_url, headers={'User-Agent': 'QuizFabric/1.0'})
    image_data = io.BytesIO()
    content_type = ''
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content_type = (resp.headers.get('Content-Type') or '').split(';')[0].strip().lower()
            # Read at most MAX_FILE_SIZE + 1 bytes so oversized files are
            # rejected without downloading them in full.
            remaining = MAX_FILE_SIZE + 1
            while remaining > 0:
                chunk = resp.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                image_data.write(chunk)
                remaining -= len(chunk)
    except Exception as e:
        raise ValueError(f'Failed to download image: {e}')

    if image_data.tell() > MAX_FILE_SIZE:
        raise ValueError('File too large. Maximum size is 300MB')
    image_data.seek(0)

    if file_ext not in ALLOWED_EXTENSIONS:
        file_ext = CONTENT_TYPE_EXTENSIONS.get(content_type, '')
    if file_ext not in ALLOWED_EXTENSIONS:
        raise ValueError('Could not determine image type. Only .png, .webp, .jpg, .jpeg, .gif are supported')

    return image_data, file_ext


@app.route('/upload_quiz_image/<quiz_name>', methods=['POST'])
def upload_quiz_image(quiz_name):
    """Upload image for a quiz with validation"""
    client_ip = get_client_ip()
    if not _owns_quiz(quiz_name):
        return jsonify({'error': 'Quiz not found or not owned by this account'}), 404

    try:
        # Check if file is in request
        if 'image' not in request.files:
            return jsonify({'error': 'No image file provided'}), 400

        image_file = request.files['image']

        if image_file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        question_index = request.form.get('question_index')
        try:
            new_filename = _store_quiz_image(quiz_name, image_file, image_file.filename,
                                             question_index, flask_session["username"])
        except ValueError as e:
            return jsonify({'error': str(e)}), 400

        # Return image URL
        image_url = f"/UploadedImages/{secure_filename(quiz_name)}/{new_filename}"
        logger.info(f"Image uploaded by {client_ip} for quiz '{quiz_name}': {new_filename}")

        return jsonify({
            'success': True,
            'url': image_url,
            'filename': new_filename
        }), 200

    except Exception as e:
        logger.error(f"Image upload failed for {client_ip}: {str(e)}")
        return jsonify({'error': f'Upload failed: {str(e)}'}), 500


@app.route('/upload_quiz_image_url/<quiz_name>', methods=['POST'])
def upload_quiz_image_url(quiz_name):
    """Download an image from a URL and store it for a quiz"""
    client_ip = get_client_ip()
    if not _owns_quiz(quiz_name):
        return jsonify({'error': 'Quiz not found or not owned by this account'}), 404

    try:
        data = request.get_json(silent=True) or {}
        image_url = (data.get('image_url') or data.get('url') or '').strip()
        if not image_url:
            return jsonify({'error': 'No image URL provided'}), 400

        try:
            image_data, file_ext = download_image_from_url(image_url)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400

        basename = urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(image_url).path)) or f'image{file_ext}'
        stem, _ = os.path.splitext(basename)
        display_name = f'{stem}{file_ext}'

        question_index = data.get('question_index')
        try:
            new_filename = _store_quiz_image(quiz_name, image_data, display_name,
                                             question_index, flask_session["username"])
        except ValueError as e:
            return jsonify({'error': str(e)}), 400

        # Return image URL
        image_url_path = f"/UploadedImages/{secure_filename(quiz_name)}/{new_filename}"
        logger.info(f"Image downloaded by {client_ip} from URL for quiz '{quiz_name}': {new_filename}")

        return jsonify({
            'success': True,
            'url': image_url_path,
            'filename': new_filename
        }), 200

    except Exception as e:
        logger.error(f"Image URL download failed for {client_ip}: {str(e)}")
        return jsonify({'error': f'Download failed: {str(e)}'}), 500


# Let's Encrypt HTTP-01 challenge support (certbot webroot mode writes the
# challenge token files into this directory; this route serves them back).
ACME_WEBROOT = os.path.join(os.path.dirname(__file__), "certbot_webroot")

# Let's Encrypt (certbot) certificate location
HTTPS_DOMAIN = "play.lucasgeitgey.com"
CERT_DIR = os.path.join(os.path.expanduser("~"), "quizfabric-certs", "live", HTTPS_DOMAIN)

@app.route('/.well-known/acme-challenge/<path:filename>')
def acme_challenge(filename):
    # certbot (and get_cert.py) write challenge files as
    # <webroot>/.well-known/acme-challenge/<token>
    file_path = os.path.join(ACME_WEBROOT, ".well-known", "acme-challenge",
                             secure_filename(filename))
    if os.path.exists(file_path) and os.path.isfile(file_path):
        return send_file(file_path)
    return "Not found", 404


_IMG_META_CACHE = {}

def _image_question_number(quiz_name, fname_stem):
    """1-based question number an image belongs to, or 0 when unknown.

    The 3DS requests images by their converted name (<stem>.565); the
    per-quiz images metadata maps each question index to the original
    image filenames, so match on the stem.
    """
    meta = _IMG_META_CACHE.get(quiz_name)
    if meta is None:
        meta_file = os.path.join(os.path.dirname(__file__), "non_static",
                                 "question", f"{quiz_name}_images.json")
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            meta = {}
        _IMG_META_CACHE[quiz_name] = meta
    for q_index, names in meta.items():
        for name in names:
            if os.path.splitext(str(name))[0] == fname_stem:
                try:
                    return int(q_index) + 1
                except ValueError:
                    return 0
    return 0


@app.route('/UploadedImages/<path:filepath>')
def serve_uploaded_image(filepath):
    """Serve uploaded images"""
    try:
        file_path = os.path.join(UPLOAD_FOLDER, filepath)
        # The 3DS client asks for the pre-converted copy (<name>.565); if it
        # does not exist yet, generate it from the original image on demand.
        # Stale copies (old converters kept only 24 GIF frames) are also
        # regenerated here - convert_on_demand is a no-op when up to date.
        if filepath.endswith(".565"):
            # Log the 3DS question-image fetch: which question, which quiz.
            try:
                quiz_name, fname = os.path.split(filepath)
                stem = os.path.splitext(fname)[0]
                q_num = _image_question_number(quiz_name, stem)
                logger.info(f"IMG {get_client_ip()} Q{q_num or '?'} {quiz_name}")
            except Exception:
                pass
            from non_static.imgconv import convert_on_demand
            convert_on_demand(file_path)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            return send_file(file_path)
        return jsonify({'error': 'Image not found'}), 404
    except Exception as e:
        return jsonify({'error': 'Failed to serve image'}), 500

@app.route('/quiz/<quiz_name>', methods=['GET'])
def get_quiz_json(quiz_name):
    # quiz_name is defined from the URL.
    flask_session['quiz_name'] = quiz_name
    client_ip = get_client_ip()
    logger.info(f"{client_ip} is playing quiz '{quiz_name}'")

    try:
        questions = read_quiz(quiz_name)
    except Exception as e:
        logger.error(f"Error reading quiz '{quiz_name}' for {client_ip}: {str(e)}")
        return f"Error reading quiz: {str(e)}", 500

    if not questions:
        return "No questions available.", 404

    # Load current index for this client.
    question_file = os.path.join(os.path.dirname(__file__), "non_static", "question", f"{quiz_name}.json")
    try:
        with open(question_file, "r", encoding="utf-8") as f:
            question_data = json.load(f)
    except Exception as e:
        question_data = {}
    if not isinstance(question_data, dict):
        question_data = {}

    current_index = question_data.get(client_ip, 0)
    
    # Increment play count only on first start of solo play
    if current_index == 0 and not flask_session.get('solo_started_' + quiz_name):
        plays = load_plays()
        plays[quiz_name] = plays.get(quiz_name, 0) + 1
        save_plays(plays)
        flask_session['solo_started_' + quiz_name] = True
    
    # If the current question index is out-of-range, render finish.html
    if current_index >= len(questions):
        logger.info(f"Quiz '{quiz_name}' completed by {client_ip}")
        results = get_quiz_times_for_user(quiz_name, client_ip)
        return render_template("finish.html", quiz_name=quiz_name, results=results)
    
    # Otherwise, show the current question.
    question = questions[current_index]
    # Attach any images for this question (by index) if present
    images_meta_file = os.path.join(os.path.dirname(__file__), "non_static", "question", f"{quiz_name}_images.json")
    try:
        if os.path.exists(images_meta_file):
            with open(images_meta_file, 'r', encoding='utf-8') as mf:
                images_meta = json.load(mf)
        else:
            images_meta = {}
    except Exception:
        images_meta = {}
    imgs = images_meta.get(str(current_index), [])
    # Prefer uploaded image-names.json in the UploadedImages folder for display names
    uploaded_names_file = os.path.join(UPLOAD_FOLDER, secure_filename(quiz_name), 'image-names.json')
    try:
        if os.path.exists(uploaded_names_file):
            with open(uploaded_names_file, 'r', encoding='utf-8') as uf:
                uploaded_names_meta = json.load(uf)
        else:
            uploaded_names_meta = {}
    except Exception:
        uploaded_names_meta = {}

    imgs_list = []
    # images_meta (non_static) may list filenames; uploaded_names_meta maps question index to objects
    if str(current_index) in uploaded_names_meta:
        for item in uploaded_names_meta.get(str(current_index), []):
            fname = item.get('filename')
            name = item.get('name') or fname
            imgs_list.append({'url': f"/UploadedImages/{quiz_name}/{fname}", 'name': name})
    else:
        for fname in imgs:
            imgs_list.append({'url': f"/UploadedImages/{quiz_name}/{fname}", 'name': fname})
    if imgs_list:
        question['images'] = imgs_list
    display_index = current_index + 1
    quiz_total = len(questions)
    flask_session['current_question_name'] = question.get('question', 'Unknown Question')
    
    return render_template("quiz.html",
                           quiz_name=quiz_name,
                           question=question,
                           question_index=display_index,
                           quiz_total=quiz_total)

@app.route('/api/quiz/<quiz_name>', methods=['GET'])
def get_quiz_data(quiz_name):
    try:
        questions = read_quiz(quiz_name)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    # Attach images metadata to each question if available
    images_meta_file = os.path.join(os.path.dirname(__file__), "non_static", "question", f"{quiz_name}_images.json")
    try:
        if os.path.exists(images_meta_file):
            with open(images_meta_file, 'r', encoding='utf-8') as mf:
                images_meta = json.load(mf)
        else:
            images_meta = {}
    except Exception:
        images_meta = {}

    # Attach images as objects with url and name
    uploaded_names_meta = {}
    try:
        uploaded_names_file = os.path.join(UPLOAD_FOLDER, secure_filename(quiz_name), 'image-names.json')
        if os.path.exists(uploaded_names_file):
            with open(uploaded_names_file, 'r', encoding='utf-8') as uf:
                uploaded_names_meta = json.load(uf)
    except Exception:
        uploaded_names_meta = {}

    for idx, q in enumerate(questions):
        imgs_list = []
        if str(idx) in uploaded_names_meta:
            for item in uploaded_names_meta.get(str(idx), []):
                fname = item.get('filename')
                name = item.get('name') or fname
                imgs_list.append({'url': f"/UploadedImages/{quiz_name}/{fname}", 'name': name})
        else:
            imgs = images_meta.get(str(idx), [])
            for fname in imgs:
                imgs_list.append({'url': f"/UploadedImages/{quiz_name}/{fname}", 'name': fname})
        if imgs_list:
            q['images'] = imgs_list

    return jsonify(questions)

def banner_image_path(quiz_name):
    """URL path of a quiz's banner image, or '' when it has none. A banner is
    any banner.<ext> file saved in the quiz's upload folder (stolen Kahoot
    covers or the one uploaded with the quiz)."""
    quiz_dir = os.path.join(UPLOAD_FOLDER, secure_filename(quiz_name))
    if not os.path.isdir(quiz_dir):
        return ''
    try:
        entries = sorted(os.listdir(quiz_dir))
    except Exception:
        return ''
    for f in entries:
        stem, ext = os.path.splitext(f)
        if stem == 'banner' and ext in ALLOWED_EXTENSIONS:
            return f"/UploadedImages/{secure_filename(quiz_name)}/{f}"
    return ''

@app.route('/wiki', methods=['GET'])
def wiki():
    """Simple how-to guide for the 3DS and DS clients."""
    client_ip = get_client_ip()
    logger.info(f"{client_ip} accessed the wiki")
    return render_template("wiki.html")

@app.route('/api/quizzes', methods=['GET'])
def list_quizzes():
    """JSON list of every quiz, for the 3DS solo/host modes."""
    quiz_dir = os.path.join(os.path.dirname(__file__), "non_static", "quiz")
    quizzes = sorted(os.path.splitext(f)[0] for f in os.listdir(quiz_dir)
                     if f.endswith(".txt"))
    return jsonify({'quizzes': [{'name': q, 'banner': bool(banner_image_path(q))}
                                for q in quizzes]})

@app.route('/api/quizbanner/<path:quiz_name>')
def api_quizbanner(quiz_name):
    """A quiz's banner converted to the 3DS tiled format, max 64x64 (small
    enough for the solo/host list thumbnails). 404 when the quiz has none."""
    quiz_dir = os.path.join(UPLOAD_FOLDER, secure_filename(quiz_name))
    banner_src = ''
    if os.path.isdir(quiz_dir):
        try:
            entries = sorted(os.listdir(quiz_dir))
        except Exception:
            entries = []
        for f in entries:
            stem, ext = os.path.splitext(f)
            if stem == 'banner' and ext in ALLOWED_EXTENSIONS:
                banner_src = os.path.join(quiz_dir, f)
                break
    if not banner_src:
        return 'No banner', 404
    try:
        from PIL import Image as PILImage
        from non_static.imgconv import image_to_rgb8_animated_bytes
        with PILImage.open(banner_src) as im:
            payload = image_to_rgb8_animated_bytes(im, 64, 64)
        import flask
        return flask.Response(payload, mimetype='application/octet-stream')
    except Exception as e:
        logger.warning(f"Could not convert banner for '{quiz_name}': {e}")
        return 'No banner', 404

_KAHOOT_COVER_CACHE = {}

@app.route('/api/kahootcover')
def api_kahootcover():
    """Proxies a Kahoot cover image and returns it as a 3DS-tiled payload.
    size=64 (default) is for the list thumbnails, size=256 for the big
    top-screen preview. The 3DS cannot reach https:// media hosts itself,
    so the server fetches and converts it (cached in memory).

    Pass id=<kahoot-uuid> to enable the on-disk banner cache: the payload
    is stored as quiz-banners/<id>.565 (keyed by the quiz ID, not its
    name, so same-named quizzes cannot clash). Once cached, the file is
    served directly - that quiz ID is never fetched or converted again."""
    url = (request.args.get('url') or '').strip()
    quiz_id = (request.args.get('id') or '').strip()
    if not re.match(r'^[0-9a-fA-F-]{10,}$', quiz_id):
        # The 3DS sends no id parameter - derive the media id from the
        # cover URL path (e.g. .../53006e81-...-20e3532743e1[_opt]).
        base = os.path.basename(urllib.parse.urlparse(url).path)
        quiz_id = base.rsplit('_opt', 1)[0]
    if not url or not urllib.parse.urlparse(url).netloc.lower().endswith('kahoot.it'):
        return 'Bad url', 400
    try:
        size = int(request.args.get('size', '64'))
    except ValueError:
        size = 64
    if size == 256:
        max_w, max_h = 256, 96
    else:
        size, max_w, max_h = 64, 64, 64

    # On-disk banner cache: serve straight from disk when present.
    banner_path = ''
    if quiz_id and re.match(r'^[0-9a-fA-F-]{10,}$', quiz_id):
        logger.info(f"CVR {get_client_ip()} {quiz_id}")
        banner_dir = os.path.join(os.path.dirname(__file__), "quiz-banners")
        banner_name = f"{quiz_id}.565" if size == 64 else f"{quiz_id}_256.565"
        banner_path = os.path.join(banner_dir, banner_name)
        if os.path.exists(banner_path):
            return send_file(banner_path, mimetype='application/octet-stream')

    cache_key = (url, size)
    payload = _KAHOOT_COVER_CACHE.get(cache_key)
    if payload is None:
        try:
            image_data, _ = download_image_from_url(url)
            from PIL import Image as PILImage
            from non_static.imgconv import image_to_rgb8_animated_bytes
            with PILImage.open(image_data) as im:
                payload = image_to_rgb8_animated_bytes(im, max_w, max_h)
        except Exception as e:
            logger.warning(f"Could not proxy Kahoot cover: {e}")
            return 'No cover', 404
        if len(_KAHOOT_COVER_CACHE) > 128:
            _KAHOOT_COVER_CACHE.clear()
        _KAHOOT_COVER_CACHE[cache_key] = payload
        # Persist it so future requests (and server restarts) skip the
        # fetch + conversion entirely.
        if banner_path:
            try:
                os.makedirs(os.path.dirname(banner_path), exist_ok=True)
                with open(banner_path, 'wb') as bf:
                    bf.write(payload)
                logger.info(f"CAH {get_client_ip()} {quiz_id}")
            except Exception as e:
                logger.warning(f"Could not cache banner for {quiz_id}: {e}")
    import flask
    return flask.Response(payload, mimetype='application/octet-stream')

@app.route('/editquiz', methods=['GET', 'POST'])
def editquiz_select():
    client_ip = get_client_ip()
    if request.method == 'POST':
        quiz = request.form.get('quiz', '').strip()
        if _owns_quiz(quiz):
            return redirect(url_for('edit_quiz', quiz_name=quiz))
        abort(404)
    quizzes = sorted(get_owned_quizzes(
        ACCOUNT_STORE, flask_session["username"]))
    logger.info(f"{client_ip} opened the quiz editor")
    return render_template("editquiz_select.html", user_uploaded=quizzes)

@app.route('/editquiz/<quiz_name>', methods=['GET', 'POST'])
def edit_quiz(quiz_name):
    if not _owns_quiz(quiz_name):
        abort(404)
    quiz_name = _canonical_quiz_name(quiz_name)
    client_ip = get_client_ip()
    if request.method == 'GET':
        logger.info(f"{client_ip} is editing quiz '{quiz_name}'")
    # ... rest of function
    quiz_file = os.path.join(os.path.dirname(__file__), "non_static", "quiz", f"{quiz_name}.txt")
    question_file = os.path.join(os.path.dirname(__file__), "non_static", "question", f"{quiz_name}.json")
    
    if request.method == 'GET':
        # Read quiz text content.
        try:
            with open(quiz_file, "r", encoding="utf-8") as f:
                quiz_content = f.read()
        except Exception as e:
            quiz_content = ""
            logger.warning(f"Error reading quiz '{quiz_name}' for editing by {client_ip}: {str(e)}")

        return render_template("editquiz.html", quiz_name=quiz_name, quiz_content=quiz_content)
    else:
        # POST: update the quiz text and/or rename
        new_content = request.form.get("quiz_content", "")
        # Browsers submit form text with CRLF line endings; Python's text-mode
        # write on Windows would turn each '\n' into '\r\n' again, producing
        # '\r\r\n' on disk (blank lines everywhere after read_quiz strips '\r').
        new_content = new_content.replace('\r\n', '\n').replace('\r', '\n')
        new_quiz_name = request.form.get("new_quiz_name", "").strip()
        use_legacy = request.form.get("use_legacy", "false") == "true"
        
        # Handle rename
        if new_quiz_name and new_quiz_name != quiz_name:
            if not _safe_quiz_name(new_quiz_name):
                return render_template(
                    "editquiz.html", quiz_name=quiz_name,
                    quiz_content=new_content,
                    message="That quiz name is not valid."), 400
            new_quiz_file = os.path.join(os.path.dirname(__file__), "non_static", "quiz", f"{new_quiz_name}.txt")
            new_question_file = os.path.join(os.path.dirname(__file__), "non_static", "question", f"{new_quiz_name}.json")
            if os.path.exists(new_quiz_file):
                return render_template(
                    "editquiz.html", quiz_name=quiz_name,
                    quiz_content=new_content,
                    message="A quiz with that name already exists."), 409
            
            try:
                # Rename quiz file
                if os.path.exists(quiz_file):
                    os.rename(quiz_file, new_quiz_file)
                # Rename question file if it exists
                if os.path.exists(question_file):
                    os.rename(question_file, new_question_file)
                # Rename images metadata file if exists
                old_images_meta = os.path.join(os.path.dirname(__file__), "non_static", "question", f"{quiz_name}_images.json")
                new_images_meta = os.path.join(os.path.dirname(__file__), "non_static", "question", f"{new_quiz_name}_images.json")
                if os.path.exists(old_images_meta):
                    os.rename(old_images_meta, new_images_meta)
                # Rename UploadedImages directory for this quiz
                old_images_dir = os.path.join(UPLOAD_FOLDER, secure_filename(quiz_name))
                new_images_dir = os.path.join(UPLOAD_FOLDER, secure_filename(new_quiz_name))
                if os.path.exists(old_images_dir):
                    try:
                        os.rename(old_images_dir, new_images_dir)
                    except Exception as e:
                        logger.warning(f"Failed to rename images dir for {client_ip}: {e}")
                
                rename_owned_quiz(
                    ACCOUNT_STORE, flask_session["username"],
                    quiz_name, new_quiz_name)
                
                quiz_name = new_quiz_name
                quiz_file = new_quiz_file
                question_file = new_question_file
                logger.info(f"Quiz renamed to '{quiz_name}' by {client_ip}")
            except Exception as e:
                return f"Error renaming quiz: {str(e)}", 500
        
        # Write updated quiz text
        try:
            with open(quiz_file, "w", encoding="utf-8") as f:
                f.write(new_content)
            logger.info(f"Quiz '{quiz_name}' updated by {client_ip}")
            message = "Quiz updated successfully."
        except Exception as e:
            return f"Error updating quiz text: {str(e)}", 500

        return render_template("editquiz.html", quiz_name=quiz_name, quiz_content=new_content, message=message)

@app.route('/quiz/<quiz_name>/quiz/validate', methods=['POST', 'GET'])
def quiz_validate(quiz_name):
    if request.method == 'GET':
        return render_template("error.html", message="Error: Use POST"), 405
    
    answer = request.json.get('answer', '')
    time_elapsed = request.json.get('time_elapsed', '0')
    try:
        questions = read_quiz(quiz_name)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    if not questions:
        return jsonify({'error': 'No questions found.'}), 404
    question_file = os.path.join(os.path.dirname(__file__), "non_static", "question", f"{quiz_name}.json")
    try:
        with open(question_file, "r", encoding="utf-8") as f:
            question_data = json.load(f)
    except Exception as e:
        question_data = {}
    if not isinstance(question_data, dict):
        question_data = {}
    client_ip = get_client_ip()
    current_index = question_data.get(client_ip, 0)
    if current_index >= len(questions):
        current_index = 0
    q = questions[current_index]
    
    # Store current question name in session for save_time
    flask_session['current_question_name'] = q.get('question', f'Question {current_index + 1}')
    
    # Convert answer to int for MC questions
    if q['type'] == 'mc':
        try:
            answer = int(answer)
        except ValueError:
            answer = None
    is_correct = validate_answer(q, answer)
    if is_correct:
        new_index = current_index + 1
        if new_index >= len(questions):
            # Signal finish; client-side JS should redirect accordingly.
            return jsonify({'redirect': url_for('finish_quiz', quiz_name=quiz_name)})
        else:
            question_data[client_ip] = new_index
            try:
                with open(question_file, "w", encoding="utf-8") as f:
                    json.dump(question_data, f)
            except Exception as e:
                return jsonify({'error': 'Failed to update question index.'}), 500
            return jsonify({'is_correct': True})
    return jsonify({'is_correct': is_correct})

@app.route('/makequiz', methods=['GET', 'POST'])
def makequiz():
    client_ip = get_client_ip()
    if request.method == 'GET':
        logger.info(f"{client_ip} accessed make quiz page")
    # ... rest of the function
    if request.method == 'POST':
        filename = secure_filename((request.form.get("filename") or "").strip())
        quiz_content = request.form.get("quiz_content") or ""
        if not filename:
            return render_template(
                "makequiz.html", error="Please enter a valid quiz name."), 400
        quiz_file = os.path.join(
            os.path.dirname(__file__), "non_static", "quiz", f"{filename}.txt")
        if os.path.exists(quiz_file) and not _owns_quiz(filename):
            return render_template(
                "makequiz.html", error="A quiz with that name already exists."), 409
        # Check if it's JSON from modern editor
        try:
            questions = json.loads(quiz_content)
            # Convert to text format
            text_content = ""
            for q in questions:
                if q.get('type') == 'slider':
                    text_content += '[SLIDER]' + q['question'] + "\n\n"
                    text_content += _fmt_num(float(q.get('min', 0))) + "\n"
                    text_content += _fmt_num(float(q.get('max', 100))) + "\n"
                    text_content += "*" + _fmt_num(float(q.get('answer', 0))) + "\n"
                    if q.get('tolerance'):
                        text_content += "~" + _fmt_num(float(q['tolerance'])) + "\n"
                    text_content += "\n"
                    continue
                if q.get('type') == 'puzzle':
                    text_content += '[PUZZLE]' + q['question'] + "\n\n"
                    for ans in q.get('answers', []):
                        text_content += str(ans) + "\n"
                    text_content += "\n"
                    continue
                text_content += q['question'] + "\n\n"
                if q['type'] == 'mc':
                    for i, ans in enumerate(q['answers']):
                        if i == q['correctIndex']:
                            text_content += "*" + ans + "\n"
                        else:
                            text_content += ans + "\n"
                else:  # typein
                    text_content += q['answers'][0] + "\n"
                text_content += "\n"
            quiz_content = text_content.strip()
        except json.JSONDecodeError:
            # It's legacy text
            pass
        # Browsers submit form text with CRLF line endings; normalize them so
        # Windows text-mode writing doesn't produce '\r\r\n' on disk.
        quiz_content = quiz_content.replace('\r\n', '\n').replace('\r', '\n')
        # Save the quiz
        quiz_dir = os.path.join(os.path.dirname(__file__), "non_static", "quiz")
        if not os.path.exists(quiz_dir):
            os.makedirs(quiz_dir)
        quiz_file = os.path.join(quiz_dir, f"{filename}.txt")
        with open(quiz_file, "w", encoding="utf-8") as f:
            f.write(quiz_content)
        add_owned_quiz(ACCOUNT_STORE, flask_session["username"], filename)
        
        # Initialize play count for new quiz
        plays = load_plays()
        if filename not in plays:
            plays[filename] = 0
            save_plays(plays)
        
        # After creating the quiz, redirect to the edit page so user can attach images per-question
        return redirect(url_for('edit_quiz', quiz_name=filename))
    return render_template("makequiz.html")

@app.route('/logs')
def view_logs():
    client_ip = get_client_ip()
    logger.info(f"{client_ip} accessed logs page")
    logs_dir = os.path.join(os.path.dirname(__file__), "logs")
    logs_content = ""
    # if os.path.exists(logs_dir):
    #     # Get files sorted by name.
    #     files = sorted(os.listdir(logs_dir))
    #     for filename in files:
    #         file_path = os.path.join(logs_dir, filename)
    #         try:
    #             with open(file_path, "r", encoding="utf-8") as f:
    #                 file_content = f.read()
    #             if filename == "print.txt":
    #                 logs_content += file_content + "\n\n"
    #             else:
    #                 # Only show nonempty files.
    #                 if file_content.strip() != "":
    #                     logs_content += f"--- {filename} ---\n{file_content}\n\n"
    #         except Exception as e:
    #             logs_content += f"--- {filename} ---\nError reading file: {str(e)}\n\n"
    # else:
    #     logs_content = "No logs found."
    # print("Detected IP", get_client_ip(), "on /logs")
    return render_template("logs.html", logs_content=logs_content)

@app.route('/logs-content')
def logs_content():
    logs_dir = os.path.join(os.path.dirname(__file__), "logs")
    logs_text = ""
    # if os.path.exists(logs_dir):
    #     files = sorted(os.listdir(logs_dir))
    #     for filename in files:
    #         file_path = os.path.join(logs_dir, filename)
    #         try:
    #             with open(file_path, "r", encoding="utf-8") as f:
    #                 file_content = f.read()
    #             if filename == "print.txt":
    #                 logs_text += file_content + "\n\n"
    #             else:
    #                 if file_content.strip() != "":
    #                     logs_text += f"--- {filename} ---\n{file_content}\n\n"
    #         except Exception as e:
    #             logs_text += f"--- {filename} ---\nError reading file: {str(e)}\n\n"
    # else:
    #     logs_text = "No logs found."
    return logs_text

@app.route('/quiz/<quiz_name>/answer', methods=['POST', 'GET'])
def submit_answer(quiz_name):
    if request.method == 'GET':
        return render_template("error.html", message="Error: Use POST"), 405
    client_ip = get_client_ip()
    time_taken = request.form.get('time_elapsed', '0')
    question_name = flask_session.get('current_question_name', "Unknown Question")
    
    # (Your code to update the time.txt file goes here)
    # ...

    # Load quiz questions and the JSON tracking file.
    questions = read_quiz(quiz_name)
    question_file = os.path.join(os.path.dirname(__file__), "non_static", "question", f"{quiz_name}.json")
    try:
        with open(question_file, "r", encoding="utf-8") as f:
            question_data = json.load(f)
    except Exception as e:
        question_data = {}
    if not isinstance(question_data, dict):
        question_data = {}

    current_index = question_data.get(client_ip, 0)
    question = questions[current_index]
    answer = request.form.get('answer')
    if question.type == 'mc':
        try:
            answer = int(answer)
        except:
            answer = None
    elif question.get('type') == 'puzzle':
        # The puzzle UI submits the ordered items as a JSON list
        try:
            answer = json.loads(answer)
        except Exception:
            answer = None
    is_correct = validate_answer(question, answer)
    if not is_correct:
        return redirect(url_for('get_quiz_json', quiz_name=quiz_name) + '?error=incorrect')
    
    new_index = current_index + 1

    # When the new_index is equal to or exceeds the total questions,
    # redirect to the finish quiz page instead of updating the current index.
    if new_index >= len(questions):
        logger.info(f"Quiz '{quiz_name}' completed by {client_ip}")
        return redirect(url_for('finish_quiz', quiz_name=quiz_name))
    else:
        # Update the question index normally.
        question_data[client_ip] = new_index
        try:
            with open(question_file, "w", encoding="utf-8") as f:
                json.dump(question_data, f)
        except Exception as e:
            logger.error(f"Failed to update question index for {client_ip}: {str(e)}")
            return jsonify({'error': 'Failed to update question index.'}), 500
        return redirect(url_for('get_quiz_json', quiz_name=quiz_name))

@app.route('/quiz/<quiz_name>/results')
def quiz_results(quiz_name):
    client_ip = get_client_ip()
    results = get_quiz_times_for_user(quiz_name, client_ip)
    # Convert to the format expected by results.html (just the question strings)
    result_strings = [f"{entry['question']} {entry['time']}" for entry in results]
    return render_template("results.html", quiz_name=quiz_name, results=result_strings)

def get_quiz_times_for_user(quiz_name, client_ip):
    """Parse time.json and return results for a specific quiz and user as list of dicts"""
    time_file = os.path.join(os.path.dirname(__file__), "data", "time.json")
    results = []

    # Ensure time.json exists and migrate from time.txt if needed
    migrate_time_data_if_needed()

    if os.path.exists(time_file):
        try:
            with open(time_file, "r", encoding="utf-8") as f:
                time_data = json.load(f)

            # Get times for this IP and quiz
            if client_ip in time_data and quiz_name in time_data[client_ip]:
                quiz_times = time_data[client_ip][quiz_name]
                for question, time_taken in quiz_times.items():
                    results.append({'question': question, 'time': time_taken})
        except Exception as e:
            logger.error(f"Error reading time.json: {e}")

    return results

def migrate_time_data_if_needed():
    """Migrate data from time.txt to time.json if time.json doesn't exist"""
    time_txt = os.path.join(os.path.dirname(__file__), "data", "time.txt")
    time_json = os.path.join(os.path.dirname(__file__), "data", "time.json")

    if os.path.exists(time_json):
        return  # Already migrated

    if not os.path.exists(time_txt):
        # Create empty time.json
        with open(time_json, "w", encoding="utf-8") as f:
            json.dump({}, f, indent=2)
        return

    # Parse time.txt and convert to JSON
    time_data = {}
    current_ip = None
    current_quiz = None

    try:
        with open(time_txt, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line.startswith("    "):
                    # IP or quiz name line
                    if re.match(r'\d+\.\d+\.\d+\.\d+', line):
                        current_ip = line
                        if current_ip not in time_data:
                            time_data[current_ip] = {}
                    else:
                        current_quiz = line
                        if current_ip and current_quiz not in time_data[current_ip]:
                            time_data[current_ip][current_quiz] = {}
                else:
                    # Question line
                    if current_ip and current_quiz:
                        q_line = line.strip()
                        parts = q_line.rsplit(' ', 1)
                        if len(parts) == 2:
                            question, time_taken = parts
                            time_data[current_ip][current_quiz][question] = time_taken

        # Write to JSON with pretty printing
        with open(time_json, "w", encoding="utf-8") as f:
            json.dump(time_data, f, indent=2, ensure_ascii=False)

        logger.info("Migrated time.txt to time.json")

    except Exception as e:
        logger.error(f"Error migrating time data: {e}")
        # Create empty JSON file
        with open(time_json, "w", encoding="utf-8") as f:
            json.dump({}, f, indent=2)

@app.route('/quiz/<quiz_name>/save_time', methods=['POST', 'GET'])
def save_time(quiz_name):
    if request.method == 'GET':
        return render_template("error.html", message="Error: Use POST"), 405
    if not quiz_name or quiz_name.strip() == "":
        logger.error("Quiz name missing in save_time URL")
        return jsonify({'error': 'Quiz name is required in the URL.'}), 400
    client_ip = get_client_ip()
    if request.is_json:
        time_taken = request.json.get('time_elapsed', '0')
    else:
        time_taken = request.form.get('time_elapsed', '0')
    question_name = flask_session.get('current_question_name', "Unknown Question")
    logger.info(f"Time saved for {client_ip} on quiz '{quiz_name}': {question_name} - {time_taken} sec")

    # Ensure time.json exists and migrate if needed
    migrate_time_data_if_needed()

    time_file = os.path.join(os.path.dirname(__file__), "data", "time.json")

    # Read existing JSON data
    time_data = {}
    if os.path.exists(time_file):
        try:
            with open(time_file, "r", encoding="utf-8") as f:
                time_data = json.load(f)
        except Exception as e:
            logger.error(f"Error reading time.json: {e}")
            time_data = {}

    # Update the data structure
    if client_ip not in time_data:
        time_data[client_ip] = {}
    if quiz_name not in time_data[client_ip]:
        time_data[client_ip][quiz_name] = {}
    time_data[client_ip][quiz_name][question_name] = time_taken

    # Write back to JSON with pretty printing
    try:
        with open(time_file, "w", encoding="utf-8") as f:
            json.dump(time_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error writing time.json: {e}")
        return jsonify({'error': 'Failed to save time data.'}), 500

    return jsonify({'status': 'ok', 'quiz_name': quiz_name})

@app.route('/quiz/<quiz_name>/finish')
def finish_quiz(quiz_name):
    client_ip = get_client_ip()
    logger.info(f"{client_ip} finished quiz '{quiz_name}'")
    results = get_quiz_times_for_user(quiz_name, client_ip)
    return render_template("finish.html", quiz_name=quiz_name, results=results)

@app.route('/quiz/<quiz_name>/reset')
def reset_quiz(quiz_name):
    client_ip = get_client_ip()
    logger.info(f"{client_ip} reset quiz '{quiz_name}'")
    question_file = os.path.join(os.path.dirname(__file__), "non_static", "question", f"{quiz_name}.json")
    try:
        with open(question_file, "r", encoding="utf-8") as f:
            question_data = json.load(f)
    except Exception as e:
        question_data = {}
    if not isinstance(question_data, dict):
        question_data = {}
    # Reset the current index to 0 for this client.
    question_data[client_ip] = 0
    # Write the updated question data back to the file.
    try:
        with open(question_file, "w", encoding="utf-8") as f:
            json.dump(question_data, f)
    except Exception as e:
        logger.error(f"Failed to reset quiz '{quiz_name}' for {client_ip}: {str(e)}")
        return jsonify({'error': 'Failed to reset question index.'}), 500
    return redirect(url_for("get_quiz_json", quiz_name=quiz_name))

@app.route('/view_times')
def view_times():
    client_ip = get_client_ip()
    logger.info(f"{client_ip} accessed view times page")
    time_file = os.path.join(os.path.dirname(__file__), "data", "time.json")
    return send_file(time_file, mimetype="application/json")

@app.route('/view_times_file')
def view_times_file():
    time_file = os.path.join(os.path.dirname(__file__), "data", "time.json")
    return send_file(time_file, mimetype="application/json")

{
  "INAPPROPRIATE_WORDS": [
    "badword1",
    "badword2",
    "inappropriate",
    "poop",
    "pee",
    "stupid",
    "dumb",
    "idiot",
    "ugly",
    "hate",
    "loser",
    "fool",
    "nonsense",
    "trash",
    "garbage",
    "moron",
    "jerk",
    "creep",
    "weirdo",
    "lame",
    "gross",
    "pathetic",
    "annoying",
    "lazy",
    "worthless"
  ]
}  # Add more words as needed

def create_host_session(quiz_name):
    """Creates a hosted game session; returns (code, control_code)."""
    import random
    code = str(random.randint(1000, 9999))  # 4-digit code
    while code in sessions:
        code = str(random.randint(1000, 9999))
    control_code = str(random.randint(1000, 9999))
    while control_code in sessions or control_code == code:
        control_code = str(random.randint(1000, 9999))
    # Load questions and attach per-question images if present
    questions = read_quiz(quiz_name)
    images_meta_file = os.path.join(os.path.dirname(__file__), "non_static", "question", f"{quiz_name}_images.json")
    try:
        if os.path.exists(images_meta_file):
            with open(images_meta_file, 'r', encoding='utf-8') as mf:
                images_meta = json.load(mf)
        else:
            images_meta = {}
    except Exception:
        images_meta = {}
    for idx, q in enumerate(questions):
        imgs = images_meta.get(str(idx), [])
        if imgs:
            # Percent-encode the quiz name/filenames so clients that send raw
            # HTTP request lines (the 3DS app) don't break on spaces.
            from urllib.parse import quote
            q['images'] = [f"/UploadedImages/{quote(quiz_name)}/{quote(fname)}" for fname in imgs]

    sessions[code] = {'host_sid': None, 'control_sids': [], 'players': [], 'quiz_name': quiz_name, 'questions': questions, 'current_q': 0, 'scores': {}, 'answers': [], 'last_correct': {}, 'state': 'lobby', 'control_code': control_code}
    control_to_game[control_code] = code

    # Increment play count
    plays = load_plays()
    plays[quiz_name] = plays.get(quiz_name, 0) + 1
    save_plays(plays)
    return code, control_code

@app.route('/host/<quiz_name>')
def host_quiz(quiz_name):
    client_ip = get_client_ip()
    logger.info(f"{client_ip} is hosting quiz '{quiz_name}'")
    code, control_code = create_host_session(quiz_name)
    return render_template('host.html', code=code, quiz_name=quiz_name, control_code=control_code, is_mobile=is_mobile())

@app.route('/api/host/<quiz_name>', methods=['GET'])
def api_host_quiz(quiz_name):
    """JSON hosting for non-browser clients (the 3DS): creates the session and
    returns the game and control codes."""
    try:
        code, control_code = create_host_session(quiz_name)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'code': code, 'control_code': control_code})

@app.route('/control/<code>')
def control_quiz(code):
    if code in control_to_game:
        game_code = control_to_game[code]
        return render_template('control.html', code=game_code, quiz_name=sessions[game_code]['quiz_name'], is_mobile=is_mobile())
    else:
        return "Invalid control code", 404

@app.route('/resolve_control/<code>')
def resolve_control(code):
    # JSON variant for non-browser clients (e.g. the 3DS app): resolves a
    # control code to the game code it belongs to.
    if code in control_to_game:
        return jsonify({'game_code': control_to_game[code]})
    return jsonify({'error': 'Invalid control code'}), 404

@app.route('/join')
def join_game():
    client_ip = get_client_ip()
    logger.info(f"{client_ip} accessed join game page")
    return render_template('join.html', is_mobile=is_mobile())

@socketio.on('connect')
def handle_connect():
    if ip_to_sid is None or active_clients is None:
        return
    ip = get_client_ip()
    sid = request.sid
    ip_to_sid[ip] = sid
    if ip in active_clients:
        active_clients[ip]['sid'] = sid

@socketio.on('disconnect')
def handle_disconnect():
    if active_clients is None or ip_to_sid is None:
        return
    sid = request.sid
    # Remove from active_clients
    for ip, data in list(active_clients.items()):
        if data.get('sid') == sid:
            del active_clients[ip]
            if ip in ip_to_sid and ip_to_sid[ip] == sid:
                del ip_to_sid[ip]
            break
    # Remove player from all sessions
    for code in list(sessions.keys()):
        # Find and remove the player
        for player in sessions[code]['players'][:]:  # Create a copy to iterate
            if player['sid'] == request.sid:
                player_name = player['name']
                sessions[code]['players'].remove(player)
                
                # Do not remove from scores, last_correct, or answers
                
                logger.info(f"Player '{player_name}' disconnected from game {code}")
                
                # Notify all players in the room that this player left using socketio.emit
                socketio.emit('player_left', {'name': player_name}, room=code)
                
                # Update and broadcast the current leaderboard to all remaining players/host
                if sessions[code]['state'] == 'leaderboard' and sessions[code]['scores']:
                    leaderboard = []
                    for name, score in sorted(sessions[code]['scores'].items(), key=lambda x: x[1], reverse=True):
                        last_correct = sessions[code]['last_correct'].get(name)
                        left = name not in [p['name'] for p in sessions[code]['players']]
                        avatar = ''
                        if not left:
                            for player in sessions[code]['players']:
                                if player['name'] == name:
                                    avatar = player.get('avatar', '')
                                    break
                        leaderboard.append({'name': name, 'score': score, 'last_correct': last_correct, 'avatar': avatar, 'left': left})
                    submitted_answers = {ans['name']: ans['answer_text'] for ans in sessions[code]['answers']}
                    q_index = sessions[code]['current_q'] - 1
                    q = sessions[code]['questions'][q_index] if q_index >= 0 and q_index < len(sessions[code]['questions']) else None
                    correct_text = correct_answer_text(q)
                    socketio.emit('leaderboard', {'leaderboard': leaderboard, 'correct_answer': correct_text, 'submitted_answers': submitted_answers}, room=code)
                
                break

@socketio.on('join_game')
def handle_join_game(data):
    code = data['code']
    player_name = data.get('name', 'Anonymous')
    avatar = data.get('avatar', '')
    client_ip = get_client_ip()
    
    if code in sessions:
        existing_names = [p['name'] for p in sessions[code]['players']]
        if player_name in existing_names:
            logger.warning(f"{client_ip} failed to join game \x1b[95m{code}\x1b[0m with name \x1b[94m{player_name}\x1b[0m: Username unavailable")
            emit('error', {'message': 'Username unavailable. Please choose a different one.'})
            return
        # Successful join
        logger.info(f"{client_ip} successfully joined game \x1b[95m{code}\x1b[0m with name \x1b[94m{player_name}\x1b[0m")
        join_room(code)
        if player_name in sessions[code]['scores']:
            # Rejoining
            sessions[code]['players'].append({'sid': request.sid, 'name': player_name, 'avatar': avatar})
            # Keep existing score, reset last_correct for new round
            sessions[code]['last_correct'][player_name] = None
        else:
            # New player
            sessions[code]['players'].append({'sid': request.sid, 'name': player_name, 'avatar': avatar})
            sessions[code]['scores'][player_name] = 0
            sessions[code]['last_correct'][player_name] = None
        emit('player_joined', {'name': player_name, 'avatar': avatar}, room=code, skip_sid=request.sid)
        emit('joined', {'code': code, 'quiz_name': sessions[code]['quiz_name']})
        # Send current state to late joiner
        state = sessions[code]['state']
        if state == 'question':
            q_index = sessions[code]['current_q'] - 1
            if q_index >= 0:
                q_obj = sessions[code]['questions'][q_index]
                emit('new_question', question_payload(q_obj))
        elif state == 'leaderboard':
            leaderboard = []
            for name, score in sorted(sessions[code]['scores'].items(), key=lambda x: x[1], reverse=True):
                last_correct = sessions[code]['last_correct'].get(name)
                left = name not in existing_names + [player_name]  # existing_names doesn't include the new player yet, but since we added, use current players
                avatar_for_name = ''
                if not left:
                    for player in sessions[code]['players']:
                        if player['name'] == name:
                            avatar_for_name = player.get('avatar', '')
                            break
                leaderboard.append({'name': name, 'score': score, 'last_correct': last_correct, 'avatar': avatar_for_name, 'left': left})
            submitted_answers = {ans['name']: ans['answer_text'] for ans in sessions[code]['answers']} if sessions[code]['answers'] else {}
            q_index = sessions[code]['current_q'] - 1
            q = sessions[code]['questions'][q_index] if q_index >= 0 and q_index < len(sessions[code]['questions']) else None
            emit('leaderboard', {'leaderboard': leaderboard, 'correct_answer': correct_answer_text(q), 'submitted_answers': submitted_answers})
        elif state == 'finished':
            leaderboard = []
            for name, score in sorted(sessions[code]['scores'].items(), key=lambda x: x[1], reverse=True):
                left = name not in [p['name'] for p in sessions[code]['players']]
                avatar_for_name = ''
                if not left:
                    for player in sessions[code]['players']:
                        if player['name'] == name:
                            avatar_for_name = player.get('avatar', '')
                            break
                leaderboard.append({'name': name, 'score': score, 'avatar': avatar_for_name, 'left': left})
            emit('final_leaderboard', {'leaderboard': leaderboard})
    else:
        logger.warning(f"{client_ip} failed to join game \x1b[95m{code}\x1b[0m with name \x1b[94m{player_name}\x1b[0m: Invalid game code")
        emit('error', {'message': 'Invalid code'})

@socketio.on('host_join')
def handle_host_join(data):
    if not _socket_has_account():
        emit('error', {'message': 'Login required to host a game.'})
        return
    code = data['code']
    if code in sessions:
        sessions[code]['host_sid'] = request.sid
        join_room(code)

@socketio.on('control_join')
def handle_control_join(data):
    if not _socket_has_account():
        emit('error', {'message': 'Login required to control a game.'})
        return
    code = data['code']
    if code in sessions:
        join_room(code)
        sessions[code]['control_sids'].append(request.sid)
        # Sync current state
        state = sessions[code]['state']
        if state == 'question':
            q_index = sessions[code]['current_q'] - 1
            if q_index >= 0 and q_index < len(sessions[code]['questions']):
                emit('new_question', question_payload(sessions[code]['questions'][q_index]))
        elif state == 'leaderboard':
            leaderboard_items = []
            for name, score in sorted(sessions[code]['scores'].items(), key=lambda x: x[1], reverse=True):
                left = name not in [p['name'] for p in sessions[code]['players']]
                avatar = ''
                if not left:
                    for player in sessions[code]['players']:
                        if player['name'] == name:
                            avatar = player.get('avatar', '')
                            break
                leaderboard_items.append({'name': name, 'score': score, 'last_correct': sessions[code]['last_correct'].get(name), 'avatar': avatar, 'left': left})
            emit('leaderboard', {'leaderboard': leaderboard_items})
        elif state == 'finished':
            leaderboard = []
            for name, score in sorted(sessions[code]['scores'].items(), key=lambda x: x[1], reverse=True):
                left = name not in [p['name'] for p in sessions[code]['players']]
                avatar = ''
                if not left:
                    for player in sessions[code]['players']:
                        if player['name'] == name:
                            avatar = player.get('avatar', '')
                            break
                leaderboard.append({'name': name, 'score': score, 'avatar': avatar, 'left': left})
            emit('final_leaderboard', {'leaderboard': leaderboard})

@socketio.on('start_round')
def handle_start_round(data):
    code = data['code']
    if code in sessions and (sessions[code]['host_sid'] == request.sid or request.sid in sessions[code]['control_sids']):
        q_index = sessions[code]['current_q']
        if q_index < len(sessions[code]['questions']):
            q_obj = sessions[code]['questions'][q_index]
            sessions[code]['answers'] = []
            sessions[code]['state'] = 'question'
            emit('new_question', question_payload(q_obj), room=code)
            sessions[code]['current_q'] += 1
        else:
            sessions[code]['state'] = 'finished'
            leaderboard = [{'name': name, 'score': score} for name, score in sorted(sessions[code]['scores'].items(), key=lambda x: x[1], reverse=True)]
            emit('final_leaderboard', {'leaderboard': leaderboard}, room=code)

@socketio.on('submit_answer')
def handle_submit_answer(data):
    code = data['code']
    answer = data['answer']
    time_taken = data.get('time_taken', 0)
    if code in sessions:
        q_index = sessions[code]['current_q'] - 1
        q = sessions[code]['questions'][q_index] if q_index >= 0 and q_index < len(sessions[code]['questions']) else None
        # Convert answer to text for display
        answer_text = answer
        if q and q['type'] == 'mc' and isinstance(answer, int):
            answer_text = q['answers'][answer] if answer < len(q['answers']) else str(answer)
        elif q and q['type'] == 'puzzle' and isinstance(answer, list):
            answer_text = ' → '.join(str(a) for a in answer)
        # Find player
        for player in sessions[code]['players']:
            if player['sid'] == request.sid:
                sessions[code]['answers'].append({'name': player['name'], 'answer': answer, 'answer_text': answer_text, 'time_taken': time_taken})
                emit('answer_received', {'name': player['name'], 'answer': answer_text}, room=code, skip_sid=request.sid)
                # Check if all active players have submitted
                active_player_names = [p['name'] for p in sessions[code]['players']]
                active_answers = [ans for ans in sessions[code]['answers'] if ans['name'] in active_player_names]
                if len(active_answers) == len(sessions[code]['players']):
                    # Auto-reveal answers
                    for ans in sessions[code]['answers']:
                        name = ans['name']
                        time_taken = ans['time_taken']
                        correct = validate_answer(q, ans['answer']) if q else False
                        sessions[code]['last_correct'][name] = correct
                        if correct:
                            points = max(0, round(1000 - time_taken * 50))
                            sessions[code]['scores'][name] += points
                        # Emit to specific player
                        correct_text = correct_answer_text(q)
                        for player in sessions[code]['players']:
                            if player['name'] == ans['name']:
                                emit('answer_revealed', {'correct': correct, 'correct_answer': correct_text}, room=player['sid'])
                                break
                    # Emit leaderboard
                        # Emit leaderboard
                        leaderboard = []
                        for name, score in sorted(sessions[code]['scores'].items(), key=lambda x: x[1], reverse=True):
                            last_correct = sessions[code]['last_correct'].get(name)
                            avatar = ''
                            for player in sessions[code]['players']:
                                if player['name'] == name:
                                    avatar = player.get('avatar', '')
                                    break
                            leaderboard.append({'name': name, 'score': score, 'last_correct': last_correct, 'avatar': avatar})
                        submitted_answers = {ans['name']: ans['answer_text'] for ans in sessions[code]['answers']}
                        emit('answers_revealed', {'correct_answer': correct_text}, room=code)
                        sessions[code]['state'] = 'leaderboard'
                        emit('leaderboard', {'leaderboard': leaderboard, 'correct_answer': correct_text, 'submitted_answers': submitted_answers}, room=code)
                break

@socketio.on('reveal_answers')
def handle_reveal_answers(data):
    code = data['code']
    if code in sessions and (sessions[code]['host_sid'] == request.sid or request.sid in sessions[code]['control_sids']):
        q_index = sessions[code]['current_q'] - 1
        q = sessions[code]['questions'][q_index] if q_index >= 0 and q_index < len(sessions[code]['questions']) else None
        correct_text = correct_answer_text(q)
        for ans in sessions[code]['answers']:
            name = ans['name']
            time_taken = ans['time_taken']
            correct = validate_answer(q, ans['answer']) if q else False
            sessions[code]['last_correct'][name] = correct
            if correct:
                points = max(0, round(1000 - time_taken * 50))  # 50 points per second deducted
                sessions[code]['scores'][name] += points
            # Emit to specific player
            for player in sessions[code]['players']:
                if player['name'] == ans['name']:
                    emit('answer_revealed', {'correct': correct, 'correct_answer': correct_text}, room=player['sid'])
                    break
        # Emit leaderboard
        leaderboard = []
        for name, score in sorted(sessions[code]['scores'].items(), key=lambda x: x[1], reverse=True):
            last_correct = sessions[code]['last_correct'].get(name)
            left = name not in [p['name'] for p in sessions[code]['players']]
            avatar = ''
            if not left:
                for player in sessions[code]['players']:
                    if player['name'] == name:
                        avatar = player.get('avatar', '')
                        break
            leaderboard.append({'name': name, 'score': score, 'last_correct': last_correct, 'avatar': avatar, 'left': left})
        submitted_answers = {ans['name']: ans['answer_text'] for ans in sessions[code]['answers']}
        emit('answers_revealed', {'correct_answer': correct_text}, room=code)
        sessions[code]['state'] = 'leaderboard'
        emit('leaderboard', {'leaderboard': leaderboard, 'correct_answer': correct_text, 'submitted_answers': submitted_answers}, room=code)

@socketio.on('kick_player')
def handle_kick_player(data):
    code = data['code']
    name = data.get('name', '')
    if code in sessions and (sessions[code]['host_sid'] == request.sid or request.sid in sessions[code]['control_sids']):
        s = sessions[code]
        for player in list(s['players']):
            if player['name'] == name:
                emit('kicked', {'message': 'You were kicked from the game'}, room=player['sid'])
                s['players'].remove(player)
                socketio.emit('player_left', {'name': name}, room=code)
                break

def process_actions():
    if actions_queue is None:
        return
    while True:
        try:
            action = actions_queue.get(timeout=1)
            logger.info(f"Processing GUI action: {action}")
            if action['action'] == 'ban':
                ip = action['ip']
                banned_ips[ip] = 'Banned from GUI'
                save_banned_ips(banned_ips)
                sid = active_clients.get(ip, {}).get('sid')
                if sid:
                    socketio.disconnect(sid)
                    logger.info(f"Banned IP {ip} from GUI")
                else:
                    logger.warning(f"No SID found for IP {ip} to ban")
            elif action['action'] == 'send_popup':
                ip = action['ip']
                message = action['message']
                sid = active_clients.get(ip, {}).get('sid')
                if sid:
                    socketio.emit('show_popup', {'message': message}, to=sid)
                    logger.info(f"Sent popup to IP {ip}: {message}")
                else:
                    logger.warning(f"No SID found for IP {ip} to send popup")
            # clean old
            now = time.time()
            for ip in list(active_clients.keys()):
                if now - active_clients[ip]['last_seen'] > 300:
                    del active_clients[ip]
        except queue.Empty:
            pass

def run_gui(shared_clients, shared_queue):
    if not GUI_AVAILABLE:
        return
    root = tk.Tk()
    root.title("IP Monitor")
    listbox = tk.Listbox(root, width=50)
    listbox.pack(fill=tk.BOTH, expand=True)
    button_frame = tk.Frame(root)
    button_frame.pack()
    info_button = tk.Button(button_frame, text="Info", command=lambda: info_callback(listbox.get(tk.ACTIVE), shared_clients))
    ban_button = tk.Button(button_frame, text="Ban", command=lambda: ban_callback(listbox.get(tk.ACTIVE), shared_queue))
    send_button = tk.Button(button_frame, text="Send Message", command=lambda: send_popup_callback(listbox.get(tk.ACTIVE), shared_queue))
    info_button.pack(side=tk.LEFT)
    ban_button.pack(side=tk.LEFT)
    send_button.pack(side=tk.LEFT)

    def update_list():
        listbox.delete(0, tk.END)
        now = time.time()
        active = {ip: data for ip, data in shared_clients.items() if now - data['last_seen'] < 300}
        for ip, data in active.items():
            listbox.insert(tk.END, f"{ip} - {data['page']} - {time.ctime(data['last_seen'])}")
        root.after(1000, update_list)

    update_list()
    root.mainloop()

def info_callback(ip_str, shared_clients):
    if not ip_str:
        return
    ip = ip_str.split(' - ')[0]
    data = shared_clients.get(ip, {})
    messagebox.showinfo("Info", f"IP: {ip}\nPage: {data.get('page', 'Unknown')}\nLast Seen: {time.ctime(data.get('last_seen', 0))}")

def ban_callback(ip_str, shared_queue):
    if not ip_str:
        return
    ip = ip_str.split(' - ')[0]
    shared_queue.put({'action': 'ban', 'ip': ip})

def send_popup_callback(ip_str, shared_queue):
    if not ip_str:
        return
    ip = ip_str.split(' - ')[0]
    msg = simpledialog.askstring("Send Message", "Enter message:")
    if msg:
        shared_queue.put({'action': 'send_popup', 'ip': ip, 'message': msg})

def start_gui():
    global gui_process
    if GUI_AVAILABLE and active_clients is not None:
        gui_process = multiprocessing.Process(target=run_gui, args=(active_clients, actions_queue))
        gui_process.start()

threading.Thread(target=process_actions, daemon=True).start()
# GUI started by main process

@app.errorhandler(404)
def page_not_found(e):
    """Handle 404 errors by showing the spinning 404 page"""
    return render_template('404.html'), 404

if __name__ == '__main__':
    multiprocessing.freeze_support()
    # Set up shared data for GUI - but since GUI is separate, use regular dicts
    active_clients = {}
    ip_to_sid = {}
    actions_queue = queue.Queue()

    keyfile = os.path.join(CERT_DIR, "privkey.pem")
    certfile = os.path.join(CERT_DIR, "fullchain.pem")

    if os.path.isfile(keyfile) and os.path.isfile(certfile):
        # One process serving the SAME app on two ports: plain HTTP on 5710
        # (the port the router maps external 80 to - used by the 3DS) and
        # HTTPS on 443 for browsers. Shared app object = shared live games.
        from socketio import WSGIApp as socketio_wsgi_app
        from gevent import pywsgi
        from geventwebsocket.handler import WebSocketHandler
        sio_app = socketio_wsgi_app(socketio.server, app)
        tls_server = pywsgi.WSGIServer(('0.0.0.0', 443), sio_app,
                                       keyfile=keyfile, certfile=certfile,
                                       handler_class=WebSocketHandler, log=None)
        plain_server = pywsgi.WSGIServer(('0.0.0.0', 5710), sio_app,
                                         handler_class=WebSocketHandler, log=None)
        plain_server.start()
        logger.info("Serving plain HTTP on 5710 (router external port 80) and HTTPS on 443 - shared game state")
        logger.info(f"HTTPS is live at https://{HTTPS_DOMAIN}")
        try:
            tls_server.serve_forever()
        finally:
            plain_server.stop()
    else:
        # No certificate yet: plain HTTP on 5710 as before. The ACME
        # challenge route is active so certbot can issue the first
        # certificate through the router's external port 80 -> 5710.
        logger.info("Starting QuizFabric development server on port 5710...")
        logger.info("No SSL certificate found - plain HTTP only for now")
        # Production: Use debug=False and recommend gunicorn with socketio
        try:
            socketio.run(app, debug=False, host="0.0.0.0", port=5710)
        except Exception as e:
            logger.error(f"Server error: {e}")
        finally:
            logger.info("QuizFabric server stopped.")
