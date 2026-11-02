from datetime import date
from decimal import Decimal

from Routes import admin, main, teacher
from reporting import MAX_REPORT_ROWS


def set_session(client, user_id, role):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["role"] = role
        session["full_name"] = "Example User"
        session["mfa_verified"] = True


def test_admin_attendance_export_uses_date_filter_and_attachment(client, monkeypatch):
    observed = []
    monkeypatch.setattr(
        admin.school,
        "get_admin_attendance_export_rows",
        lambda start, end: observed.append((start, end))
        or [{
            "attendance_date": date(2026, 4, 2),
            "class_name": "Grade 10A",
            "student_number": "GA-2026-001",
            "student_name": "Avery Student",
            "status": "present",
            "marked_by": "Jordan Teacher",
        }],
    )
    set_session(client, 1, "admin")

    response = client.get(
        "/admin/exports/attendance/csv?start_date=2026-04-02&end_date=2026-04-03"
    )

    assert response.status_code == 200
    assert observed == [(date(2026, 4, 2), date(2026, 4, 3))]
    assert response.headers["Content-Disposition"].endswith(".csv\"")
    assert b"Avery Student" in response.get_data()


def test_admin_grade_export_calculates_grade_from_score(client, monkeypatch):
    monkeypatch.setattr(
        admin.school,
        "get_admin_results_export_rows",
        lambda _term: [{"score": Decimal("75.99")}],
    )
    set_session(client, 1, "admin")

    response = client.get("/admin/exports/grades/csv?term=2")

    assert response.status_code == 200
    assert b"B,1" in response.get_data()


def test_admin_audit_export_preserves_date_and_actor_filters(client, monkeypatch):
    observed = []
    monkeypatch.setattr(
        admin.school,
        "get_admin_audit_export_rows",
        lambda *args: observed.append(args) or [{
            "created_at": "2026-04-02 10:30:00",
            "actor": "Avery Student",
            "action": "login_success",
            "ip_address": "127.0.0.1",
        }],
    )
    set_session(client, 1, "admin")

    response = client.get(
        "/admin/exports/audit/csv?user_id=51&action=login"
        "&start_date=2026-04-02&end_date=2026-04-02"
    )

    assert response.status_code == 200
    assert observed == [
        (date(2026, 4, 2), date(2026, 4, 2), 51, "login")
    ]
    assert b"login_success" in response.get_data()


def test_teacher_attendance_export_uses_authenticated_teacher_and_class(client, monkeypatch):
    observed = []
    monkeypatch.setattr(
        teacher.school,
        "get_class_roster_for_attendance",
        lambda teacher_id, class_id, attendance_date: observed.append(
            (teacher_id, class_id, attendance_date)
        )
        or [{
            "class_name": "Grade 10A",
            "student_number": "GA-2026-001",
            "full_name": "Avery Student",
            "status": "late",
        }],
    )
    set_session(client, 17, "teacher")

    response = client.get(
        "/teacher/classes/3/exports/attendance/csv?date=2026-04-02&teacher_id=999"
    )

    assert response.status_code == 200
    assert observed == [(17, 3, date(2026, 4, 2))]
    assert b"Grade 10A" in response.get_data()


def test_teacher_results_export_uses_assignment_and_term(client, monkeypatch):
    observed = []
    monkeypatch.setattr(
        teacher.school,
        "get_class_roster_for_results",
        lambda teacher_id, class_id, subject_id, term: observed.append(
            (teacher_id, class_id, subject_id, term)
        )
        or [{
            "class_name": "Grade 10A",
            "student_number": "GA-2026-001",
            "full_name": "Avery Student",
            "subject_name": "Mathematics",
            "score": Decimal("85.00"),
        }],
    )
    set_session(client, 17, "teacher")

    response = client.get(
        "/teacher/classes/3/exports/results/csv?subject_id=5&term=2"
    )

    assert response.status_code == 200
    assert observed == [(17, 3, 5, 2)]
    assert b"Mathematics" in response.get_data()
    assert b"85.00,A\r\n" in response.get_data()


def test_teacher_export_denies_unassigned_class(client, monkeypatch):
    monkeypatch.setattr(
        teacher.school,
        "get_class_roster_for_attendance",
        lambda *_args: None,
    )
    set_session(client, 17, "teacher")

    response = client.get("/teacher/classes/3/exports/attendance/pdf")

    assert response.status_code == 403


def test_student_results_export_uses_signed_in_student_only(client, monkeypatch):
    observed = []
    monkeypatch.setattr(
        main.school,
        "get_student_results_export_rows",
        lambda user_id, term: observed.append((user_id, term))
        or [{
            "term": term,
            "subject_name": "Mathematics",
            "score": Decimal("88.00"),
        }],
    )
    set_session(client, 501, "student")

    response = client.get(
        "/student/exports/results/pdf?term=2&user_id=999"
    )

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert observed == [(501, 2)]


def test_student_export_enforces_row_cap(client, monkeypatch):
    monkeypatch.setattr(
        main.school,
        "get_student_attendance_export_rows",
        lambda _user_id: [
            {"attendance_date": date(2026, 4, 2), "status": "present"}
        ] * (MAX_REPORT_ROWS + 1),
    )
    set_session(client, 501, "student")

    response = client.get("/student/exports/attendance/csv")

    assert response.status_code == 413
    assert b"5,000-row export limit" in response.data
