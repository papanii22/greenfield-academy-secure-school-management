from html.parser import HTMLParser

from argon2 import PasswordHasher
from click.testing import CliRunner
import pyotp
import pytest
from psycopg import errors

from Routes import auth
from mfa_crypto import encrypt_mfa_secret


@pytest.fixture(autouse=True)
def isolate_login_failure_hint_storage(monkeypatch):
    counts = {}
    monkeypatch.setattr(auth.accounts, "clear_login_failure_hint", lambda *_args: None)

    def record_failure(email, ip_address):
        key = (email, ip_address)
        counts[key] = counts.get(key, 0) + 1
        return counts[key]

    monkeypatch.setattr(auth.accounts, "record_login_failure_hint", record_failure)
    return counts


class CsrfTokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.value = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name") == "csrf_token":
            self.value = attributes.get("value")


def csrf_token(client, path="/login") -> str:
    response = client.get(path)
    parser = CsrfTokenParser()
    parser.feed(response.get_data(as_text=True))
    assert parser.value
    return parser.value


def login(client, email: str, password: str):
    return client.post(
        "/login",
        data={
            "csrf_token": csrf_token(client),
            "email": email,
            "password": password,
            "submit": "Sign in",
        },
        follow_redirects=False,
    )


def test_successful_login_clears_old_session_and_redirects(client, monkeypatch):
    password = "correct horse battery staple"
    secret = pyotp.random_base32()
    user = {
        "id": 41,
        "full_name": "Avery Student",
        "email": "avery@greenfield.test",
        "password_hash": PasswordHasher().hash(password),
        "role": "student",
        "is_active": True,
        "mfa_secret": encrypt_mfa_secret(secret),
        "mfa_enabled": True,
        "must_change_password": False,
        "is_locked": False,
    }
    audit_events = []
    monkeypatch.setattr(auth, "get_user_by_email", lambda _email: user)
    monkeypatch.setattr(auth, "get_user_auth_data", lambda _user_id: user)
    monkeypatch.setattr(auth, "clear_login_failures", lambda _user_id: None)
    monkeypatch.setattr(auth, "consume_mfa_backup_code", lambda *_args: False)
    monkeypatch.setattr(
        auth,
        "record_audit_event",
        lambda *args: audit_events.append(args),
    )

    with client.session_transaction() as session:
        session["old_session_value"] = "remove-me"

    response = login(client, user["email"], password)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/mfa/verify")
    with client.session_transaction() as session:
        assert session["mfa_pending_user_id"] == 41
        assert "user_id" not in session

    token = csrf_token(client, "/mfa/verify")
    response = client.post(
        "/mfa/verify",
        data={"csrf_token": token, "code": pyotp.TOTP(secret).now()},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    with client.session_transaction() as session:
        assert session["user_id"] == 41
        assert session["role"] == "student"
        assert session["mfa_verified"] is True
        assert "old_session_value" not in session
        assert session.permanent is True
    assert audit_events == [(41, "login_success", "127.0.0.1")]


@pytest.mark.parametrize("role", ["teacher", "student"])
def test_signup_creates_pending_teacher_or_student_request(
    client,
    monkeypatch,
    role,
):
    submitted = []
    monkeypatch.setattr(
        auth.accounts,
        "submit_signup",
        lambda *values: submitted.append(values) or 51,
    )
    token = csrf_token(client, "/signup")
    password = "a-strong-signup-password"

    response = client.post(
        "/signup",
        data={
            "csrf_token": token,
            "full_name": "Avery Green",
            "email": "Avery@Greenfield.test",
            "role": role,
            "password": password,
            "confirm_password": password,
        },
    )

    assert response.status_code == 200
    assert b"Your request has been received. An administrator must approve your account before you can sign in." in response.data
    assert submitted[0][0:3] == ("Avery Green", "avery@greenfield.test", role)
    assert PasswordHasher().verify(submitted[0][3], password)
    assert submitted[0][4] == "127.0.0.1"


@pytest.mark.parametrize("role", ["admin", "owner", ""])
def test_signup_rejects_roles_outside_teacher_student(client, monkeypatch, role):
    submitted = []
    monkeypatch.setattr(
        auth.accounts,
        "submit_signup",
        lambda *values: submitted.append(values),
    )
    response = client.post(
        "/signup",
        data={
            "csrf_token": csrf_token(client, "/signup"),
            "full_name": "Avery Green",
            "email": "avery@greenfield.test",
            "role": role,
            "password": "a-strong-signup-password",
            "confirm_password": "a-strong-signup-password",
        },
    )

    assert response.status_code == 200
    assert b"An administrator must approve" not in response.data
    assert submitted == []


def test_duplicate_signup_has_the_same_generic_response(client, monkeypatch):
    def duplicate_signup(*_values):
        raise errors.UniqueViolation("duplicate")

    monkeypatch.setattr(auth.accounts, "submit_signup", duplicate_signup)
    token = csrf_token(client, "/signup")
    response = client.post(
        "/signup",
        data={
            "csrf_token": token,
            "full_name": "Avery Green",
            "email": "avery@greenfield.test",
            "role": "student",
            "password": "a-strong-signup-password",
            "confirm_password": "a-strong-signup-password",
        },
    )

    assert response.status_code == 200
    assert b"Your request has been received. An administrator must approve your account before you can sign in." in response.data


def test_signup_is_public_and_login_does_not_link_to_signup(client):
    response = client.get("/signup")
    login_response = client.get("/login")

    assert response.status_code == 200
    assert b"Create account" not in login_response.data
    assert b'href="/signup"' not in login_response.data


def test_temporary_password_login_requires_mfa_before_password_change(client, monkeypatch):
    user = {
        "id": 41,
        "full_name": "Avery Student",
        "email": "avery@greenfield.test",
        "password_hash": PasswordHasher().hash("temporary-password"),
        "role": "student",
        "is_active": True,
        "must_change_password": True,
    }
    monkeypatch.setattr(auth, "get_user_by_email", lambda _email: user)
    monkeypatch.setattr(auth, "record_audit_event", lambda *_args: None)

    response = login(client, user["email"], "temporary-password")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/mfa/setup")
    with client.session_transaction() as session:
        assert session["mfa_pending_user_id"] == 41
        assert "user_id" not in session


def test_temporary_password_cannot_access_dashboard(client):
    with client.session_transaction() as session:
        session["user_id"] = 41
        session["role"] = "student"
        session["must_change_password"] = True
        session["mfa_verified"] = True

    response = client.get("/student")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/change-password")


def test_password_change_updates_hash_and_clears_requirement(client, monkeypatch):
    current_password = "temporary-password"
    new_password = "a-new-secure-password"
    saved = []
    monkeypatch.setattr(
        auth,
        "get_user_auth_data",
        lambda user_id: {
            "id": user_id,
            "password_hash": PasswordHasher().hash(current_password),
            "is_active": True,
        },
    )
    monkeypatch.setattr(
        auth,
        "change_user_password",
        lambda *args: saved.append(args) or True,
    )
    token = csrf_token(client)
    with client.session_transaction() as session:
        session["user_id"] = 41
        session["role"] = "student"
        session["must_change_password"] = True
        session["mfa_verified"] = True

    response = client.post(
        "/change-password",
        data={
            "csrf_token": token,
            "current_password": current_password,
            "new_password": new_password,
            "confirm_password": new_password,
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    assert saved[0][0] == 41
    assert PasswordHasher().verify(saved[0][1], new_password)
    with client.session_transaction() as session:
        assert session["must_change_password"] is False


def test_wrong_password_uses_generic_failure_message(client, monkeypatch):
    user = {
        "id": 41,
        "full_name": "Avery Student",
        "email": "avery@greenfield.test",
        "password_hash": PasswordHasher().hash("the-correct-password"),
        "role": "student",
        "is_active": True,
    }
    audit_events = []
    monkeypatch.setattr(auth, "get_user_by_email", lambda _email: user)
    monkeypatch.setattr(
        auth,
        "record_audit_event",
        lambda *args: audit_events.append(args),
    )
    monkeypatch.setattr(auth, "register_failed_login", lambda _user_id: False)

    response = login(client, user["email"], "not-the-password")

    assert response.status_code == 200
    assert b"Invalid email or password." in response.data
    assert b"wrong password" not in response.data.lower()
    assert audit_events == [(41, "login_failure", "127.0.0.1")]
    with client.session_transaction() as session:
        assert "user_id" not in session


def test_login_failure_is_generic_for_unknown_email(client, monkeypatch):
    audit_events = []
    monkeypatch.setattr(auth, "get_user_by_email", lambda _email: None)
    monkeypatch.setattr(
        auth,
        "record_audit_event",
        lambda *args: audit_events.append(args),
    )

    response = login(client, "unknown@greenfield.test", "some-password")

    assert response.status_code == 200
    assert b"Invalid email or password." in response.data
    assert audit_events == [(None, "login_failure", "127.0.0.1")]


def test_inactive_account_uses_generic_failure_message(client, monkeypatch):
    user = {
        "id": 41,
        "full_name": "Avery Student",
        "email": "avery@greenfield.test",
        "password_hash": PasswordHasher().hash("the-correct-password"),
        "role": "student",
        "is_active": False,
    }
    audit_events = []
    monkeypatch.setattr(auth, "get_user_by_email", lambda _email: user)
    monkeypatch.setattr(
        auth,
        "record_audit_event",
        lambda *args: audit_events.append(args),
    )

    response = login(client, user["email"], "the-correct-password")

    assert response.status_code == 200
    assert b"Invalid email or password." in response.data
    assert audit_events == [(41, "login_failure", "127.0.0.1")]


def test_admin_route_allows_admin(client, admin_dashboard):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = "admin"
        session["mfa_verified"] = True

    response = client.get("/admin")

    assert response.status_code == 200
    assert b"Administrator dashboard" in response.data


def test_admin_route_redirects_logged_out_user(client):
    response = client.get("/admin")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_admin_route_rejects_non_admin(client):
    with client.session_transaction() as session:
        session["user_id"] = 2
        session["role"] = "teacher"
        session["mfa_verified"] = True

    response = client.get("/admin")

    assert response.status_code == 403
    assert b"Access denied" in response.data


def test_logout_requires_csrf_token(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = "admin"
        session["mfa_verified"] = True

    response = client.post("/logout")

    assert response.status_code == 400


def test_logout_clears_session_and_records_audit(client, monkeypatch):
    audit_events = []
    monkeypatch.setattr(
        auth,
        "record_audit_event",
        lambda *args: audit_events.append(args),
    )
    token = csrf_token(client)
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = "admin"
        session["mfa_verified"] = True

    response = client.post(
        "/logout",
        data={"csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    assert audit_events == [(1, "logout", "127.0.0.1")]
    with client.session_transaction() as session:
        assert not session


def test_get_logout_is_not_allowed(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = "admin"
        session["mfa_verified"] = True

    response = client.get("/logout")

    assert response.status_code == 405


def test_unknown_page_uses_app_error_template(client):
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert b"Page not found" in response.data


def test_create_admin_cli_rejects_password_under_12_characters(app):
    runner = CliRunner()

    result = runner.invoke(
        app.cli,
        ["create-admin"],
        input="Demo Administrator\nadmin@greenfield.test\nshort-pass\nshort-pass\n",
    )

    assert result.exit_code == 1
    assert "Password must be at least 12 characters." in result.output
    assert "short-password" not in result.output
