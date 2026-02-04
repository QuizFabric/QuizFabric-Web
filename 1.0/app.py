import os
import json
import random
import datetime
import re
import sys
import threading
import logging
import time
import queue
import multiprocessing

from flask import Flask, render_template, jsonify, request, session, redirect, url_for, send_file

from flask_socketio import SocketIO, emit, join_room, leave_room

try:
    import tkinter as tk
    from tkinter import messagebox, simpledialog
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False

from non_static.quiz import read_quiz, get_random_question, validate_answer  # adjust imports as needed

from non_static.utils import read_uploaded_json, write_uploaded_json

try:
    from colorama import init, Fore, Back, Style
    init()
    COLORAMA_AVAILABLE = True
except ImportError:
    COLORAMA_AVAILABLE = False

app = Flask(__name__, template_folder="templates")
app.secret_key = "your_random_secret_key_here"

socketio = SocketIO(app, async_mode='gevent')

# Globals for IP monitoring - will be set by main process
active_clients = None
ip_to_sid = None
actions_queue = None

# Globals for IP monitoring

from werkzeug.utils import secure_filename

# Configuration for file uploads
ALLOWED_EXTENSIONS = {'.png', '.webp', '.jpg', '.jpeg'}
MAX_IMAGES_PER_QUIZ = 20
from werkzeug.utils import secure_filename

# Configuration for file uploads
ALLOWED_EXTENSIONS = {'.png', '.webp', '.jpg', '.jpeg'}
MAX_FILE_SIZE = 300 * 1024 * 1024  # 300MB
MAX_IMAGES_PER_QUIZ = 20
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "UploadedImages")

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
    forwarded_for = request.headers.get('X-Forwarded-For')
    return forwarded_for.split(",")[0].strip() if forwarded_for else request.remote_addr

def is_mobile():
    user_agent = request.headers.get('User-Agent', '').lower()
    mobile_keywords = ['mobile', 'android', 'iphone', 'ipad', 'ipod', 'blackberry', 'windows phone']
    return any(keyword in user_agent for keyword in mobile_keywords)

@app.before_request
def before_request():
    log_connection()
    track_page()

def log_connection():
    ip = get_client_ip()
    # Optional: Add connection logging here

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
    logger.info(f"{client_ip} accessed home page")
    try:
        quiz_dir = os.path.join(os.path.dirname(__file__), "non_static", "quiz")
        quizzes = []
        if os.path.exists(quiz_dir):
            for file in os.listdir(quiz_dir):
                if file.endswith(".txt"):
                    quiz_name = os.path.splitext(file)[0]
                    quizzes.append(quiz_name)
        # Load uploaded quizzes for current IP
        uploaded_file = os.path.join(os.path.dirname(__file__), "data", "uploaded.json")
        user_uploaded = []
        ip = get_client_ip()
        if os.path.exists(uploaded_file):
            with open(uploaded_file, "r", encoding="utf-8") as f:
                uploaded_data = json.load(f)
            user_uploaded = uploaded_data.get(ip, [])
        # Special case: 127.0.0.1 has full access to all quizzes
        if ip == "127.0.0.1":
            user_uploaded = quizzes
        
        # Load plays
        plays = load_plays()
        for q in quizzes:
            if q not in plays:
                plays[q] = 0
        save_plays(plays)
        
        # Sort quizzes by plays descending
        quizzes_sorted = sorted(quizzes, key=lambda x: plays.get(x, 0), reverse=True)
        
        return render_template('home.html', quizzes=quizzes_sorted, user_uploaded=user_uploaded, plays=plays), 200
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
    icon_file = os.path.join(os.path.dirname(__file__), "data", "icon.png")
    if os.path.exists(icon_file):
        return send_file(icon_file, mimetype='image/png')
    else:
        return "Icon not found", 404

@app.route('/deletequiz', methods=['GET', 'POST'])
def deletequiz():
    ip = get_client_ip()
    if request.method == "GET":
        logger.info(f"{ip} accessed delete quiz page")
        # Expect the quiz name as a query parameter: ?quiz=quizname
        quiz = request.args.get("quiz")
        if not quiz:
            return redirect(url_for("home"))
        # Check if the quiz exists in the uploaded quizzes for this IP.
        return render_template("deletequiz_confirm.html", quiz=quiz)

    # POST: actually delete the quiz.
    quiz_to_delete = request.form.get("quiz")
    uploaded_file = os.path.join(os.path.dirname(__file__), "data", "uploaded.json")
    if os.path.exists(uploaded_file):
        with open(uploaded_file, "r", encoding="utf-8") as f:
            uploaded_data = json.load(f)
    else:
        uploaded_data = {}
    user_uploaded = uploaded_data.get(ip, [])

    # Check if the quiz exists in the uploaded quizzes for this IP.
    if quiz_to_delete in user_uploaded:
        logger.info(f"User {ip} deleting quiz: {quiz_to_delete}")
        quiz_dir = os.path.join(os.path.dirname(__file__), "non_static", "quiz")
        quiz_file = os.path.join(quiz_dir, quiz_to_delete + ".txt")
        if os.path.exists(quiz_file):
            os.remove(quiz_file)
        user_uploaded.remove(quiz_to_delete)
        uploaded_data[ip] = user_uploaded
        with open(uploaded_file, "w", encoding="utf-8") as f:
            json.dump(uploaded_data, f)

    # Redirect to home page after deletion.
    return redirect(url_for("home"))

