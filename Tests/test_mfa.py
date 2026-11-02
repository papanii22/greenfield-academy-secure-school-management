from html.parser import HTMLParser

import pyotp

from Routes import auth
from app import create_app
from mfa_crypto import decrypt_mfa_secret, encrypt_mfa_secret


class CsrfTokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.value = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name") == "csrf_token":
            self.value = attributes.get("value")


def csrf_token(client, path):
    response = client.get(path)
    parser = CsrfTokenParser()
    parser.feed(response.get_data(as_text=True))
    assert parser.value
    return parser.value


def pending_session(client, user_id, mode):
    with client.session_transaction() as session:
        session["mfa_pending_user_id"] = user_id
        session["mfa_pending_mode"] = mode


def test_mfa_enrollment_encrypts_secret_and_shows_backup_codes_once(
    client,
    monkeypatch,
):
    user = {
        "id": 41,
        "full_name": "Avery Student",
        "email": "avery@greenfield.test",
        "role": "student",
        "is_active": True,
        "must_change_password": True,
        "mfa_secret": None,
        "mfa_enabled": False,
        "is_locked": False,
    }
    backup_hashes = []
    events = []
    monkeypatch.setattr(auth, "get_user_auth_data", lambda _user_id: user.copy())

    def save_secret(_user_id, encrypted_secret):
        user["mfa_secret"] = encrypted_secret
        return True

    monkeypatch.setattr(auth, "save_pending_mfa_secret", save_secret)
    monkeypatch.setattr(
        auth,
        "enable_user_mfa",
        lambda _user_id, hashes, _ip: backup_hashes.extend(hashes) or True,
    )
    monkeypatch.setattr(auth, "clear_login_failures", lambda _user_id: None)
    monkeypatch.setattr(
        auth,
        "record_audit_event",
        lambda *args: events.append(args),
    )
    pending_session(client, 41, "setup")

    setup_response = client.get("/mfa/setup")
    assert setup_response.status_code == 200
    assert setup_response.headers["Cache-Control"] == "no-store"
    assert b"data:image/png;base64," in setup_response.data
    secret = decrypt_mfa_secret(user["mfa_secret"])
    assert secret in setup_response.get_data(as_text=True)
    token = csrf_token(client, "/mfa/setup")

    response = client.post(
        "/mfa/setup",
        data={"csrf_token": token, "code": pyotp.TOTP(secret).now()},
    )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert b"Save your recovery codes" in response.data
    assert b"Continue to password change" in response.data
    assert len(backup_hashes) == 10
    assert events == [(41, "login_success", "127.0.0.1")]
    with client.session_transaction() as session:
        assert session["user_id"] == 41
        assert session["mfa_verified"] is True
        assert session["must_change_password"] is True
        assert "mfa_pending_user_id" not in session

    refresh = client.get("/mfa/setup")
    assert refresh.status_code == 302
    assert refresh.headers["Location"].endswith("/dashboard")
    dashboard_response = client.get("/dashboard")
    assert dashboard_response.status_code == 302
    assert dashboard_response.headers["Location"].endswith("/change-password")


def test_pending_mfa_setup_can_generate_a_fresh_key(client, monkeypatch):
    user = {
        "id": 41,
        "full_name": "Avery Student",
        "email": "avery@greenfield.test",
        "role": "student",
        "is_active": True,
        "mfa_secret": None,
        "mfa_enabled": False,
        "is_locked": False,
    }
    events = []
    monkeypatch.setattr(
        auth,
        "get_user_auth_data",
        lambda _user_id: user.copy(),
    )

    def save_secret(_user_id, encrypted_secret):
        user["mfa_secret"] = encrypted_secret
        return True

    def reset_secret(_user_id):
        user["mfa_secret"] = None
        return True

    monkeypatch.setattr(auth, "save_pending_mfa_secret", save_secret)
    monkeypatch.setattr(auth, "reset_pending_mfa_secret", reset_secret)
    monkeypatch.setattr(
        auth,
        "record_audit_event",
        lambda *args: events.append(args),
    )
    pending_session(client, 41, "setup")

    first_setup = client.get("/mfa/setup")
    assert first_setup.status_code == 200
    old_secret = decrypt_mfa_secret(user["mfa_secret"])
    token = csrf_token(client, "/mfa/setup")

    reset_response = client.post(
        "/mfa/setup/reset",
        data={"csrf_token": token},
    )

    assert reset_response.status_code == 302
    assert reset_response.headers["Location"].endswith("/mfa/setup")
    assert user["mfa_secret"] is None
    assert events == [(41, "mfa_setup_reset", "127.0.0.1")]

    new_setup = client.get("/mfa/setup")
    assert new_setup.status_code == 200
    new_secret = decrypt_mfa_secret(user["mfa_secret"])
    assert new_secret != old_secret


