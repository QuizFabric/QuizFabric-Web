from flask import Flask, render_template, request, jsonify
import subprocess

app = Flask(__name__)

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/terminal", methods=["GET", "POST"])
def terminal():
    if request.method == "POST":
        # Support both JSON and form data
        if request.is_json:
            command = request.json.get("command")
        else:
            command = request.form.get("command")
            
        print("Received command:", command)  # Debug: log received command
        output = ""
        if command:
            try:
                # Execute the command using WSL
                output = subprocess.check_output(
                    ["wsl"] + command.split(),
                    stderr=subprocess.STDOUT,
                    text=True
                )
            except subprocess.CalledProcessError as e:
                output = e.output
                print("Error output:", output)  # Debug: log command error output
        else:
            output = "No command provided."
        # Disable randomization logic here (if applicable)
        print("Returning output:", output)  # Debug
        return jsonify({"output": output})
    # For GET, render the terminal page
    return render_template("terminal.html", output="")

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5702)