@app.route('/addquiz', methods=['GET', 'POST'])
def addquiz():
    client_ip = get_client_ip()
    if request.method == 'GET':
        logger.info(f"{client_ip} accessed add quiz page")
    if request.method == 'POST':
        uploaded_file = request.files.get("quiz_file")
        if uploaded_file:
            content = uploaded_file.read().decode("utf-8")
            # Validate and save the file
            quiz_dir = os.path.join(os.path.dirname(__file__), "non_static", "quiz")
            if not os.path.exists(quiz_dir):
                os.makedirs(quiz_dir)
            file_path = os.path.join(quiz_dir, uploaded_file.filename)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            
            # Update uploaded.json
            uploaded_data = read_uploaded_json()
            if client_ip not in uploaded_data:
                uploaded_data[client_ip] = []
            if uploaded_file.filename not in uploaded_data[client_ip]:
                uploaded_data[client_ip].append(uploaded_file.filename)
            write_uploaded_json(uploaded_data)
            
            # Initialize play count for uploaded quiz
            quiz_name = os.path.splitext(uploaded_file.filename)[0]  # Remove .txt extension
            plays = load_plays()
            if quiz_name not in plays:
                plays[quiz_name] = 0
                save_plays(plays)
            
            return render_template("addquiz.html", message="Quiz uploaded successfully!")
    return render_template("addquiz.html")

@app.route('/upload_quiz_image/<quiz_name>', methods=['POST'])
def upload_quiz_image(quiz_name):
    """Upload image for a quiz with validation"""
    client_ip = get_client_ip()

    try:
        # Check if file is in request
        if 'image' not in request.files:
            return jsonify({'error': 'No image file provided'}), 400

        image_file = request.files['image']

        if image_file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        # Get file extension
        filename = secure_filename(image_file.filename)
        file_ext = os.path.splitext(filename)[1].lower()

        # Validate file extension
        if file_ext not in ALLOWED_EXTENSIONS:
            return jsonify({'error': f'File type not allowed. Only {", ".join(ALLOWED_EXTENSIONS)} are supported'}), 400

        # Check file size before saving
        image_file.seek(0, os.SEEK_END)
        file_size = image_file.tell()
        image_file.seek(0)

        if file_size > MAX_FILE_SIZE:
            return jsonify({'error': f'File too large. Maximum size is 300MB'}), 400

        # Create quiz-specific directory
        quiz_image_dir = os.path.join(UPLOAD_FOLDER, secure_filename(quiz_name))
        if not os.path.exists(quiz_image_dir):
            os.makedirs(quiz_image_dir)

        # Check image count
        existing_images = os.listdir(quiz_image_dir)
        if len(existing_images) >= MAX_IMAGES_PER_QUIZ:
            return jsonify({'error': f'Maximum {MAX_IMAGES_PER_QUIZ} images per quiz reached'}), 400

        # Generate unique filename
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        new_filename = f"{timestamp}{file_ext}"
        file_path = os.path.join(quiz_image_dir, new_filename)

        # Save file
        image_file.save(file_path)

        # Update per-question images metadata if question_index provided
        images_meta_dir = os.path.join(os.path.dirname(__file__), "non_static", "question")
        if not os.path.exists(images_meta_dir):
            os.makedirs(images_meta_dir)
        images_meta_file = os.path.join(images_meta_dir, f"{quiz_name}_images.json")

        question_index = request.form.get('question_index')
        try:
            if os.path.exists(images_meta_file):
                with open(images_meta_file, 'r', encoding='utf-8') as mf:
                    images_meta = json.load(mf)
            else:
                images_meta = {}
        except Exception:
            images_meta = {}

        if question_index is not None and question_index != '':
            key = str(int(question_index))
            images_meta.setdefault(key, [])
            images_meta[key].append(new_filename)
            try:
                with open(images_meta_file, 'w', encoding='utf-8') as mf:
                    json.dump(images_meta, mf)
            except Exception as e:
                logger.warning(f"Failed to update images metadata for {client_ip}: {e}")

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
        uploaded_names.setdefault(key if 'key' in locals() else '', [])
        uploaded_names[key].append({'filename': new_filename, 'name': display_name})
        try:
            with open(uploaded_names_file, 'w', encoding='utf-8') as uf:
                json.dump(uploaded_names, uf)
        except Exception as e:
            logger.warning(f"Failed to update image metadata for {client_ip}: {e}")

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


