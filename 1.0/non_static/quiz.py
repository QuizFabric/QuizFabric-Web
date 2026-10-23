import random
import os
import json

def read_quiz(quiz_name):
    # Use the directory of this file as the base (which is ...\non_static)
    base_dir = os.path.dirname(__file__)
    # Append only the 'quiz' folder since this file is already in non_static
    quiz_dir = os.path.join(base_dir, "quiz")
    file_path = os.path.join(quiz_dir, f"{quiz_name}.txt")
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Quiz file {file_path} not found")

    # Read and parse the quiz file
    questions = []
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read().replace('\r', '')  # Remove carriage returns for cross-platform compatibility
        # Split by double newlines to get question blocks
        blocks = [block.strip() for block in content.split('\n\n') if block.strip()]
        i = 0
        while i < len(blocks):
            block = blocks[i]
            if '\n' not in block:
                # This is a question
                question = block
                i += 1
                answers = []
                correct_indices = []
                if i < len(blocks):
                    answers_block = blocks[i]
                    lines = answers_block.split('\n')
                    for line in lines:
                        line = line.strip()
                        if line.startswith('*'):
                            correct = line[1:]
                            answers.append(correct)
                            correct_indices.append(len(answers) - 1)
                        elif line:
                            answers.append(line)
                    i += 1
                # Tagged question types ([SLIDER] / [PUZZLE]) or plain format
                if question.startswith('[SLIDER]'):
                    q_text = question[len('[SLIDER]'):].strip()
                    # Answers block: min, max, *correct, optional ~tolerance
                    try:
                        q_min = float(answers[0])
                        q_max = float(answers[1])
                        q_answer = float(answers[correct_indices[0]])
                    except (ValueError, TypeError, IndexError):
                        q_min = q_max = q_answer = None
                    if q_min is not None and q_text:
                        q_tolerance = 0.0
                        for extra in answers[3:]:
                            if extra.startswith('~'):
                                try:
                                    q_tolerance = float(extra[1:])
                                except ValueError:
                                    pass
                        questions.append({"question": q_text, "answers": [], "correct_indices": [],
                                          "type": "slider", "min": q_min, "max": q_max,
                                          "answer": q_answer, "tolerance": q_tolerance})
                elif question.startswith('[PUZZLE]'):
                    q_text = question[len('[PUZZLE]'):].strip()
                    if q_text and len(answers) >= 2:
                        # Listed order is the correct order
                        questions.append({"question": q_text, "answers": answers,
                                          "correct_indices": list(range(len(answers))),
                                          "type": "puzzle"})
                else:
                    # Now process the question
                    if correct_indices:
                        # Has correct markers, multiple choice
                        if len(correct_indices) == 1 and len(answers) == 1:
                            # Convert to type-in if only one correct answer and one answer option
                            answer = answers[correct_indices[0]]
                            questions.append({"question": question, "answers": [], "correct_indices": [], "type": "type", "answer": answer})
                        else:
                            questions.append({"question": question, "answers": answers, "correct_indices": correct_indices, "type": "mc"})
                    elif len(answers) == 1:
                        # Type-in question
                        answer = answers[0]
                        questions.append({"question": question, "answers": [], "correct_indices": [], "type": "type", "answer": answer})
                    # Else, skip invalid
            else:
                # Unexpected block with \n, skip
                i += 1
    # Ensure questions are returned in their original order
    return questions

def get_random_question(quiz_name):
    questions = read_quiz(quiz_name)
    if questions:
        return random.choice(questions)
    return {'question': "No questions available", 'answer': ""}

def validate_answer(question, user_answer):
    if question['type'] == 'mc':
        return user_answer in question['correct_indices']
    elif question['type'] == 'slider':
        try:
            value = float(user_answer)
        except (TypeError, ValueError):
            return False
        tolerance = question.get('tolerance', 0) or 0
        return abs(value - float(question['answer'])) <= tolerance
    elif question['type'] == 'puzzle':
        # User answer: list of answers in the submitted order
        if isinstance(user_answer, str):
            try:
                user_answer = json.loads(user_answer)
            except ValueError:
                user_answer = [s.strip() for s in user_answer.split(',') if s.strip()]
        if not isinstance(user_answer, list):
            return False
        return user_answer == question.get('answers', [])
    else:
        # type-in
        correct_answer = question.get('answer', '')
        return correct_answer.strip().lower() == user_answer.strip().lower()

def correct_answer_text(q):
    """Human-readable correct answer for display on host/control pages."""
    if not q:
        return ''
    if q['type'] == 'mc':
        return ', '.join(q['answers'][i] for i in q['correct_indices'])
    if q['type'] == 'puzzle':
        return ' → '.join(q['answers'])
    return str(q.get('answer', ''))

def question_payload(q_obj):
    """Build the 'new_question' payload sent to players and the control page."""
    payload = {'question': q_obj.get('question')}
    t = q_obj.get('type')
    if t == 'mc':
        payload.update({'answers': q_obj.get('answers', []), 'type': 'mc'})
    elif t == 'slider':
        payload.update({'type': 'slider', 'min': q_obj.get('min', 0), 'max': q_obj.get('max', 100)})
    elif t == 'puzzle':
        answers = list(q_obj.get('answers', []))
        random.shuffle(answers)
        payload.update({'type': 'puzzle', 'answers': answers})
    else:
        payload.update({'type': 'type'})
    if q_obj.get('images'):
        payload['images'] = q_obj.get('images')
    return payload
