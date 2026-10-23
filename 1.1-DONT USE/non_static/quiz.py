import random
import os

def read_quiz(quiz_name):
    # Use the directory of this file as the base (which is ...\non_static)
    base_dir = os.path.dirname(__file__)
    # Append only the 'quiz' folder since this file is already in non_static
    quiz_dir = os.path.join(base_dir, "quiz")
    file_path = os.path.join(quiz_dir, f"{quiz_name}.txt")
    print("Reading quiz file from:", file_path)
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
    else:
        # type-in
        correct_answer = question.get('answer', '')
        return correct_answer.strip().lower() == user_answer.strip().lower()
