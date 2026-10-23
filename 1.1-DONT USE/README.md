# My Flask Quiz Application

This is a simple Flask application that allows users to answer quiz questions. The application is structured to separate static files, non-static files, and templates for better organization.

## Project Structure

```
my-flask-app
├── app.py               # Entry point of the Flask application
├── non_static
│   ├── utils.py        # Utility functions and classes
│   └── quiz.py         # Quiz-related functionality
├── static
│   ├── css
│   │   └── style.css    # CSS styles for the application
│   └── js
│       └── main.js      # JavaScript for client-side interactivity
├── templates
│   └── index.html       # Main HTML template for the application
└── README.md            # Documentation for the project
```

## Setup Instructions

1. **Clone the repository**:
   ```
   git clone <repository-url>
   cd PythonServer/1.0
   ```

2. **Create a virtual environment**:
   ```
   python -m venv venv
   ```

3. **Activate the virtual environment**:
   - On Windows:
     ```
     venv\Scripts\activate
     ```
   - On macOS/Linux:
     ```
     source venv/bin/activate
     ```

4. **Install the required packages**:
   ```
   pip install Flask
   ```

## Usage

1. **Run the application**:
   ```
   python app.py
   ```

2. **Access the application**:
   Open your web browser and go to `http://127.0.0.1:5710`.

## Features

- Users can answer quiz questions.
- The application uses Flask for the backend.
- Static files are served for CSS and JavaScript.
- Dynamic content is rendered using HTML templates.

## Contributing

Feel free to submit issues or pull requests for improvements or bug fixes.
