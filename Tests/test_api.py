from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import jwt

from Routes import api


class CsrfTokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.value = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name") == "csrf_token":
            self.value = attributes.get("value")


class AccessTokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.reading_token = False
        self.value = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.reading_token = (
            tag == "code"
            and "api-token-value" in attributes.get("class", "").split()
        )

    def handle_data(self, data):
        if self.reading_token:
            self.value = data

    def handle_endtag(self, tag):
        if tag == "code":
            self.reading_token = False


def csrf_token(client):
    response = client.get("/login")
    parser = CsrfTokenParser()
    parser.feed(response.get_data(as_text=True))
    assert parser.value
    return parser.value


def make_token(
    app,
    user_id=41,
    role="student",
    scope="profile:read dashboard:read",
    auth_version=0,
):
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id),
            "role": role,
            "ver": auth_version,
            "scope": scope,
            "iat": now,
            "exp": now + timedelta(minutes=15),
            "iss": "greenfield-academy",
            "aud": "greenfield-api",
        },
        app.config["JWT_SECRET"],
        algorithm="HS256",
    )


def test_mfa_authenticated_user_can_create_short_lived_api_token(client):
    token_csrf = csrf_token(client)
    with client.session_transaction() as session:
        session["user_id"] = 41
        session["role"] = "student"
        session["full_name"] = "Avery Student"
        session["mfa_verified"] = True

    response = client.post(
        "/api-token",
        data={"csrf_token": token_csrf},
    )

    parser = AccessTokenParser()
    parser.feed(response.get_data(as_text=True))
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert parser.value
    claims = jwt.decode(
        parser.value,
        client.application.config["JWT_SECRET"],
        algorithms=["HS256"],
        audience="greenfield-api",
        issuer="greenfield-academy",
    )
    assert claims["sub"] == "41"
    assert claims["scope"] == "profile:read dashboard:read"


def test_api_profile_requires_valid_token_and_current_active_user(client, monkeypatch):
    monkeypatch.setattr(
        api,
        "get_user_profile",
        lambda user_id: {
            "id": user_id,
            "full_name": "Avery Student",
            "email": "avery@greenfield.test",
            "role": "student",
            "is_active": True,
        },
    )
    token = make_token(client.application)

    response = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json == {
        "id": 41,
        "full_name": "Avery Student",
        "email": "avery@greenfield.test",
        "role": "student",
    }
    assert response.headers["Cache-Control"] == "no-store"

    missing_token = client.get("/api/v1/me")
    assert missing_token.status_code == 401
    assert missing_token.headers["WWW-Authenticate"] == "Bearer"


def test_api_rejects_role_drift_and_insufficient_scope(client, monkeypatch):
    monkeypatch.setattr(
        api,
        "get_user_profile",
        lambda user_id: {
            "id": user_id,
            "full_name": "Avery Teacher",
            "email": "avery@greenfield.test",
            "role": "teacher",
            "is_active": True,
        },
    )
    mismatched_token = make_token(client.application, role="student")
    response = client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {mismatched_token}"},
    )
    assert response.status_code == 401

    profile_only_token = make_token(
        client.application,
        role="teacher",
        scope="profile:read",
    )
    response = client.get(
        "/api/v1/dashboard",
        headers={"Authorization": f"Bearer {profile_only_token}"},
    )
    assert response.status_code == 403
    assert response.json == {"error": "insufficient_scope"}


def test_api_rejects_token_after_auth_version_changes(client, monkeypatch):
    monkeypatch.setattr(
        api,
        "get_user_profile",
        lambda user_id: {
            "id": user_id,
            "full_name": "Avery Student",
            "email": "avery@greenfield.test",
            "role": "student",
            "is_active": True,
            "auth_version": 2,
        },
    )
    token = make_token(client.application, auth_version=1)

    response = client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.json == {"error": "invalid_or_missing_token"}


def test_api_dashboard_uses_token_user_and_returns_json_validation_errors(
    client,
    monkeypatch,
):
    observed = []
    monkeypatch.setattr(
        api,
        "get_user_profile",
        lambda user_id: {
            "id": user_id,
            "full_name": "Avery Student",
            "email": "avery@greenfield.test",
            "role": "student",
            "is_active": True,
        },
    )
    monkeypatch.setattr(
        api.school,
        "get_student_dashboard",
        lambda user_id, term: observed.append((user_id, term))
        or {"term": term, "average": 82.5},
    )
    token = make_token(client.application)

    response = client.get(
        "/api/v1/dashboard?term=2&user_id=999",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json == {"term": 2, "average": 82.5}
    assert observed == [(41, 2)]

    invalid_term = client.get(
        "/api/v1/dashboard?term=8",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert invalid_term.status_code == 400
    assert invalid_term.json == {"error": "invalid_term"}
