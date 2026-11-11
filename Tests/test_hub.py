from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from Routes import hub as hub_routes
from database import hub
from test_school_access import new_csrf_token


NOW = datetime(2026, 5, 4, 9, 0, tzinfo=timezone.utc)
TODAY = date.today()


def sign_in(client, role, user_id=7, name="Demo User"):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["role"] = role
        session["mfa_verified"] = True
        session["full_name"] = name


def page(rows, **extra):
    return {"rows": rows, "total": len(rows), "page": 1, "pages": 1, "per_page": 10, **extra}


@pytest.fixture
def fake_hub(monkeypatch):
    """Replace every database call the hub pages make with sample data."""
    calls = []
    event = {
        "id": 3, "title": "Science Fair Setup", "description": "Hall B",
        "event_date": TODAY, "start_time": time(8), "end_time": time(9),
        "category": "exam", "audience": "all", "created_by": 7, "creator": "Demo User",
    }
    notice = {
        "id": 5, "title": "Welcome Back to School!", "body": "First line\nSecond line",
        "audience": "all", "tags": "School, Academic", "created_at": NOW,
        "author_id": 7, "author": "Principal Carter", "views": 12,
    }
    conversation = {
        "id": 9, "is_group": True, "title": "Staff Coordination", "other_role": None,
        "last_body": "Reports due Friday", "last_at": NOW, "last_sender": "Ms. Patel",
        "unread": 2,
    }
    fakes = {
        "list_teachers": lambda *a: page([{
            "id": 4, "full_name": "Sir Kofi Mensah", "email": "10021@gamail.edu.gh",
            "is_active": True, "phone": "024 000 0000", "address": "Accra",
            "subjects": "History", "classes": "Year 8, Year 11",
        }]),
        "list_students": lambda *a: page([{
            "user_id": 11, "full_name": "Ama Owusu", "email": "1010000001@gamail.edu.gh",
            "is_active": True, "id": 21, "student_number": "1010000001",
            "class_name": "Year 10", "date_of_birth": date(2010, 4, 18),
            "phone": None, "address": None, "gender": "female",
        }], classes=[{"id": 3, "name": "Year 10"}]),
        "attendance_grid": lambda *a: {
            "classes": [{"id": 3, "name": "Year 10"}], "class_id": 3,
            "dates": [TODAY + timedelta(days=n) for n in range(14)],
            "rows": [{"full_name": "Ama Owusu",
                      "cells": ["present", "absent", "late"] + [None] * 11}],
            "can_mark": True,
        },
        "list_fees": lambda *a: page([{
            "id": 2, "term": 1, "tuition_fee": Decimal("4500"), "activities_fee": Decimal("300"),
            "misc_fee": Decimal("200"), "amount_paid": Decimal("1000"), "due_date": TODAY,
            "total": Decimal("5000"), "status": "pending", "student_id": 21,
            "student_number": "1010000001", "class_id": 3, "class_name": "Year 10",
            "full_name": "Ama Owusu",
        }], classes=[{"id": 3, "name": "Year 10"}], students=[{
            "id": 21, "full_name": "Ama Owusu", "student_number": "1010000001",
            "class_name": "Year 10"}]),
        "fee_summary": lambda year: {
            "totals": {"total": Decimal("5000"), "tuition": Decimal("4500"),
                       "activities": Decimal("300"), "misc": Decimal("200"),
                       "collected": Decimal("1000")},
            "monthly": [0.0] * 12, "year": year,
        },
        "student_fees": lambda *a: [],
        "list_expenses": lambda *a: page([{
            "id": 1, "category": "Laboratory", "description": "Chemicals",
            "quantity": "100 units", "amount": Decimal("500"), "paid_on": TODAY,
        }], categories=["Laboratory"], sum=Decimal("500")),
        "list_notices": lambda *a, **k: page([notice]),
        "get_notice": lambda notice_id, user_id, role: notice if notice_id == 5 else None,
        "events_between": lambda *a: [event],
        "list_books": lambda *a: page([{
            "id": 1, "code": "2024-LIT-001-01", "title": "Great Expectations",
            "author": "Charles Dickens", "subject": "English Literature",
            "classes": "Year 12", "publish_year": 1861, "status": "available",
        }]),
        "list_conversations": lambda *a, **k: [conversation],
        "get_conversation": lambda conversation_id, user_id: {
            "id": 9, "title": "Staff Coordination", "is_group": True,
            "description": "Team hub", "created_at": NOW,
            "members": [{"id": 7, "full_name": "Demo User", "role": "admin"},
                        {"id": 8, "full_name": "Ms. Patel", "role": "teacher"}],
            "messages": [
                {"id": 1, "body": "<script>alert(1)</script>", "created_at": NOW,
                 "sender_id": 8, "sender": "Ms. Patel", "sender_role": "teacher"},
                {"id": 2, "body": "Thanks", "created_at": NOW, "sender_id": 7,
                 "sender": "Demo User", "sender_role": "admin"},
            ],
        } if conversation_id == 9 else None,
        "message_recipients": lambda *a: [{"id": 8, "full_name": "Ms. Patel", "role": "teacher"}],
        "teacher_assignment_options": lambda *a: [{
            "class_id": 3, "subject_id": 5, "class_name": "Year 10", "subject_name": "History"}],
        "list_teacher_assignments": lambda *a: [{
            "id": 4, "title": "Essay", "description": "", "due_at": NOW,
            "class_name": "Year 10", "subject_name": "History", "class_size": 20,
            "handed_in": 5}],
        "teacher_submissions": lambda *a, **k: page([{
            "assignment_id": 4, "student_id": 21, "full_name": "Ama Owusu",
            "class_name": "Year 10", "subject_name": "History", "title": "Essay",
            "due_at": NOW, "status": "submitted"}]),
        "student_assignments": lambda *a, **k: page([{
            "id": 4, "title": "Essay", "description": "Two pages", "due_at": NOW,
            "subject_name": "History", "status": "in_progress"}], done=1, in_progress=1),
        "get_profile": lambda user_id: {
            "id": user_id, "full_name": "Demo User", "email": "demo@greenfield.test",
            "role": "teacher", "created_at": NOW, "mfa_enabled": True, "phone": None,
            "address": None, "date_of_birth": None, "gender": None, "bio": None,
            "student_number": None, "class_name": None,
            "teaching": [{"class_name": "Year 10", "subject_name": "History"}],
        },
    }
    for name, fake in fakes.items():
        def recorder(*args, _name=name, _fake=fake, **kwargs):
            calls.append((_name, args))
            return _fake(*args, **kwargs)
        monkeypatch.setattr(hub, name, recorder)
    return calls


