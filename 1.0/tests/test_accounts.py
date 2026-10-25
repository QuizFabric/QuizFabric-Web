import os
import tempfile
import unittest
import uuid

from socketio import WSGIApp
from werkzeug.test import Client
from werkzeug.wrappers import Response
from werkzeug.security import check_password_hash

from non_static.accounts import (
    AccountValidationError,
    RegistrationRateLimitError,
    add_owned_quiz,
    get_account,
    get_owned_quizzes,
    register_account,
    remove_owned_quiz,
    rename_owned_quiz,
)


class AccountStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store_path = os.path.join(self.temp_dir.name, "logins.json")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_passwords_are_hashed_and_quiz_ownership_updates(self):
        username = register_account(
            self.store_path, "192.0.2.10", "Owner", "correct horse battery")
        account = get_account(self.store_path, username)

        self.assertNotEqual(
            account["password_hash"], "correct horse battery")
        self.assertTrue(
            check_password_hash(account["password_hash"], "correct horse battery"))

        add_owned_quiz(self.store_path, username, "quiz")
        self.assertEqual(get_owned_quizzes(self.store_path, username), ["quiz"])
        self.assertTrue(rename_owned_quiz(
            self.store_path, username, "quiz", "renamed"))
        self.assertEqual(
            get_owned_quizzes(self.store_path, username), ["renamed"])
        self.assertTrue(remove_owned_quiz(
            self.store_path, username, "renamed"))
        self.assertEqual(get_owned_quizzes(self.store_path, username), [])

    def test_registration_attempt_is_limited_per_ip_per_day(self):
        register_account(
            self.store_path, "192.0.2.11", "first", "correct horse battery")

        with self.assertRaises(RegistrationRateLimitError):
            register_account(
                self.store_path, "192.0.2.11", "second", "correct horse battery")

    def test_invalid_registration_consumes_daily_attempt(self):
        with self.assertRaises(AccountValidationError):
            register_account(
                self.store_path, "192.0.2.12", "x", "correct horse battery")

        with self.assertRaises(RegistrationRateLimitError):
            register_account(
                self.store_path, "192.0.2.12", "valid_user",
                "correct horse battery")

    def test_mismatched_password_confirmation_consumes_daily_attempt(self):
        with self.assertRaisesRegex(AccountValidationError, "do not match"):
            register_account(
                self.store_path, "192.0.2.15", "valid_user",
                "correct horse battery",
                password_confirmation="different password")

        with self.assertRaises(RegistrationRateLimitError):
            register_account(
                self.store_path, "192.0.2.15", "valid_user",
                "correct horse battery",
                password_confirmation="correct horse battery")

    def test_admin_username_requires_configured_password(self):
        with self.assertRaises(AccountValidationError):
            register_account(
                self.store_path, "192.0.2.13", "siteadmin",
                "wrong admin password", admin_username="siteadmin",
                admin_password="correct admin password")

        register_account(
            self.store_path, "192.0.2.14", "siteadmin",
            "correct admin password", admin_username="siteadmin",
            admin_password="correct admin password")
        self.assertEqual(
            get_account(self.store_path, "siteadmin")["role"], "admin")


class QuizAuthorizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("FLASK_SECRET_KEY", "test-only-secret")
        import app as app_module

        cls.app_module = app_module
        cls.flask_app = app_module.app
        cls.flask_app.config["TESTING"] = True

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_store_path = self.app_module.ACCOUNT_STORE
        self.app_module.ACCOUNT_STORE = os.path.join(
            self.temp_dir.name, "logins.json")
        self.quiz_name = f"authz-test-{uuid.uuid4().hex}"
        quiz_dir = os.path.join(
            os.path.dirname(self.app_module.__file__), "non_static", "quiz")
        self.quiz_path = os.path.join(quiz_dir, self.quiz_name + ".txt")
        with open(self.quiz_path, "w", encoding="utf-8") as quiz_file:
            quiz_file.write("test quiz")

        register_account(
            self.app_module.ACCOUNT_STORE, "192.0.2.20",
            "owner", "owner password secure")
        register_account(
            self.app_module.ACCOUNT_STORE, "192.0.2.21",
            "attacker", "attacker password secure")
        add_owned_quiz(
            self.app_module.ACCOUNT_STORE, "owner", self.quiz_name)

    def tearDown(self):
        if os.path.exists(self.quiz_path):
            os.remove(self.quiz_path)
        self.app_module.ACCOUNT_STORE = self.original_store_path
        self.temp_dir.cleanup()

    def _login(self, username, password, remote_addr):
        client = self.flask_app.test_client()
        response = client.post(
            "/login", data={"username": username, "password": password},
            base_url="https://localhost",
            environ_overrides={"REMOTE_ADDR": remote_addr})
        self.assertEqual(response.status_code, 302)
        return client

    def test_unauthenticated_requests_are_redirected_or_rejected(self):
        client = self.flask_app.test_client()

        page_response = client.get(
            "/", base_url="https://localhost",
            environ_overrides={"REMOTE_ADDR": "192.0.2.22"})
        api_response = client.post(
            "/api/deletequiz", data={"quiz_name": self.quiz_name},
            base_url="https://localhost",
            environ_overrides={"REMOTE_ADDR": "192.0.2.22"})

        self.assertEqual(page_response.status_code, 302)
        self.assertEqual(api_response.status_code, 401)
        self.assertTrue(os.path.exists(self.quiz_path))

    def test_3ds_read_apis_are_public_but_account_apis_stay_protected(self):
        client = self.flask_app.test_client()

        quizzes_response = client.get(
            "/api/quizzes", base_url="http://localhost",
            environ_overrides={"REMOTE_ADDR": "192.0.2.26"})
        quiz_response = client.get(
            f"/api/quiz/{self.quiz_name}", base_url="http://localhost",
            environ_overrides={"REMOTE_ADDR": "192.0.2.26"})
        my_quizzes_response = client.get(
            "/api/myquizzes", base_url="http://localhost",
            environ_overrides={"REMOTE_ADDR": "192.0.2.26"})

        self.assertEqual(quizzes_response.status_code, 200)
        self.assertEqual(quiz_response.status_code, 200)
        self.assertEqual(my_quizzes_response.status_code, 401)

    def test_socketio_wsgi_routes_http_api_requests_to_flask(self):
        socketio_wsgi_app = WSGIApp(
            self.app_module.socketio.server, self.flask_app)
        client = Client(socketio_wsgi_app, Response)

        response = client.get(
            "/api/quizzes",
            environ_overrides={"REMOTE_ADDR": "192.0.2.27"})

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'"quizzes"', response.data)

    def test_registration_creates_hashed_account_and_persistent_secure_cookie(self):
        client = self.flask_app.test_client()
        response = client.post(
            "/register", data={
                "username": "new_user",
                "password": "long password phrase",
                "password_confirmation": "long password phrase",
            },
            base_url="https://localhost",
            environ_overrides={"REMOTE_ADDR": "192.0.2.23"})

        self.assertEqual(response.status_code, 302)
        cookie = response.headers["Set-Cookie"]
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertEqual(
            client.get(
                "/api/myquizzes", base_url="https://localhost",
                environ_overrides={"REMOTE_ADDR": "192.0.2.23"}).status_code,
            200)
        account = get_account(self.app_module.ACCOUNT_STORE, "new_user")
        self.assertNotEqual(account["password_hash"], "long password phrase")

        limited_response = self.flask_app.test_client().post(
            "/register",
            data={"username": "another_user",
                  "password": "another safe password",
                  "password_confirmation": "another safe password"},
            base_url="https://localhost",
            environ_overrides={"REMOTE_ADDR": "192.0.2.23"})
        self.assertEqual(limited_response.status_code, 429)

    def test_join_page_and_guest_socket_connection_do_not_require_login(self):
        client = self.flask_app.test_client()
        page_response = client.get(
            "/join", base_url="https://localhost",
            environ_overrides={"REMOTE_ADDR": "192.0.2.24"})
        socket_client = self.app_module.socketio.test_client(
            self.flask_app, flask_test_client=client)

        self.assertEqual(page_response.status_code, 200)
        self.assertTrue(socket_client.is_connected())
        socket_client.emit("host_join", {"code": "not-a-host-account"})
        errors = [event for event in socket_client.get_received()
                  if event["name"] == "error"]
        self.assertTrue(errors)
        self.assertEqual(
            errors[-1]["args"][0]["message"],
            "Login required to host a game.")
        socket_client.disconnect()

    def test_registration_rejects_mismatched_password_confirmation(self):
        client = self.flask_app.test_client()
        mismatch_response = client.post(
            "/register", data={
                "username": "new_user",
                "password": "long password phrase",
                "password_confirmation": "a different password",
            },
            base_url="https://localhost",
            environ_overrides={"REMOTE_ADDR": "192.0.2.25"})
        retry_response = self.flask_app.test_client().post(
            "/register", data={
                "username": "new_user",
                "password": "long password phrase",
                "password_confirmation": "long password phrase",
            },
            base_url="https://localhost",
            environ_overrides={"REMOTE_ADDR": "192.0.2.25"})

        self.assertEqual(mismatch_response.status_code, 400)
        self.assertIn(b"passwords do not match", mismatch_response.data.lower())
        self.assertEqual(retry_response.status_code, 429)
        self.assertIsNone(get_account(
            self.app_module.ACCOUNT_STORE, "new_user"))

    def test_non_owner_cannot_open_edit_or_delete_by_url(self):
        client = self._login(
            "attacker", "attacker password secure", "192.0.2.21")

        edit_response = client.get(
            f"/editquiz/{self.quiz_name}", base_url="https://localhost",
            environ_overrides={"REMOTE_ADDR": "192.0.2.21"})
        delete_page_response = client.get(
            f"/deletequiz?quiz={self.quiz_name}", base_url="https://localhost",
            environ_overrides={"REMOTE_ADDR": "192.0.2.21"})
        delete_response = client.post(
            "/deletequiz", data={"quiz": self.quiz_name},
            base_url="https://localhost",
            environ_overrides={"REMOTE_ADDR": "192.0.2.21"})
        api_delete_response = client.post(
            "/api/deletequiz", data={"quiz_name": self.quiz_name},
            base_url="https://localhost",
            environ_overrides={"REMOTE_ADDR": "192.0.2.21"})
        admin_response = client.get(
            "/logs", base_url="https://localhost",
            environ_overrides={"REMOTE_ADDR": "192.0.2.21"})

        self.assertEqual(edit_response.status_code, 404)
        self.assertEqual(delete_page_response.status_code, 404)
        self.assertEqual(delete_response.status_code, 404)
        self.assertEqual(api_delete_response.status_code, 404)
        self.assertEqual(admin_response.status_code, 403)
        self.assertTrue(os.path.exists(self.quiz_path))

    def test_owner_can_delete_quiz(self):
        client = self._login("owner", "owner password secure", "192.0.2.20")
        response = client.post(
            "/deletequiz", data={"quiz": self.quiz_name},
            base_url="https://localhost",
            environ_overrides={"REMOTE_ADDR": "192.0.2.20"})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(os.path.exists(self.quiz_path))
        self.assertNotIn(
            self.quiz_name,
            get_owned_quizzes(self.app_module.ACCOUNT_STORE, "owner"))


if __name__ == "__main__":
    unittest.main()
