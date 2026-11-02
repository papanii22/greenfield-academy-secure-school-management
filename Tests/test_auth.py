from html.parser import HTMLParser

from argon2 import PasswordHasher
from click.testing import CliRunner

from Routes import auth


class CsrfTokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.value = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name") == "csrf_token":
            self.value = attributes.get("value")


def csrf_token(client) -> str:
    response = client.get("/login")
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
    user = {
        "id": 41,
        "full_name": "Avery Student",
        "email": "avery@greenfield.test",
        "password_hash": PasswordHasher().hash(password),
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

    with client.session_transaction() as session:
        session["old_session_value"] = "remove-me"

    response = login(client, user["email"], password)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    with client.session_transaction() as session:
        assert session["user_id"] == 41
        assert session["role"] == "student"
        assert "old_session_value" not in session
        assert session.permanent is True
    assert audit_events == [(41, "login_success", "127.0.0.1")]


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


def test_admin_route_allows_admin(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = "admin"

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

    response = client.get("/admin")

    assert response.status_code == 403
    assert b"Access denied" in response.data


def test_logout_requires_csrf_token(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = "admin"

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
