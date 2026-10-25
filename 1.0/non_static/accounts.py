import datetime
import hmac
import json
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from werkzeug.security import generate_password_hash

_thread_lock = threading.Lock()
_username_pattern = re.compile(r"^[a-zA-Z0-9_-]{3,24}$")


class RegistrationRateLimitError(ValueError):
    pass


class AccountValidationError(ValueError):
    pass


@contextmanager
def _store_lock(path):
    lock_path = path + ".lock"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _thread_lock:
        with open(lock_path, "a+b") as lock_file:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write(b"\0")
                    lock_file.flush()
                while True:
                    lock_file.seek(0)
                    try:
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read_store(path):
    if not os.path.exists(path):
        return {"users": {}, "registration_attempts": {}}
    with open(path, "r", encoding="utf-8") as store_file:
        store = json.load(store_file)
    if not isinstance(store, dict):
        raise ValueError("Account store must contain a JSON object")
    users = store.get("users", {})
    attempts = store.get("registration_attempts", {})
    if not isinstance(users, dict) or not isinstance(attempts, dict):
        raise ValueError("Account store has an invalid structure")
    return {"users": users, "registration_attempts": attempts}


def _write_store(path, store):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=directory, delete=False) as store_file:
        json.dump(store, store_file, indent=2)
        temp_path = store_file.name
    try:
        os.replace(temp_path, path)
    except OSError:
        os.remove(temp_path)
        raise


def register_account(path, client_ip, username, password, legacy_quizzes=None,
                     admin_username=None, admin_password=None,
                     password_confirmation=None):
    today = datetime.date.today().isoformat()
    normalized_username = username.strip().lower()
    quizzes = sorted(set(legacy_quizzes or []))

    with _store_lock(path):
        store = _read_store(path)
        attempts = store["registration_attempts"]
        if attempts.get(client_ip) == today:
            raise RegistrationRateLimitError(
                "Only one registration attempt per IP address is allowed each day.")

        attempts[client_ip] = today
        validation_error = None
        if not _username_pattern.fullmatch(username.strip()):
            validation_error = (
                "Username must be 3-24 characters and use only letters, numbers, "
                "underscores, or hyphens.")
        elif len(password) < 12:
            validation_error = "Password must be at least 12 characters long."
        elif len(password) > 128:
            validation_error = "Password must be no longer than 128 characters."
        elif (password_confirmation is not None
              and password != password_confirmation):
            validation_error = "The passwords do not match."
        elif normalized_username in store["users"]:
            validation_error = "That username is already registered."
        elif (admin_username and normalized_username == admin_username.lower()
              and (not admin_password
                   or not hmac.compare_digest(password, admin_password))):
            validation_error = "That username is reserved for the server administrator."

        if validation_error:
            _write_store(path, store)
            raise AccountValidationError(validation_error)

        store["users"][normalized_username] = {
            "username": username.strip(),
            "password_hash": generate_password_hash(password),
            "quizzes": quizzes,
            "role": "admin" if (
                admin_username and normalized_username == admin_username.lower()
                and admin_password
                and hmac.compare_digest(password, admin_password)) else "user",
        }
        _write_store(path, store)

    return normalized_username


def get_account(path, username):
    if not username:
        return None
    with _store_lock(path):
        store = _read_store(path)
        account = store["users"].get(username.strip().lower())
        return dict(account) if account else None


def get_owned_quizzes(path, username):
    account = get_account(path, username)
    if not account:
        return []
    quizzes = account.get("quizzes", [])
    return list(quizzes) if isinstance(quizzes, list) else []


def add_owned_quiz(path, username, quiz_name):
    with _store_lock(path):
        store = _read_store(path)
        account = store["users"].get(username.strip().lower())
        if not account:
            raise ValueError("Authenticated account no longer exists")
        quizzes = account.setdefault("quizzes", [])
        if quiz_name not in quizzes:
            quizzes.append(quiz_name)
            _write_store(path, store)


def rename_owned_quiz(path, username, old_name, new_name):
    with _store_lock(path):
        store = _read_store(path)
        account = store["users"].get(username.strip().lower())
        if not account:
            raise ValueError("Authenticated account no longer exists")
        quizzes = account.get("quizzes", [])
        if old_name not in quizzes:
            return False
        account["quizzes"] = [
            new_name if quiz == old_name else quiz for quiz in quizzes]
        _write_store(path, store)
        return True


def remove_owned_quiz(path, username, quiz_name):
    with _store_lock(path):
        store = _read_store(path)
        account = store["users"].get(username.strip().lower())
        if not account:
            raise ValueError("Authenticated account no longer exists")
        quizzes = account.get("quizzes", [])
        if quiz_name not in quizzes:
            return False
        account["quizzes"] = [quiz for quiz in quizzes if quiz != quiz_name]
        _write_store(path, store)
        return True
