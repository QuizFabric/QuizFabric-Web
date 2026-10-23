document.addEventListener('DOMContentLoaded', function() {
    const quizForm = document.getElementById('quiz-form');
    const resultEl = document.getElementById('result');
    const answerInput = document.getElementById('user-answer');  
    const quizName = document.getElementById('quiz-name').value; // get quiz name

    // Store the current question globally after loading it
    window.currentQuestion = null;

    quizForm.addEventListener('submit', function(event) {
        event.preventDefault();
        const userAnswer = answerInput.value;
        if(!window.currentQuestion) {
            resultEl.textContent = "No question loaded.";
            return;
        }
        fetch(`/${quizName}/quiz/validate`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            // send both the current question and user answer in the request body
            body: JSON.stringify({
                question: window.currentQuestion,
                answer: userAnswer
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.is_correct) {
                resultEl.textContent = "Correct!";
                // Load a new question after a short delay
                setTimeout(displayQuestion, 1500);
            } else {
                resultEl.textContent = "Incorrect! Try again!";
            }
        })
        .catch(error => {
            console.error('Error validating answer:', error);
        });
        answerInput.value = ''; // Clear the input field
    });

    displayQuestion();
});

function displayQuestion() {
    const quizName = document.getElementById('quiz-name').value;
    fetch(`/${quizName}/quiz/question`)
        .then(response => response.json())
        .then(data => {
            window.currentQuestion = data; // store the current question globally
            const questionEl = document.getElementById('question');
            if (questionEl) {
                questionEl.textContent = data.question;
            } else {
                console.error('Element with id "question" not found.');
            }
            const resultEl = document.getElementById('result');
            if(resultEl) resultEl.textContent = '';
        })
        .catch(error => {
            console.error('Error fetching question:', error);
        });
}