@app.route('/UploadedImages/<path:filepath>')
def serve_uploaded_image(filepath):
    """Serve uploaded images"""
    try:
        file_path = os.path.join(UPLOAD_FOLDER, filepath)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            return send_file(file_path)
        return jsonify({'error': 'Image not found'}), 404
    except Exception as e:
        return jsonify({'error': 'Failed to serve image'}), 500

@app.route('/quiz/<quiz_name>', methods=['GET'])
def get_quiz_json(quiz_name):
    # quiz_name is defined from the URL.
    session['quiz_name'] = quiz_name
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
    if current_index == 0 and not session.get('solo_started_' + quiz_name):
        plays = load_plays()
        plays[quiz_name] = plays.get(quiz_name, 0) + 1
        save_plays(plays)
        session['solo_started_' + quiz_name] = True
    
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
    session['current_question_name'] = question.get('question', 'Unknown Question')
    
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

@app.route('/editquiz/<quiz_name>', methods=['GET', 'POST'])
def edit_quiz(quiz_name):
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
        new_quiz_name = request.form.get("new_quiz_name", "").strip()
        use_legacy = request.form.get("use_legacy", "false") == "true"
        
        # Handle rename
        if new_quiz_name and new_quiz_name != quiz_name:
            new_quiz_file = os.path.join(os.path.dirname(__file__), "non_static", "quiz", f"{new_quiz_name}.txt")
            new_question_file = os.path.join(os.path.dirname(__file__), "non_static", "question", f"{new_quiz_name}.json")
            
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
                
                # Update uploaded.json if needed
                uploaded_data = read_uploaded_json()
                if client_ip in uploaded_data and quiz_name in uploaded_data[client_ip]:
                    uploaded_data[client_ip].remove(quiz_name)
                    if new_quiz_name not in uploaded_data[client_ip]:
                        uploaded_data[client_ip].append(new_quiz_name)
                    write_uploaded_json(uploaded_data)
                
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
    session['current_question_name'] = q.get('question', f'Question {current_index + 1}')
    
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
        filename = request.form.get("filename")
        quiz_content = request.form.get("quiz_content")
        # Check if it's JSON from modern editor
        try:
            questions = json.loads(quiz_content)
            # Convert to text format
            text_content = ""
            for q in questions:
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
        # Save the quiz
        quiz_dir = os.path.join(os.path.dirname(__file__), "non_static", "quiz")
        if not os.path.exists(quiz_dir):
            os.makedirs(quiz_dir)
        quiz_file = os.path.join(quiz_dir, f"{filename}.txt")
        with open(quiz_file, "w", encoding="utf-8") as f:
            f.write(quiz_content)
        
        # Update uploaded.json
        uploaded_data = read_uploaded_json()
        if client_ip not in uploaded_data:
            uploaded_data[client_ip] = []
        if filename not in uploaded_data[client_ip]:
            uploaded_data[client_ip].append(filename)
        write_uploaded_json(uploaded_data)
        
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
    question_name = session.get('current_question_name', "Unknown Question")
    
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
    question_name = session.get('current_question_name', "Unknown Question")
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

@app.route('/host/<quiz_name>')
def host_quiz(quiz_name):
    client_ip = get_client_ip()
    logger.info(f"{client_ip} is hosting quiz '{quiz_name}'")
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
            q['images'] = [f"/UploadedImages/{quiz_name}/{fname}" for fname in imgs]

    sessions[code] = {'host_sid': None, 'control_sids': [], 'players': [], 'quiz_name': quiz_name, 'questions': questions, 'current_q': 0, 'scores': {}, 'answers': [], 'last_correct': {}, 'state': 'lobby', 'control_code': control_code}
    control_to_game[control_code] = code
    
    # Increment play count
    plays = load_plays()
    plays[quiz_name] = plays.get(quiz_name, 0) + 1
    save_plays(plays)
    
    return render_template('host.html', code=code, quiz_name=quiz_name, control_code=control_code, is_mobile=is_mobile())

