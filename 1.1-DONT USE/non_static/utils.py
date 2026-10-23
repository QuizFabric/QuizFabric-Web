import json
import os

# New path: Go one directory up from non_static, then into the data folder.
UPLOADED_JSON_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "uploaded.json")

def read_uploaded_json():
    """Read the uploaded.json file and return its content as a dictionary."""
    if os.path.exists(UPLOADED_JSON_PATH):
        with open(UPLOADED_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def write_uploaded_json(data):
    """Write the given dictionary to the uploaded.json file."""
    with open(UPLOADED_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

def example_util_function():
    return "This is an example utility function."

class ExampleUtility:
    @staticmethod
    def example_method():
        return "This is an example method from ExampleUtility."