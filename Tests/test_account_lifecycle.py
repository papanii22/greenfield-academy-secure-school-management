from html.parser import HTMLParser

from argon2 import PasswordHasher
import pytest

from Routes import admin, auth
from database import accounts


class CsrfTokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.value = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name") == "csrf_token":
            self.value = attributes.get("value")


def csrf_token(client):
    response = client.get("/login")
    parser = CsrfTokenParser()
    parser.feed(response.get_data(as_text=True))
    assert parser.value
    return parser.value


def set_session(client, role, user_id=1):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["role"] = role
        session["full_name"] = "Demo User"
        session["mfa_verified"] = True


def test_admin_only_account_pages_and_reset_action(client, monkeypatch):
    monkeypatch.setattr(accounts, "list_pending_signups", lambda: [])
    monkeypatch.setattr(accounts, "list_password_reset_requests", lambda: [])
    monkeypatch.setattr(
        admin.school,
        "get_user_edit_data",
        lambda _user_id: {
            "id": 8,
            "full_name": "Avery Student",
            "role": "student",
            "is_active": True,
        },
    )
    token = csrf_token(client)
    set_session(client, "teacher", 4)

    assert client.get("/admin/pending-accounts").status_code == 403
    assert client.get("/admin/password-reset-requests").status_code == 403
    assert client.get("/admin/users/8/reset-password").status_code == 403
    assert client.post(
        "/admin/users/8/reset-password",
        data={"csrf_token": token},
    ).status_code == 403

    set_session(client, "admin")
    assert client.get("/admin/pending-accounts").status_code == 200
    assert client.get("/admin/password-reset-requests").status_code == 200


def test_admin_password_reset_requires_confirmation_and_preserves_mfa(
    client,
    monkeypatch,
):
    user = {
        "id": 8,
        "full_name": "Avery Student",
        "role": "student",
        "is_active": True,
        "mfa_enabled": True,
        "mfa_secret": "existing-encrypted-secret",
    }
    calls = []
    monkeypatch.setattr(admin.school, "get_user_edit_data", lambda _user_id: user)
    monkeypatch.setattr(
        accounts,
        "reset_school_user_password",
        lambda *values: calls.append(values) or True,
    )
    token = csrf_token(client)
    set_session(client, "admin", 3)

    confirmation = client.get("/admin/users/8/reset-password")
    assert confirmation.status_code == 200
    assert b"Confirm reset password" in confirmation.data
    assert calls == []

    response = client.post(
        "/admin/users/8/reset-password",
        data={"csrf_token": token},
    )
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert "temporary_password" not in response.request.url
    assert calls[0][0] == 8
    assert calls[0][2:] == (3, "127.0.0.1")
    password = (
        page.split('class="temporary-password" aria-label="Temporary password">', 1)[1]
        .split("<", 1)[0]
    )
    assert len(password) >= 12
    assert PasswordHasher().verify(calls[0][1], password)
    assert user["mfa_enabled"] is True
    assert user["mfa_secret"] == "existing-encrypted-secret"


@pytest.mark.parametrize("role", ["admin", "other"])
def test_password_reset_cannot_target_non_teacher_student(
    client,
    monkeypatch,
    role,
):
    monkeypatch.setattr(
        admin.school,
        "get_user_edit_data",
        lambda _user_id: {
            "id": 9,
            "full_name": "Protected User",
            "role": role,
            "is_active": True,
        },
    )
    set_session(client, "admin")

    response = client.get("/admin/users/9/reset-password")

    assert response.status_code == 404


def test_signup_approval_keeps_mfa_enrollment_required(client, monkeypatch):
    password = "a-strong-signup-password"
    state = {"approved": False}
    user = {
        "id": 41,
        "full_name": "Avery Student",
        "email": "avery@greenfield.test",
        "password_hash": PasswordHasher().hash(password),
        "role": "student",
        "is_active": False,
        "mfa_enabled": False,
        "mfa_secret": None,
        "must_change_password": False,
        "auth_version": 1,
        "is_locked": False,
    }

    monkeypatch.setattr(
        accounts,
        "get_pending_signup",
        lambda _user_id: {
            "id": 41,
            "full_name": user["full_name"],
            "email": user["email"],
            "role": "student",
        },
    )
    monkeypatch.setattr(
        admin.school,
        "get_create_user_options",
        lambda: {"classes": [{"id": 3, "name": "Year 1"}], "subjects": []},
    )

    def approve(*_values):
        state["approved"] = True
        user["is_active"] = True
        return True

    monkeypatch.setattr(accounts, "approve_signup", approve)
    monkeypatch.setattr(
        auth,
        "get_user_by_email",
        lambda _email: user if state["approved"] else {**user, "is_active": False},
    )
    monkeypatch.setattr(auth, "get_user_auth_data", lambda _user_id: user)
    monkeypatch.setattr(accounts, "clear_login_failure_hint", lambda *_args: None)
    token = csrf_token(client)
    set_session(client, "admin")

    approved = client.post(
        "/admin/pending-accounts/41",
        data={
            "csrf_token": token,
            "class_id": "3",
            "student_number": "GA-2026-041",
        },
    )
    assert approved.status_code == 302
    assert state["approved"] is True

    with client.session_transaction() as session:
        session.clear()
    login_response = client.post(
        "/login",
        data={
            "csrf_token": csrf_token(client),
            "email": user["email"],
            "password": password,
        },
    )

    assert login_response.status_code == 302
    assert login_response.headers["Location"].endswith("/mfa/setup")


