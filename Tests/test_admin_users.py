from datetime import date
from html.parser import HTMLParser

from argon2 import PasswordHasher

from Routes import admin


class CsrfTokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.value = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name") == "csrf_token":
            self.value = attributes.get("value")


class TemporaryPasswordParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_password = False
        self.value = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.in_password = (
            tag == "div"
            and "temporary-password" in attributes.get("class", "").split()
        )

    def handle_data(self, data):
        if self.in_password:
            self.value = data.strip()

    def handle_endtag(self, tag):
        if tag == "div" and self.in_password:
            self.in_password = False


def csrf_token(client):
    response = client.get("/login")
    parser = CsrfTokenParser()
    parser.feed(response.get_data(as_text=True))
    assert parser.value
    return parser.value


def admin_session(client, user_id=1):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["role"] = "admin"
        session["full_name"] = "Demo Administrator"
        session["mfa_verified"] = True


def test_admin_creates_student_with_one_time_temporary_password(
    client,
    monkeypatch,
):
    token = csrf_token(client)
    calls = []
    monkeypatch.setattr(
        admin.school,
        "get_create_user_options",
        lambda: {
            "classes": [{"id": 3, "name": "Grade 10A"}],
            "subjects": [],
        },
    )
    monkeypatch.setattr(
        admin.school,
        "create_school_user",
        lambda **values: calls.append(values) or 51,
    )
    admin_session(client)

    response = client.post(
        "/admin/users/new",
        data={
            "csrf_token": token,
            "full_name": "Avery Student",
            "email": "avery@greenfield.test",
            "role": "student",
            "class_id": "3",
            "student_number": "GA-2026-001",
        },
    )

    parser = TemporaryPasswordParser()
    parser.feed(response.get_data(as_text=True))
    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"
    assert parser.value
    assert len(parser.value) >= 12
    assert calls[0]["role"] == "student"
    assert calls[0]["class_id"] == 3
    assert calls[0]["student_number"] == "GA-2026-001"
    assert calls[0]["actor_id"] == 1
    assert PasswordHasher().verify(calls[0]["password_hash"], parser.value)
    assert b"must_change_password" not in response.data


def test_admin_creates_teacher_with_two_subject_assignments(client, monkeypatch):
    token = csrf_token(client)
    calls = []
    monkeypatch.setattr(
        admin.school,
        "get_create_user_options",
        lambda: {
            "classes": [{"id": 4, "name": "Grade 11A"}],
            "subjects": [
                {"id": 2, "name": "Mathematics"},
                {"id": 3, "name": "Physics"},
                {"id": 4, "name": "Chemistry"},
            ],
        },
    )
    monkeypatch.setattr(
        admin.school,
        "create_school_user",
        lambda **values: calls.append(values) or 52,
    )
    admin_session(client)

    response = client.post(
        "/admin/users/new",
        data={
            "csrf_token": token,
            "full_name": "Jordan Teacher",
            "email": "jordan@greenfield.test",
            "role": "teacher",
            "class_id": "4",
            "subject_ids": ["2", "3"],
        },
    )

    assert response.status_code == 201
    assert calls[0]["role"] == "teacher"
    assert calls[0]["class_id"] == 4
    assert calls[0]["subject_ids"] == [2, 3]
    assert b"Jordan Teacher" in response.data


def test_teacher_creation_requires_two_or_three_subjects(client, monkeypatch):
    token = csrf_token(client)
    calls = []
    monkeypatch.setattr(
        admin.school,
        "get_create_user_options",
        lambda: {
            "classes": [{"id": 4, "name": "Grade 11A"}],
            "subjects": [{"id": 2, "name": "Mathematics"}],
        },
    )
    monkeypatch.setattr(
        admin.school,
        "create_school_user",
        lambda **values: calls.append(values) or 52,
    )
    admin_session(client)

    response = client.post(
        "/admin/users/new",
        data={
            "csrf_token": token,
            "full_name": "Jordan Teacher",
            "email": "jordan@greenfield.test",
            "role": "teacher",
            "class_id": "4",
            "subject_ids": ["2"],
        },
    )

    assert response.status_code == 200
    assert b"Choose two or three subjects." in response.data
    assert calls == []


def test_admin_user_list_applies_search_and_role_filters(client, monkeypatch):
    observed = []
    monkeypatch.setattr(
        admin.school,
        "list_users",
        lambda search, role, page: observed.append((search, role, page))
        or {"users": [], "total": 0, "page": page, "per_page": 20},
    )
    admin_session(client)

    response = client.get("/admin/users?q=Avery&role=student")

    assert response.status_code == 200
    assert observed == [("Avery", "student", 1)]
    assert b"School accounts" in response.data


def test_admin_edits_account_details(client, monkeypatch):
    token = csrf_token(client)
    calls = []
    monkeypatch.setattr(
        admin.school,
        "get_user_edit_data",
        lambda user_id: {
            "id": user_id,
            "full_name": "Avery Student",
            "email": "avery@greenfield.test",
            "role": "student",
            "is_active": True,
        },
    )
    monkeypatch.setattr(
        admin.school,
        "update_user",
        lambda *args: calls.append(args) or True,
    )
    admin_session(client)

    response = client.post(
        "/admin/users/51/edit",
        data={
            "csrf_token": token,
            "full_name": "Avery Green",
            "email": "avery.green@greenfield.test",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/users")
    assert calls == [
        (51, "Avery Green", "avery.green@greenfield.test", 1, "127.0.0.1")
    ]


def test_admin_audit_page_applies_filters(client, monkeypatch):
    observed = []
    monkeypatch.setattr(
        admin.school,
        "get_audit_logs",
        lambda *args: observed.append(args)
        or {"logs": [], "users": [], "total": 0, "page": 1, "per_page": 25},
    )
    admin_session(client)

    response = client.get(
        "/admin/audit?user_id=51&action=login&start_date=2026-04-01"
        "&end_date=2026-04-02"
    )

    assert response.status_code == 200
    assert observed == [
        (51, "login", date(2026, 4, 1), date(2026, 4, 2), 1)
    ]
    assert b"Audit log" in response.data


def test_admin_cannot_deactivate_their_own_account(client, monkeypatch):
    token = csrf_token(client)
    calls = []
    monkeypatch.setattr(
        admin.school,
        "set_user_active",
        lambda *args: calls.append(args) or True,
    )
    admin_session(client, user_id=1)

    response = client.post(
        "/admin/users/1/deactivate",
        data={"csrf_token": token},
    )

    assert response.status_code == 302
    assert calls == []
