# wsl-terminal-app

This project is a web application that functions as a Linux terminal, allowing users to run commands using Windows Subsystem for Linux (WSL) on a Windows server. The application is built using Flask and provides a simple interface for executing commands and viewing their output.

## Project Structure

```
wsl-terminal-app
├── app.py                # Main entry point of the application
├── requirements.txt      # Lists dependencies for the project
├── templates             # Contains HTML templates for the web pages
│   ├── home.html        # Home page template
│   ├── about.html       # About page template
│   └── terminal.html     # Terminal interface template
└── README.md             # Documentation for the project
```

## Setup Instructions

1. **Clone the repository**:
   ```
   git clone <repository-url>
   cd wsl-terminal-app
   ```

2. **Create a virtual environment** (optional but recommended):
   ```
   python -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`
   ```

3. **Install the required dependencies**:
   ```
   pip install -r requirements.txt
   ```

## Usage

1. **Run the application**:
   ```
   python app.py
   ```

2. **Access the web application**:
   Open your web browser and navigate to `http://127.0.0.1:5000`.

3. **Using the terminal interface**:
   - Navigate to the terminal page to input Linux commands.
   - The output of the commands will be displayed on the same page.

## License

This project is licensed under the MIT License - see the LICENSE file for details.