PAGES = {
    "admin": ["/teachers", "/students", "/attendance", "/finance/fees", "/finance/expenses",
              "/notices", "/notices?notice=5", "/calendar", "/calendar?view=week",
              "/calendar?view=day", "/library", "/messages", "/messages/9", "/profile",
              "/settings"],
    "teacher": ["/students", "/attendance", "/assignments", "/notices", "/calendar",
                "/library", "/messages", "/profile", "/settings"],
    "student": ["/attendance", "/assignments", "/finance/fees", "/notices", "/calendar",
                "/library", "/messages/9", "/profile", "/settings"],
}


@pytest.mark.parametrize("role,path", [(r, p) for r, paths in PAGES.items() for p in paths])
def test_hub_pages_render_for_each_role(client, fake_hub, role, path):
    sign_in(client, role)

    response = client.get(path)

    assert response.status_code == 200, (role, path)
    assert b'name="csrf_token"' in response.data


@pytest.mark.parametrize("role,path", [
    ("student", "/teachers"),
    ("student", "/students"),
    ("teacher", "/teachers"),
    ("teacher", "/finance/fees"),
    ("teacher", "/finance/expenses"),
    ("student", "/finance/expenses"),
    ("admin", "/assignments"),
])
def test_hub_pages_refuse_other_roles(client, fake_hub, role, path):
    sign_in(client, role)

    assert client.get(path).status_code == 403
    assert fake_hub == []


def test_hub_pages_require_sign_in(client):
    for path in ("/notices", "/messages", "/calendar", "/library", "/profile"):
        response = client.get(path)
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/login")