def test_backup_code_login_is_normalized_and_completes_mfa(client, monkeypatch):
    secret = pyotp.random_base32()
    user = {
        "id": 41,
        "full_name": "Avery Student",
        "email": "avery@greenfield.test",
        "role": "student",
        "is_active": True,
        "must_change_password": False,
        "mfa_secret": encrypt_mfa_secret(secret),
        "mfa_enabled": True,
        "is_locked": False,
    }
    consumed = []
    events = []
    monkeypatch.setattr(auth, "get_user_auth_data", lambda _user_id: user)
    monkeypatch.setattr(
        auth,
        "consume_mfa_backup_code",
        lambda user_id, code, _ip: consumed.append((user_id, code)) or True,
    )
    monkeypatch.setattr(auth, "clear_login_failures", lambda _user_id: None)
    monkeypatch.setattr(
        auth,
        "record_audit_event",
        lambda *args: events.append(args),
    )
    pending_session(client, 41, "verify")
    token = csrf_token(client, "/mfa/verify")

    response = client.post(
        "/mfa/verify",
        data={"csrf_token": token, "code": "ABCD-EFGH-IJKL"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    assert consumed == [(41, "ABCDEFGHIJKL")]
    assert events == [
        (41, "mfa_backup_login", "127.0.0.1"),
        (41, "login_success", "127.0.0.1"),
    ]


def test_failed_mfa_code_locks_account_and_discards_pending_session(
    client,
    monkeypatch,
):
    user = {
        "id": 41,
        "full_name": "Avery Student",
        "email": "avery@greenfield.test",
        "role": "student",
        "is_active": True,
        "mfa_secret": encrypt_mfa_secret(pyotp.random_base32()),
        "mfa_enabled": True,
        "is_locked": False,
    }
    events = []
    monkeypatch.setattr(auth, "get_user_auth_data", lambda _user_id: user)
    monkeypatch.setattr(auth, "consume_mfa_backup_code", lambda *_args: False)
    monkeypatch.setattr(auth, "register_failed_login", lambda _user_id: True)
    monkeypatch.setattr(
        auth,
        "record_audit_event",
        lambda *args: events.append(args),
    )
    pending_session(client, 41, "verify")
    token = csrf_token(client, "/mfa/verify")

    response = client.post(
        "/mfa/verify",
        data={"csrf_token": token, "code": "000000"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    assert events == [
        (41, "mfa_failure", "127.0.0.1"),
        (41, "account_locked", "127.0.0.1"),
    ]
    with client.session_transaction() as session:
        assert "mfa_pending_user_id" not in session
        assert "user_id" not in session


def test_pre_mfa_session_cannot_access_protected_page(client):
    with client.session_transaction() as session:
        session["user_id"] = 41
        session["role"] = "student"

    response = client.get("/student")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    with client.session_transaction() as session:
        assert not session


def test_login_is_rate_limited_by_ip():
    app = create_app(
        {
            "TESTING": True,
            "RATELIMIT_ENABLED": True,
            "RATELIMIT_STORAGE_URI": "memory://",
        }
    )
    client = app.test_client()

    responses = [client.get("/login") for _ in range(6)]

    assert [response.status_code for response in responses[:5]] == [200] * 5
    assert responses[5].status_code == 429