def test_failed_login_hint_is_identical_for_known_and_unknown_email(
    client,
    monkeypatch,
):
    known_user = {
        "id": 21,
        "email": "known@greenfield.test",
        "password_hash": PasswordHasher().hash("correct-password"),
        "is_active": True,
        "is_locked": False,
    }
    counts = {}

    def record_failure(email, ip_address):
        key = (email, ip_address)
        counts[key] = counts.get(key, 0) + 1
        return counts[key]

    monkeypatch.setattr(accounts, "record_login_failure_hint", record_failure)
    monkeypatch.setattr(
        auth,
        "get_user_by_email",
        lambda email: known_user if email == known_user["email"] else None,
    )
    monkeypatch.setattr(auth, "register_failed_login", lambda _user_id: False)
    monkeypatch.setattr(auth, "record_audit_event", lambda *_args: None)
    rendered = {}

    for email in ("known@greenfield.test", "unknown@greenfield.test"):
        token = csrf_token(client)
        for attempt in range(3):
            response = client.post(
                "/login",
                data={
                    "csrf_token": token,
                    "email": email,
                    "password": "wrong-password",
                },
            )
        page = response.get_data(as_text=True)
        assert "Having trouble signing in?" in page
        assert 'href="/request-reset"' in page
        rendered[email] = (
            "Having trouble signing in?" in page,
            "Request a password reset" in page,
        )
        with client.session_transaction() as session:
            session.clear()

    assert rendered["known@greenfield.test"] == rendered["unknown@greenfield.test"]


def test_reset_request_response_is_generic_for_known_and_unknown(client, monkeypatch):
    received = []
    monkeypatch.setattr(
        accounts,
        "create_password_reset_request",
        lambda email, _ip: received.append(email) or email.startswith("known@"),
    )
    token = csrf_token(client)
    responses = []

    for email in ("known@greenfield.test", "unknown@greenfield.test"):
        response = client.post(
            "/request-reset",
            data={"csrf_token": token, "email": email},
        )
        responses.append(response.get_data(as_text=True))

    message = "If this account exists, the administrator has been notified."
    assert all(message in page for page in responses)
    assert received == ["known@greenfield.test", "unknown@greenfield.test"]


def test_reset_storage_changes_password_and_version_without_touching_mfa(
    monkeypatch,
):
    class Cursor:
        def __init__(self):
            self.queries = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, _params):
            self.queries.append(query)

        def fetchone(self):
            return {"id": 8} if len(self.queries) == 1 else None

    class Connection:
        def __init__(self):
            self.active_cursor = Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return self.active_cursor

    connection = Connection()
    audit = []
    monkeypatch.setattr(accounts, "connect_database", lambda: connection)
    monkeypatch.setattr(
        accounts,
        "record_audit_event",
        lambda *args, **kwargs: audit.append((args, kwargs)),
    )

    assert accounts.reset_school_user_password(8, "argon-hash", 3, "127.0.0.1")

    user_update, request_update = connection.active_cursor.queries
    assert "password_hash = %s" in user_update
    assert "must_change_password = TRUE" in user_update
    assert "auth_version = auth_version + 1" in user_update
    assert "mfa" not in user_update.lower()
    assert "status = 'completed'" in request_update
    assert audit[0][0][:3] == (3, "admin_password_reset", "127.0.0.1")
    assert audit[0][1]["target_user_id"] == 8
    assert "argon-hash" not in repr(audit)


def test_new_public_pages_are_unprotected_by_login_but_csrf_protected(client):
    for route in ("/request-reset",):
        response = client.get(route)
        assert response.status_code == 200
        assert b'name="csrf_token"' in response.data
        assert client.post(route, data={}).status_code == 400