@app.route('/control/<code>')
def control_quiz(code):
    if code in control_to_game:
        game_code = control_to_game[code]
        return render_template('control.html', code=game_code, quiz_name=sessions[game_code]['quiz_name'], is_mobile=is_mobile())
    else:
        return "Invalid control code", 404

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
                    correct_answer_text = ''
                    if q:
                        if q['type'] == 'mc':
                            correct_answer_text = ', '.join(q['answers'][i] for i in q['correct_indices'])
                        else:
                            correct_answer_text = q['answer']
                    socketio.emit('leaderboard', {'leaderboard': leaderboard, 'correct_answer': correct_answer_text, 'submitted_answers': submitted_answers}, room=code)
                
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
                payload = {'question': q_obj.get('question')}
                if q_obj.get('type') == 'mc':
                    payload.update({'answers': q_obj.get('answers', []), 'type': 'mc'})
                else:
                    payload.update({'type': 'type'})
                if q_obj.get('images'):
                    payload['images'] = q_obj.get('images')
                emit('new_question', payload)
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
            correct_answer_text = ''
            q_index = sessions[code]['current_q'] - 1
            q = sessions[code]['questions'][q_index] if q_index >= 0 and q_index < len(sessions[code]['questions']) else None
            if q:
                if q['type'] == 'mc':
                    correct_answer_text = ', '.join(q['answers'][i] for i in q['correct_indices'])
                else:
                    correct_answer_text = q['answer']
            emit('leaderboard', {'leaderboard': leaderboard, 'correct_answer': correct_answer_text, 'submitted_answers': submitted_answers})
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
    code = data['code']
    if code in sessions:
        sessions[code]['host_sid'] = request.sid
        join_room(code)

@socketio.on('control_join')
def handle_control_join(data):
    code = data['code']
    if code in sessions:
        join_room(code)
        sessions[code]['control_sids'].append(request.sid)
        # Sync current state
        state = sessions[code]['state']
        if state == 'question':
            q_index = sessions[code]['current_q'] - 1
            if q_index >= 0 and q_index < len(sessions[code]['questions']):
                q_obj = sessions[code]['questions'][q_index]
                payload = {'question': q_obj.get('question')}
                if q_obj.get('type') == 'mc':
                    payload.update({'answers': q_obj.get('answers', []), 'type': 'mc'})
                else:
                    payload.update({'type': 'type'})
                if q_obj.get('images'):
                    payload['images'] = q_obj.get('images')
                emit('new_question', payload)
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
            payload = {'question': q_obj.get('question')}
            if q_obj.get('type') == 'mc':
                payload.update({'answers': q_obj.get('answers', []), 'type': 'mc'})
            else:
                payload.update({'type': 'type'})
            if q_obj.get('images'):
                payload['images'] = q_obj.get('images')
            emit('new_question', payload, room=code)
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
                        correct_answer_text = ''
                        if q:
                            if q['type'] == 'mc':
                                correct_answer_text = ', '.join(q['answers'][i] for i in q['correct_indices'])
                            else:
                                correct_answer_text = q['answer']
                        for player in sessions[code]['players']:
                            if player['name'] == ans['name']:
                                emit('answer_revealed', {'correct': correct, 'correct_answer': correct_answer_text}, room=player['sid'])
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
                        emit('answers_revealed', {'correct_answer': correct_answer_text}, room=code)
                        sessions[code]['state'] = 'leaderboard'
                        emit('leaderboard', {'leaderboard': leaderboard, 'correct_answer': correct_answer_text, 'submitted_answers': submitted_answers}, room=code)
                break

@socketio.on('reveal_answers')
def handle_reveal_answers(data):
    code = data['code']
    if code in sessions and (sessions[code]['host_sid'] == request.sid or request.sid in sessions[code]['control_sids']):
        q_index = sessions[code]['current_q'] - 1
        q = sessions[code]['questions'][q_index] if q_index >= 0 and q_index < len(sessions[code]['questions']) else None
        correct_answer_text = ''
        if q:
            if q['type'] == 'mc':
                correct_answer_text = ', '.join(q['answers'][i] for i in q['correct_indices'])
            else:
                correct_answer_text = q['answer']
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
                    emit('answer_revealed', {'correct': correct, 'correct_answer': correct_answer_text}, room=player['sid'])
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
        emit('answers_revealed', {'correct_answer': correct_answer_text}, room=code)
        sessions[code]['state'] = 'leaderboard'
        emit('leaderboard', {'leaderboard': leaderboard, 'correct_answer': correct_answer_text, 'submitted_answers': submitted_answers}, room=code)

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
    logger.info("Starting QuizFabric development server on port 5710...")
    # Production: Use debug=False and recommend gunicorn with socketio
    try:
        socketio.run(app, debug=False, host="0.0.0.0", port=5710)
    except Exception as e:
        logger.error(f"Server error: {e}")
    finally:
        logger.info("QuizFabric server stopped.")
