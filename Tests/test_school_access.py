from datetime import date
from decimal import Decimal
from html.parser import HTMLParser

from Routes import main, teacher


class CsrfTokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.value = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name") == "csrf_token":
            self.value = attributes.get("value")


def new_csrf_token(client):
    response = client.get("/login")
    parser = CsrfTokenParser()
    parser.feed(response.get_data(as_text=True))
    assert parser.value
    return parser.value


def test_student_dashboard_ignores_tampered_student_id(client, monkeypatch):
    observed = []
    dashboard = {
        "student": {
            "id": 12,
            "student_number": "GA-2026-001",
            "class_id": 1,
            "class_name": "Grade 10A",
            "full_name": "Avery Student",
        },
        "term": 1,
        "attendance_counts": {"present": 0, "absent": 0, "late": 0},
        "attendance_percentage": 0,
        "recent_attendance": [],
        "results": [],
        "term_averages": [],
    }

    def get_dashboard(user_id, term):
        observed.append((user_id, term))
        return dashboard

    monkeypatch.setattr(main.school, "get_student_dashboard", get_dashboard)
    with client.session_transaction() as session:
        session["user_id"] = 501
        session["role"] = "student"
        session["mfa_verified"] = True
        session["full_name"] = "Signed-in Student"

    response = client.get("/student?student_id=999&user_id=999&term=1")

    assert response.status_code == 200
    assert observed == [(501, 1)]
    assert b"Avery Student" in response.data
    assert b"Another Student" not in response.data


def test_student_has_no_route_that_accepts_another_student_id(client):
    with client.session_transaction() as session:
        session["user_id"] = 501
        session["role"] = "student"
        session["mfa_verified"] = True

    response = client.get("/student/999")

    assert response.status_code == 404


def test_teacher_cannot_open_another_class_attendance(client, monkeypatch):
    observed = []

    def no_class_access(teacher_id, class_id, attendance_date):
        observed.append((teacher_id, class_id, attendance_date))
        return None

    monkeypatch.setattr(
        teacher.school,
        "get_class_roster_for_attendance",
        no_class_access,
    )
    with client.session_transaction() as session:
        session["user_id"] = 17
        session["role"] = "teacher"
        session["mfa_verified"] = True

    response = client.get("/teacher/classes/88/attendance")

    assert response.status_code == 403
    assert observed == [(17, 88, date.today())]


def test_teacher_dashboard_uses_authenticated_teacher_id(client, monkeypatch):
    observed = []
    monkeypatch.setattr(
        teacher.school,
        "get_teacher_dashboard",
        lambda teacher_id, term: observed.append((teacher_id, term))
        or {"term": term, "classes": [], "attendance_trend": []},
    )
    with client.session_transaction() as session:
        session["user_id"] = 17
        session["role"] = "teacher"
        session["mfa_verified"] = True

    response = client.get("/teacher?teacher_id=999&term=2")

    assert response.status_code == 200
    assert observed == [(17, 2)]
    assert b"Your classes" in response.data


def test_teacher_attendance_page_renders_scoped_roster(client, monkeypatch):
    monkeypatch.setattr(
        teacher.school,
        "get_class_roster_for_attendance",
        lambda teacher_id, class_id, _day: [{
            "id": 21,
            "student_number": "GA-2026-001",
            "full_name": "Avery Student",
            "status": "present",
        }] if (teacher_id, class_id) == (17, 3) else None,
    )
    with client.session_transaction() as session:
        session["user_id"] = 17
        session["role"] = "teacher"
        session["mfa_verified"] = True

    response = client.get("/teacher/classes/3/attendance")

    assert response.status_code == 200
    assert b"Avery Student" in response.data
    assert b"Save attendance" in response.data


def test_teacher_results_page_renders_derived_grade_badge(client, monkeypatch):
    monkeypatch.setattr(
        teacher.school,
        "get_class_roster_for_results",
        lambda teacher_id, class_id, subject_id, term: [{
            "id": 21,
            "student_number": "GA-2026-001",
            "full_name": "Avery Student",
            "subject_name": "Mathematics",
            "score": Decimal("80.00"),
            "grade": "A",
        }] if (teacher_id, class_id, subject_id, term) == (17, 3, 5, 2) else None,
    )
    with client.session_transaction() as session:
        session["user_id"] = 17
        session["role"] = "teacher"
        session["mfa_verified"] = True

    response = client.get("/teacher/classes/3/results/5?term=2")

    assert response.status_code == 200
    assert b"Mathematics" in response.data
    assert b"grade-a" in response.data
    assert b">A</span>" in response.data


def test_attendance_post_does_not_apply_tampered_student_id(client, monkeypatch):
    token = new_csrf_token(client)
    roster = [{
        "id": 21,
        "student_number": "GA-2026-001",
        "full_name": "Avery Student",
        "status": "present",
    }]
    calls = []
    monkeypatch.setattr(
        teacher.school,
        "get_class_roster_for_attendance",
        lambda _teacher_id, _class_id, _day: roster,
    )
    monkeypatch.setattr(
        teacher.school,
        "save_class_attendance",
        lambda *args: calls.append(args) or True,
    )
    with client.session_transaction() as session:
        session["user_id"] = 17
        session["role"] = "teacher"
        session["mfa_verified"] = True

    response = client.post(
        "/teacher/classes/3/attendance",
        data={
            "csrf_token": token,
            "attendance_date": date.today().isoformat(),
            "attendance_21": "late",
            "attendance_999": "absent",
        },
    )

    assert response.status_code == 302
    assert calls[0][0:2] == (17, 3)
    assert calls[0][3] == {21: "late"}


def test_results_post_only_saves_rostered_students(client, monkeypatch):
    token = new_csrf_token(client)
    roster = [{
        "id": 21,
        "student_number": "GA-2026-001",
        "full_name": "Avery Student",
        "subject_name": "Mathematics",
        "score": None,
    }]
    calls = []
    monkeypatch.setattr(
        teacher.school,
        "get_class_roster_for_results",
        lambda _teacher_id, _class_id, _subject_id, _term: roster,
    )
    monkeypatch.setattr(
        teacher.school,
        "save_class_results",
        lambda *args: calls.append(args) or True,
    )
    with client.session_transaction() as session:
        session["user_id"] = 17
        session["role"] = "teacher"
        session["mfa_verified"] = True

    response = client.post(
        "/teacher/classes/3/results/5",
        data={
            "csrf_token": token,
            "term": "2",
            "score_21": "80",
            "score_999": "0",
        },
    )

    assert response.status_code == 302
    assert calls[0][0:4] == (17, 3, 5, 2)
    assert calls[0][4] == {21: Decimal("80")}


def test_invalid_class_scores_fail_before_database_access(monkeypatch):
    def unexpected_connection():
        raise AssertionError("Invalid input must be rejected before connecting to the database.")

    monkeypatch.setattr(teacher.school, "connect_database", unexpected_connection)

    try:
        teacher.school.save_class_results(
            teacher_id=17,
            class_id=3,
            subject_id=5,
            term=2,
            scores={21: Decimal("100.01")},
            ip_address="127.0.0.1",
        )
    except ValueError as error:
        assert "0 and 100" in str(error)
    else:
        raise AssertionError("Out-of-range score was accepted.")
