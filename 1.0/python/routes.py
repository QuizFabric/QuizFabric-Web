import os
import json
import random
import datetime
import re

from flask import Blueprint, render_template, jsonify, request, session, redirect, url_for
from non_static.quiz import read_quiz, get_random_question, validate_answer
#from non_static.utils import example_util_function, ExampleUtility  # if needed

routes = Blueprint('routes', __name__)

def get_client_ip():
    forwarded_for = request.headers.get('X-Forwarded-For')
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr

@routes.before_request
def log_connection():
    ip = get_client_ip()
    # Optional logging here

@routes.after_request
def log_disconnection(response):
    ip = get_client_ip()
    # Log disconnection if desired
    return response

@routes.route('/')
def home():
    try:
        quiz_dir = os.path.join(os.path.dirname(__file__), "../non_static/quiz")
        quizzes = []
        if os.path.exists(quiz_dir):
            for file in os.listdir(quiz_dir):
                if file.endswith(".txt"):
                    quiz_name = os.path.splitext(file)[0]
                    quizzes.append(quiz_name)
        # Load uploaded quizzes for current IP
        uploaded_file = os.path.join(os.path.dirname(__file__), "../data/uploaded.json")
        user_uploaded = []
        ip = get_client_ip()
        if os.path.exists(uploaded_file):
            with open(uploaded_file, "r", encoding="utf-8") as f:
                uploaded_data = json.load(f)
            user_uploaded = uploaded_data.get(ip, [])
        return render_template('home.html', quizzes=quizzes, user_uploaded=user_uploaded), 200
    except Exception as e:
        return f"Error in home route: {str(e)}", 500

# (Other quiz routes such as addquiz, quiz_index, show_question, quiz_validate, etc. remain as-is and are moved here)

@routes.route('/deletequiz', methods=['GET', 'POST'])
def deletequiz():
    ip = get_client_ip()
    # For GET, display a confirmation pop-up page.
    if request.method == "GET":
        # Expecting the quiz name in the query string: /deletequiz?quiz=quizname
        quiz = request.args.get("quiz")
        if not quiz:
            return redirect(url_for('routes.home'))
        return render_template("deletequiz_confirm.html", quiz=quiz)
    
    # For POST, actually delete the quiz.
    quiz_to_delete = request.form.get("quiz")
    uploaded_file = os.path.join(os.path.dirname(__file__), "../data/uploaded.json")
    if os.path.exists(uploaded_file):
        with open(uploaded_file, "r", encoding="utf-8") as f:
            uploaded_data = json.load(f)
    else:
        uploaded_data = {}
    user_uploaded = uploaded_data.get(ip, [])
    if quiz_to_delete in user_uploaded:
        # Remove the quiz file from the quiz directory
        quiz_dir = os.path.join(os.path.dirname(__file__), "../non_static/quiz")
        quiz_file = os.path.join(quiz_dir, quiz_to_delete + ".txt")
        if os.path.exists(quiz_file):
            os.remove(quiz_file)
        # Update the uploaded.json data
        user_uploaded.remove(quiz_to_delete)
        uploaded_data[ip] = user_uploaded
        with open(uploaded_file, "w", encoding="utf-8") as f:
            json.dump(uploaded_data, f)
        message = "Quiz deleted successfully."
    else:
        message = "You are not allowed to delete this quiz."
    return render_template("deletequiz_confirm.html", message=message, quiz=quiz_to_delete)

@routes.route('/addquiz', methods=['GET', 'POST'])
def addquiz():
    # For GET, simply display the "Add Quiz" (formerly Upload Quiz) page
    if request.method == 'GET':
        return render_template("addquiz.html")
    # For POST, add your quiz upload/creation logic below...
    # (This is just a placeholder)
    return render_template("addquiz.html", message="Quiz uploaded successfully!")

@routes.route('/<quiz_name>', methods=['GET'])
def quiz_index(quiz_name):
    session['quiz_name'] = quiz_name
    try:
        questions = read_quiz(quiz_name)
    except Exception as e:
        return f"Error reading quiz: {str(e)}", 500

    # Get the first question, or None if no questions exist
    question = questions[0] if questions else None
    return render_template("quiz.html", quiz_name=quiz_name, question=question)

@routes.route('/editquiz/<quiz_name>', methods=['GET', 'POST'])
def edit_quiz(quiz_name):
    if request.method == 'GET':
        # Render the edit quiz page with the current quiz name (and any other data needed)
        return render_template("editquiz.html", quiz_name=quiz_name)
    else:
        # Process the quiz edit submission here
        # (placeholder logic)
        return render_template("editquiz.html", quiz_name=quiz_name, message="Quiz updated successfully!")

@routes.route('/<quiz_name>/quiz/validate', methods=['POST'])
def quiz_validate(quiz_name):
    from flask import jsonify  
    # Get the answer from the client’s JSON payload
    answer = request.json.get('answer', '')
    # For this example, simply use the first question from the quiz file.
    try:
        questions = read_quiz(quiz_name)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    if not questions:
        return jsonify({'error': 'No questions found.'}), 404

    # Obtain the correct answer from the first question.
    correct_answer = questions[0].get('answer', '')
    is_correct = answer.strip().lower() == correct_answer.strip().lower()
    return jsonify({'is_correct': is_correct})

# (Other routes such as editquiz, makequiz, upload_quiz, reset_quiz, toggle_shuffle remain here)