@pytest.mark.parametrize("role,path,data", [
    ("student", "/notices", {"title": "x", "body": "y", "audience": "all"}),
    ("student", "/calendar/events", {"title": "x", "event_date": "2026-05-01",
                                      "category": "event", "audience": "all"}),
    ("teacher", "/library/books", {"code": "B1", "title": "t", "author": "a", "subject": "s"}),
    ("teacher", "/finance/expenses", {"category": "c", "description": "d", "amount": "1",
                                       "paid_on": "2026-05-01"}),
    ("student", "/messages/groups", {"title": "Group", "member_ids": "8"}),
    ("student", "/assignments", {"target": "3:5", "title": "t", "due_date": "2099-01-01"}),
])
def test_writes_are_limited_by_role(client, monkeypatch, role, path, data):
    writes = []
    for name in ("create_notice", "create_event", "create_book", "create_expense",
                 "create_group", "create_assignment"):
        monkeypatch.setattr(hub, name, lambda *a, **k: writes.append(a))
    token = new_csrf_token(client)
    sign_in(client, role)

    response = client.post(path, data={"csrf_token": token, **data})

    assert response.status_code == 403
    assert writes == []


def test_hub_posts_require_csrf_token(client, monkeypatch):
    writes = []
    monkeypatch.setattr(hub, "create_notice", lambda *a: writes.append(a))
    sign_in(client, "admin")

    response = client.post("/notices", data={"title": "x", "body": "y", "audience": "all"})

    assert response.status_code == 400
    assert writes == []


def test_messages_escape_user_content(client, fake_hub):
    sign_in(client, "admin")

    response = client.get("/messages/9")

    assert b"<script>alert(1)</script>" not in response.data
    assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in response.data


def test_conversation_outside_membership_is_not_found(client, fake_hub):
    sign_in(client, "student", user_id=501)

    assert client.get("/messages/77").status_code == 404
    assert ("get_conversation", (77, 501)) in fake_hub


def test_send_message_uses_session_identity(client, monkeypatch):
    sent = []
    monkeypatch.setattr(hub, "send_message", lambda *a: sent.append(a) or True)
    token = new_csrf_token(client)
    sign_in(client, "student", user_id=501)

    response = client.post(
        "/messages/9",
        data={"csrf_token": token, "body": "Hello", "sender_id": "1"},
    )

    assert response.status_code == 302
    assert sent == [(9, 501, "Hello")]


def test_student_cannot_message_disallowed_recipient(client, monkeypatch):
    monkeypatch.setattr(hub, "start_direct_conversation", lambda *a: None)
    token = new_csrf_token(client)
    sign_in(client, "student", user_id=501)

    response = client.post(
        "/messages/new",
        data={"csrf_token": token, "recipient_id": "12"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/messages")


def test_recipient_roles_for_students_are_staff_only():
    assert hub._allowed_recipient_roles("student") == ("admin", "teacher")
    assert "student" in hub._allowed_recipient_roles("teacher")


def test_fee_payment_rejects_invalid_amounts(client, monkeypatch):
    payments = []
    monkeypatch.setattr(hub, "record_fee_payment", lambda *a: payments.append(a) or True)
    token = new_csrf_token(client)
    sign_in(client, "admin")

    for amount in ("-5", "0", "abc", "1.001", "NaN", "100000000"):
        client.post(
            "/finance/fees/2/payment",
            data={"csrf_token": token, "amount": amount, "paid_on": TODAY.isoformat()},
        )
    client.post(
        "/finance/fees/2/payment",
        data={"csrf_token": token, "amount": "10",
              "paid_on": (TODAY + timedelta(days=3)).isoformat()},
    )
    assert payments == []

    client.post(
        "/finance/fees/2/payment",
        data={"csrf_token": token, "amount": "250.50", "paid_on": TODAY.isoformat()},
    )
    assert payments == [(2, Decimal("250.50"), TODAY, 7, "127.0.0.1")]


def test_student_assignment_status_is_restricted(client, monkeypatch):
    updates = []
    monkeypatch.setattr(hub, "set_student_assignment_status",
                        lambda *a: updates.append(a) or True)
    token = new_csrf_token(client)
    sign_in(client, "student", user_id=501)

    client.post("/assignments/4/status", data={"csrf_token": token, "status": "reviewed"})
    client.post("/assignments/4/status", data={"csrf_token": token, "status": "submitted"})

    assert updates == [(501, 4, "submitted")]


def test_profile_update_validates_fields(client, monkeypatch, fake_hub):
    saved = []
    monkeypatch.setattr(hub, "update_profile", lambda *a: saved.append(a))
    token = new_csrf_token(client)
    sign_in(client, "teacher")

    client.post("/profile", data={"csrf_token": token, "gender": "other"})
    client.post("/profile", data={"csrf_token": token, "phone": "call me"})
    client.post("/profile", data={"csrf_token": token,
                                  "date_of_birth": (TODAY + timedelta(days=1)).isoformat()})
    assert saved == []

    response = client.post("/profile", data={
        "csrf_token": token, "phone": "+233 24 000 0000", "gender": "female",
        "address": "Accra", "date_of_birth": "2000-02-01", "bio": "Hi",
    })
    assert response.status_code == 302
    assert saved[0][0] == 7
    assert saved[0][1]["gender"] == "female"
    assert saved[0][1]["date_of_birth"] == date(2000, 2, 1)


def test_tasks_are_scoped_to_the_signed_in_user(client, monkeypatch):
    toggles = []
    monkeypatch.setattr(hub, "toggle_task", lambda *a: toggles.append(a) or False)
    token = new_csrf_token(client)
    sign_in(client, "teacher", user_id=17)

    response = client.post("/tasks/99/toggle", data={"csrf_token": token})

    assert response.status_code == 404
    assert toggles == [(17, 99)]


def test_invalid_query_values_are_rejected(client, fake_hub):
    sign_in(client, "admin")

    assert client.get("/students?class_id=abc").status_code == 400
    assert client.get("/finance/fees?status=stolen").status_code == 400
    assert client.get("/calendar?day=2026-13-01").status_code == 400
    assert client.get("/attendance?start=yesterday").status_code == 400
    assert client.get("/teachers?q=" + "x" * 101).status_code == 400


def test_dashboards_render_widgets_for_all_roles(client, admin_dashboard, monkeypatch):
    from Routes import main, teacher

    monkeypatch.setattr(teacher.school, "get_teacher_dashboard",
                        lambda *a: {"term": 1, "classes": [], "attendance_trend": []})
    monkeypatch.setattr(main.school, "get_student_dashboard", lambda *a: {
        "student": {"id": 1, "student_number": "1010000001", "class_id": 3,
                    "class_name": "Year 10", "full_name": "Ama Owusu"},
        "term": 1, "attendance_counts": {"present": 3, "absent": 1, "late": 0},
        "attendance_percentage": 75.0, "recent_attendance": [],
        "results": [{"subject_id": 1, "subject_name": "History", "score": 82.0, "grade": "A"}],
        "term_averages": [{"term": 1, "average": 82.0}],
    })
    expected = {
        "admin": ("/admin", b"Earnings"),
        "teacher": ("/teacher", b"Student tasks"),
        "student": ("/student", b"Grade by subject"),
    }
    for role, (path, marker) in expected.items():
        sign_in(client, role)
        response = client.get(path)
        assert response.status_code == 200, role
        assert marker in response.data, role
        assert b"Agenda" in response.data and b"Messages" in response.data, role


def test_dashboard_links_all_resolve(client, admin_dashboard, monkeypatch, fake_hub):
    """Every link on each dashboard opens a page that role is allowed to see."""
    from html.parser import HTMLParser
    from urllib.parse import urlsplit

    from Routes import main, teacher

    class LinkParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links = set()

        def handle_starttag(self, tag, attrs):
            href = dict(attrs).get("href") if tag == "a" else None
            if href and not href.startswith("#"):
                self.links.add(href)

    monkeypatch.setattr(teacher.school, "get_teacher_dashboard", lambda *a: {
        "term": 1, "attendance_trend": [],
        "classes": [{"id": 3, "name": "Year 10", "subjects": [
            {"subject_id": 5, "subject_name": "History", "average": 70, "student_count": 2}]}],
    })
    monkeypatch.setattr(main.school, "get_student_dashboard", lambda *a: {
        "student": {"id": 1, "student_number": "1010000001", "class_id": 3,
                    "class_name": "Year 10", "full_name": "Ama Owusu"},
        "term": 1, "attendance_counts": {"present": 3, "absent": 1, "late": 0},
        "attendance_percentage": 75.0, "recent_attendance": [],
        "results": [{"subject_id": 1, "subject_name": "History", "score": 82.0, "grade": "A"}],
        "term_averages": [{"term": 1, "average": 82.0}],
    })
    # Replace the empty-widget fixture with populated widgets so list links render.
    monkeypatch.setattr(hub, "dashboard_widgets", lambda user_id, role, term, *a: {
        **hub.empty_widgets(role),
        "notices": hub.list_notices(role, "", 1)["rows"],
        "conversations": hub.list_conversations(user_id),
        "agenda": hub.events_between(role, TODAY, TODAY),
        **({"top_students": [{"full_name": "Ama Owusu", "class_name": "Year 10",
                             "average": 85.0, "grade": "A"}]} if role == "admin" else {}),
        **({"submissions": hub.teacher_submissions(user_id, "", 1)["rows"]}
           if role == "teacher" else {}),
        **({"assignments": hub.student_assignments(user_id, "", 1),
            "profile": hub.get_profile(user_id)} if role == "student" else {}),
    })
    for name in ("list_users", "get_audit_logs"):
        monkeypatch.setattr(f"Routes.admin.school.{name}", lambda *a, **k: {
            "users": [], "logs": [], "total": 0, "page": 1, "per_page": 20})
    monkeypatch.setattr("Routes.admin.school.get_admin_results_export_rows", lambda *a: [])
    monkeypatch.setattr("Routes.main.school.get_student_results_export_rows", lambda *a: [])
    monkeypatch.setattr("Routes.main.school.get_student_attendance_export_rows", lambda *a: [])
    monkeypatch.setattr("Routes.admin.school.get_admin_attendance_export_rows", lambda *a: [])
    monkeypatch.setattr("Routes.admin.school.get_admin_audit_export_rows", lambda *a: [])
    monkeypatch.setattr("Routes.teacher.school.get_class_roster_for_attendance",
                        lambda *a: [])
    monkeypatch.setattr("Routes.teacher.school.get_class_roster_for_results", lambda *a: [])
    monkeypatch.setattr("Routes.admin.accounts.list_pending_signups", lambda: [])
    monkeypatch.setattr("Routes.admin.accounts.list_password_reset_requests", lambda: [])

    for role, path in (("admin", "/admin"), ("teacher", "/teacher"), ("student", "/student")):
        sign_in(client, role)
        response = client.get(path)
        assert response.status_code == 200, role
        parser = LinkParser()
        parser.feed(response.get_data(as_text=True))
        app_links = {href for href in parser.links if urlsplit(href).netloc == ""}
        assert app_links, role
        for href in sorted(app_links):
            if "/Static/" in href:
                continue
            status = client.get(href, follow_redirects=True).status_code
            assert status == 200, (role, href, status)


def test_gpa_uses_grade_points():
    assert hub.gpa_from_results([]) is None
    assert hub.gpa_from_results([{"score": 85}, {"score": 72}, {"score": 40}]) == 2.33


def test_month_grid_starts_on_sunday_and_places_events():
    event = {"event_date": date(2030, 5, 8), "title": "Fair"}
    grid = hub.month_grid(2030, 5, [event])

    assert grid["weeks"][0][0]["date"].weekday() == 6
    days = [day for week in grid["weeks"] for day in week]
    assert next(d for d in days if d["date"] == date(2030, 5, 8))["events"] == [event]
    assert grid["previous"] == date(2030, 4, 1)
    assert grid["next"] == date(2030, 6, 1)


def test_like_pattern_escapes_wildcards():
    assert hub._like("50%_off\\") == "%50\\%\\_off\\\\%"
