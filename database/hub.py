"""Data access for the school hub: people lists, attendance grid, finance,
notices, calendar, library, messages, assignments, tasks and profiles.

Every function that acts for a signed-in user takes that user's id and role
from the session and checks access in SQL, so identifiers sent by the browser
are never trusted on their own.
"""
import calendar
import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import psycopg

from database.db import connect_database, record_audit_event
from grading import grade_for


logger = logging.getLogger(__name__)

PER_PAGE = 10
ROLES = ("admin", "teacher", "student")
AUDIENCES = ("all", "teachers", "students")
EVENT_CATEGORIES = ("event", "meeting", "exam", "holiday")
BOOK_STATUSES = ("available", "checked_out")
FEE_STATUSES = ("paid", "pending", "overdue")
STUDENT_TASK_STATUSES = ("not_started", "in_progress", "submitted")
SUBMISSION_STATUSES = ("not_started", "in_progress", "submitted", "reviewed")
GPA_POINTS = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}
MONTH_LABELS = [calendar.month_abbr[number] for number in range(1, 13)]


def _like(search: str) -> str:
    escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _page(total: int, page: int, per_page: int = PER_PAGE) -> dict[str, int]:
    pages = max(1, -(-total // per_page))
    return {"total": total, "page": page, "pages": pages, "per_page": per_page}


def _audience_for(role: str) -> tuple[str, ...]:
    """Audiences a role may see."""
    if role == "admin":
        return AUDIENCES
    return ("all", "teachers") if role == "teacher" else ("all", "students")


def _teacher_class_ids(cursor, teacher_id: int) -> list[int]:
    cursor.execute(
        """
        SELECT id FROM classes WHERE class_teacher_id = %s
        UNION
        SELECT class_id FROM teacher_assignments WHERE teacher_id = %s
        """,
        (teacher_id, teacher_id),
    )
    return [row["id"] for row in cursor.fetchall()]


# --------------------------------------------------------------------------
# People
# --------------------------------------------------------------------------

def list_teachers(search: str, page: int) -> dict[str, Any]:
    pattern = _like(search)
    where = """
        u.role = 'teacher'
        AND (%(search)s = ''
             OR u.full_name ILIKE %(pattern)s
             OR u.email ILIKE %(pattern)s
             OR EXISTS (
                 SELECT 1 FROM teacher_assignments ta2
                 JOIN subjects s2 ON s2.id = ta2.subject_id
                 WHERE ta2.teacher_id = u.id AND s2.name ILIKE %(pattern)s))
    """
    params = {"search": search, "pattern": pattern,
              "limit": PER_PAGE, "offset": (page - 1) * PER_PAGE}
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) AS count FROM users u WHERE {where}", params)
            total = cursor.fetchone()["count"]
            cursor.execute(
                f"""
                SELECT u.id, u.full_name, u.email, u.is_active, p.phone, p.address,
                       COALESCE(string_agg(DISTINCT s.name, ', '), '') AS subjects,
                       COALESCE(string_agg(DISTINCT c.name, ', '), '') AS classes
                FROM users u
                LEFT JOIN user_profiles p ON p.user_id = u.id
                LEFT JOIN teacher_assignments ta ON ta.teacher_id = u.id
                LEFT JOIN subjects s ON s.id = ta.subject_id
                LEFT JOIN classes c ON c.id = ta.class_id
                WHERE {where}
                GROUP BY u.id, p.phone, p.address
                ORDER BY u.full_name, u.id
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                params,
            )
            rows = cursor.fetchall()
    return {"rows": rows, **_page(total, page)}


def list_students(
    user_id: int,
    role: str,
    search: str,
    class_id: int | None,
    page: int,
) -> dict[str, Any]:
    pattern = _like(search)
    with connect_database() as connection:
        with connection.cursor() as cursor:
            scope = None if role == "admin" else _teacher_class_ids(cursor, user_id)
            cursor.execute(
                "SELECT id, name FROM classes WHERE (%s::bigint[] IS NULL OR id = ANY(%s)) ORDER BY id",
                (scope, scope),
            )
            classes = cursor.fetchall()
            params = {
                "scope": scope, "class_id": class_id, "search": search,
                "pattern": pattern, "limit": PER_PAGE,
                "offset": (page - 1) * PER_PAGE,
            }
            where = """
                (%(scope)s::bigint[] IS NULL OR st.class_id = ANY(%(scope)s))
                AND (%(class_id)s::bigint IS NULL OR st.class_id = %(class_id)s)
                AND (%(search)s = '' OR u.full_name ILIKE %(pattern)s
                     OR st.student_number ILIKE %(pattern)s OR u.email ILIKE %(pattern)s)
            """
            cursor.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM students st JOIN users u ON u.id = st.user_id
                WHERE {where}
                """,
                params,
            )
            total = cursor.fetchone()["count"]
            cursor.execute(
                f"""
                SELECT u.id AS user_id, u.full_name, u.email, u.is_active,
                       st.id, st.student_number, c.name AS class_name,
                       p.date_of_birth, p.phone, p.address, p.gender
                FROM students st
                JOIN users u ON u.id = st.user_id
                JOIN classes c ON c.id = st.class_id
                LEFT JOIN user_profiles p ON p.user_id = u.id
                WHERE {where}
                ORDER BY u.full_name, st.id
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                params,
            )
            rows = cursor.fetchall()
    return {"rows": rows, "classes": classes, **_page(total, page)}


# --------------------------------------------------------------------------
# Attendance grid
# --------------------------------------------------------------------------

def attendance_grid(
    user_id: int,
    role: str,
    class_id: int | None,
    start: date,
    days: int = 14,
) -> dict[str, Any] | None:
    """Attendance for a run of dates. Returns None if the class is out of scope."""
    dates = [start + timedelta(days=offset) for offset in range(days)]
    end = dates[-1]
    with connect_database() as connection:
        with connection.cursor() as cursor:
            if role == "student":
                cursor.execute(
                    """
                    SELECT st.id, u.full_name, st.class_id, c.name AS class_name
                    FROM students st
                    JOIN users u ON u.id = st.user_id
                    JOIN classes c ON c.id = st.class_id
                    WHERE st.user_id = %s
                    """,
                    (user_id,),
                )
                student = cursor.fetchone()
                if student is None:
                    return None
                classes = [{"id": student["class_id"], "name": student["class_name"]}]
                class_id = student["class_id"]
                roster = [student]
            else:
                scope = None if role == "admin" else _teacher_class_ids(cursor, user_id)
                cursor.execute(
                    """
                    SELECT id, name FROM classes
                    WHERE (%s::bigint[] IS NULL OR id = ANY(%s))
                    ORDER BY id
                    """,
                    (scope, scope),
                )
                classes = cursor.fetchall()
                if not classes:
                    return {"classes": [], "class_id": None, "dates": dates, "rows": [],
                            "can_mark": False}
                if class_id is None:
                    class_id = classes[0]["id"]
                if class_id not in {item["id"] for item in classes}:
                    return None
                cursor.execute(
                    """
                    SELECT st.id, u.full_name
                    FROM students st JOIN users u ON u.id = st.user_id
                    WHERE st.class_id = %s
                    ORDER BY u.full_name
                    """,
                    (class_id,),
                )
                roster = cursor.fetchall()
            cursor.execute(
                """
                SELECT student_id, attendance_date, status
                FROM attendance
                WHERE student_id = ANY(%s) AND attendance_date BETWEEN %s AND %s
                """,
                ([row["id"] for row in roster] or [0], start, end),
            )
            marks: dict[tuple[int, date], str] = {
                (row["student_id"], row["attendance_date"]): row["status"]
                for row in cursor.fetchall()
            }
            can_mark = False
            if role == "teacher":
                cursor.execute(
                    "SELECT 1 FROM classes WHERE id = %s AND class_teacher_id = %s",
                    (class_id, user_id),
                )
                can_mark = cursor.fetchone() is not None
    rows = [
        {
            "full_name": student["full_name"],
            "cells": [marks.get((student["id"], day)) for day in dates],
        }
        for student in roster
    ]
    return {"classes": classes, "class_id": class_id, "dates": dates, "rows": rows,
            "can_mark": can_mark}


# --------------------------------------------------------------------------
# Finance
# --------------------------------------------------------------------------

_FEE_SELECT = """
    SELECT f.id, f.term, f.tuition_fee, f.activities_fee, f.misc_fee, f.amount_paid,
           f.due_date,
           f.tuition_fee + f.activities_fee + f.misc_fee AS total,
           CASE
               WHEN f.amount_paid >= f.tuition_fee + f.activities_fee + f.misc_fee THEN 'paid'
               WHEN f.due_date < CURRENT_DATE THEN 'overdue'
               ELSE 'pending'
           END AS status,
           st.id AS student_id, st.student_number, st.class_id,
           c.name AS class_name, u.full_name
    FROM fee_records f
    JOIN students st ON st.id = f.student_id
    JOIN users u ON u.id = st.user_id
    JOIN classes c ON c.id = st.class_id
"""


def list_fees(
    search: str,
    class_id: int | None,
    status: str,
    term: int | None,
    page: int,
) -> dict[str, Any]:
    params = {
        "search": search, "pattern": _like(search), "class_id": class_id,
        "status": status, "term": term, "limit": PER_PAGE,
        "offset": (page - 1) * PER_PAGE,
    }
    filtered = f"""
        WITH fees AS ({_FEE_SELECT})
        SELECT * FROM fees
        WHERE (%(search)s = '' OR full_name ILIKE %(pattern)s
               OR student_number ILIKE %(pattern)s)
          AND (%(class_id)s::bigint IS NULL OR class_id = %(class_id)s)
          AND (%(status)s = '' OR status = %(status)s)
          AND (%(term)s::smallint IS NULL OR term = %(term)s)
    """
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) AS count FROM ({filtered}) t", params)
            total = cursor.fetchone()["count"]
            cursor.execute(
                f"{filtered} ORDER BY due_date DESC, full_name LIMIT %(limit)s OFFSET %(offset)s",
                params,
            )
            rows = cursor.fetchall()
            cursor.execute("SELECT id, name FROM classes ORDER BY id")
            classes = cursor.fetchall()
            cursor.execute(
                """
                SELECT st.id, u.full_name, st.student_number, c.name AS class_name
                FROM students st JOIN users u ON u.id = st.user_id
                JOIN classes c ON c.id = st.class_id
                WHERE u.is_active
                ORDER BY u.full_name
                """
            )
            students = cursor.fetchall()
    return {"rows": rows, "classes": classes, "students": students, **_page(total, page)}


def fee_summary(year: int) -> dict[str, Any]:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COALESCE(SUM(tuition_fee + activities_fee + misc_fee), 0) AS total,
                       COALESCE(SUM(tuition_fee), 0) AS tuition,
                       COALESCE(SUM(activities_fee), 0) AS activities,
                       COALESCE(SUM(misc_fee), 0) AS misc,
                       COALESCE(SUM(amount_paid), 0) AS collected
                FROM fee_records
                """
            )
            totals = cursor.fetchone()
            monthly = _monthly_sums(cursor, "fee_payments", "amount", "paid_on", year)
    return {"totals": totals, "monthly": monthly, "year": year}


def _monthly_sums(cursor, table: str, amount: str, day: str, year: int) -> list[float]:
    # Table and column names are fixed literals chosen by the caller, never user input.
    cursor.execute(
        f"""
        SELECT EXTRACT(MONTH FROM {day})::int AS month, SUM({amount}) AS total
        FROM {table}
        WHERE {day} >= %s AND {day} < %s
        GROUP BY 1
        """,
        (date(year, 1, 1), date(year + 1, 1, 1)),
    )
    sums = [0.0] * 12
    for row in cursor.fetchall():
        sums[row["month"] - 1] = float(row["total"])
    return sums


def create_fee(
    student_id: int,
    term: int,
    tuition: Decimal,
    activities: Decimal,
    misc: Decimal,
    due_date: date,
    actor_id: int,
    ip_address: str | None,
) -> int:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO fee_records
                    (student_id, term, tuition_fee, activities_fee, misc_fee,
                     due_date, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (student_id, term, tuition, activities, misc, due_date, actor_id),
            )
            fee_id = cursor.fetchone()["id"]
            record_audit_event(actor_id, "fee_created", ip_address, connection=connection)
    return fee_id


def record_fee_payment(
    fee_id: int,
    amount: Decimal,
    paid_on: date,
    actor_id: int,
    ip_address: str | None,
) -> bool:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE fee_records
                SET amount_paid = amount_paid + %s
                WHERE id = %s
                  AND amount_paid + %s <= tuition_fee + activities_fee + misc_fee
                RETURNING id
                """,
                (amount, fee_id, amount),
            )
            if cursor.fetchone() is None:
                return False
            cursor.execute(
                """
                INSERT INTO fee_payments (fee_id, amount, paid_on, recorded_by)
                VALUES (%s, %s, %s, %s)
                """,
                (fee_id, amount, paid_on, actor_id),
            )
            record_audit_event(actor_id, "fee_payment_recorded", ip_address,
                               connection=connection)
    return True


def delete_fee(fee_id: int, actor_id: int, ip_address: str | None) -> bool:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM fee_records WHERE id = %s RETURNING id", (fee_id,))
            if cursor.fetchone() is None:
                return False
            record_audit_event(actor_id, "fee_deleted", ip_address, connection=connection)
    return True


def student_fees(user_id: int) -> list[dict[str, Any]]:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"{_FEE_SELECT} WHERE st.user_id = %s ORDER BY f.term",
                (user_id,),
            )
            return cursor.fetchall()


def list_expenses(
    search: str,
    category: str,
    month: date | None,
    page: int,
) -> dict[str, Any]:
    month_end = (
        (month.replace(day=28) + timedelta(days=4)).replace(day=1) if month else None
    )
    params = {
        "search": search, "pattern": _like(search), "category": category,
        "start": month, "end": month_end, "limit": PER_PAGE,
        "offset": (page - 1) * PER_PAGE,
    }
    where = """
        (%(search)s = '' OR description ILIKE %(pattern)s OR category ILIKE %(pattern)s
         OR ('EX' || lpad(id::text, 2, '0')) ILIKE %(pattern)s)
        AND (%(category)s = '' OR category = %(category)s)
        AND (%(start)s::date IS NULL OR paid_on >= %(start)s)
        AND (%(end)s::date IS NULL OR paid_on < %(end)s)
    """
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) AS count, COALESCE(SUM(amount), 0) AS sum "
                           f"FROM expenses WHERE {where}", params)
            counts = cursor.fetchone()
            cursor.execute(
                f"""
                SELECT id, category, description, quantity, amount, paid_on
                FROM expenses WHERE {where}
                ORDER BY paid_on DESC, id DESC
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                params,
            )
            rows = cursor.fetchall()
            cursor.execute("SELECT DISTINCT category FROM expenses ORDER BY category")
            categories = [row["category"] for row in cursor.fetchall()]
    return {"rows": rows, "categories": categories, "sum": counts["sum"],
            **_page(counts["count"], page)}


def create_expense(
    category: str,
    description: str,
    quantity: str,
    amount: Decimal,
    paid_on: date,
    actor_id: int,
    ip_address: str | None,
) -> None:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO expenses
                    (category, description, quantity, amount, paid_on, recorded_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (category, description, quantity, amount, paid_on, actor_id),
            )
            record_audit_event(actor_id, "expense_created", ip_address,
                               connection=connection)


def delete_expense(expense_id: int, actor_id: int, ip_address: str | None) -> bool:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM expenses WHERE id = %s RETURNING id", (expense_id,))
            if cursor.fetchone() is None:
                return False
            record_audit_event(actor_id, "expense_deleted", ip_address,
                               connection=connection)
    return True


# --------------------------------------------------------------------------
# Notices
# --------------------------------------------------------------------------

def list_notices(role: str, search: str, page: int, per_page: int = 5) -> dict[str, Any]:
    params = {
        "audiences": list(_audience_for(role)), "search": search,
        "pattern": _like(search), "limit": per_page,
        "offset": (page - 1) * per_page,
    }
    where = """
        n.audience = ANY(%(audiences)s)
        AND (%(search)s = '' OR n.title ILIKE %(pattern)s OR u.full_name ILIKE %(pattern)s)
    """
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""SELECT COUNT(*) AS count FROM notices n
                    JOIN users u ON u.id = n.author_id WHERE {where}""",
                params,
            )
            total = cursor.fetchone()["count"]
            cursor.execute(
                f"""
                SELECT n.id, n.title, n.body, n.audience, n.tags, n.created_at,
                       n.author_id, u.full_name AS author,
                       (SELECT COUNT(*) FROM notice_views v WHERE v.notice_id = n.id) AS views
                FROM notices n JOIN users u ON u.id = n.author_id
                WHERE {where}
                ORDER BY n.created_at DESC, n.id DESC
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                params,
            )
            rows = cursor.fetchall()
    return {"rows": rows, **_page(total, page, per_page)}


def get_notice(notice_id: int, user_id: int, role: str) -> dict[str, Any] | None:
    """Fetch a notice the user may see and count their view once."""
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT n.id, n.title, n.body, n.audience, n.tags, n.created_at,
                       n.author_id, u.full_name AS author
                FROM notices n JOIN users u ON u.id = n.author_id
                WHERE n.id = %s AND n.audience = ANY(%s)
                """,
                (notice_id, list(_audience_for(role))),
            )
            notice = cursor.fetchone()
            if notice is None:
                return None
            cursor.execute(
                """
                INSERT INTO notice_views (notice_id, user_id) VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                """,
                (notice_id, user_id),
            )
            cursor.execute(
                "SELECT COUNT(*) AS views FROM notice_views WHERE notice_id = %s",
                (notice_id,),
            )
            notice["views"] = cursor.fetchone()["views"]
    return notice


def create_notice(
    title: str,
    body: str,
    audience: str,
    tags: str,
    author_id: int,
    ip_address: str | None,
) -> int:
    if audience not in AUDIENCES:
        raise ValueError("Choose a valid audience.")
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO notices (title, body, audience, tags, author_id)
                VALUES (%s, %s, %s, %s, %s) RETURNING id
                """,
                (title, body, audience, tags, author_id),
            )
            notice_id = cursor.fetchone()["id"]
            record_audit_event(author_id, "notice_created", ip_address,
                               connection=connection)
    return notice_id


def delete_notice(notice_id: int, user_id: int, role: str, ip_address: str | None) -> bool:
    """Admins may delete any notice; authors may delete their own."""
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM notices
                WHERE id = %s AND (%s = 'admin' OR author_id = %s)
                RETURNING id
                """,
                (notice_id, role, user_id),
            )
            if cursor.fetchone() is None:
                return False
            record_audit_event(user_id, "notice_deleted", ip_address,
                               connection=connection)
    return True


def unread_notice_count(user_id: int, role: str) -> int:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) AS count FROM notices n
                WHERE n.audience = ANY(%s)
                  AND n.created_at > CURRENT_TIMESTAMP - INTERVAL '30 days'
                  AND NOT EXISTS (SELECT 1 FROM notice_views v
                                  WHERE v.notice_id = n.id AND v.user_id = %s)
                """,
                (list(_audience_for(role)), user_id),
            )
            return cursor.fetchone()["count"]


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------

def events_between(role: str, start: date, end: date) -> list[dict[str, Any]]:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT e.id, e.title, e.description, e.event_date, e.start_time,
                       e.end_time, e.category, e.audience, e.created_by,
                       u.full_name AS creator
                FROM events e JOIN users u ON u.id = e.created_by
                WHERE e.event_date BETWEEN %s AND %s AND e.audience = ANY(%s)
                ORDER BY e.event_date, e.start_time NULLS FIRST, e.id
                """,
                (start, end, list(_audience_for(role))),
            )
            return cursor.fetchall()


def create_event(
    title: str,
    description: str,
    event_date: date,
    start_time: time | None,
    end_time: time | None,
    category: str,
    audience: str,
    actor_id: int,
    ip_address: str | None,
) -> None:
    if category not in EVENT_CATEGORIES or audience not in AUDIENCES:
        raise ValueError("Choose a valid category and audience.")
    if start_time and end_time and end_time < start_time:
        raise ValueError("The end time must be after the start time.")
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO events (title, description, event_date, start_time,
                                    end_time, category, audience, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (title, description, event_date, start_time, end_time, category,
                 audience, actor_id),
            )
            record_audit_event(actor_id, "event_created", ip_address,
                               connection=connection)


def delete_event(event_id: int, user_id: int, role: str, ip_address: str | None) -> bool:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM events
                WHERE id = %s AND (%s = 'admin' OR created_by = %s)
                RETURNING id
                """,
                (event_id, role, user_id),
            )
            if cursor.fetchone() is None:
                return False
            record_audit_event(user_id, "event_deleted", ip_address,
                               connection=connection)
    return True


def month_grid(year: int, month: int, events: list[dict[str, Any]] | None = None,
               selected: date | None = None) -> dict[str, Any]:
    """A Sunday-first month grid of weeks, with each day's events attached."""
    by_day: dict[date, list[dict[str, Any]]] = {}
    for event in events or []:
        by_day.setdefault(event["event_date"], []).append(event)
    weeks = []
    for week in calendar.Calendar(firstweekday=6).monthdatescalendar(year, month):
        weeks.append([
            {"date": day, "in_month": day.month == month,
             "events": by_day.get(day, [])}
            for day in week
        ])
    first = date(year, month, 1)
    previous_month = (first - timedelta(days=1)).replace(day=1)
    next_month = (first + timedelta(days=32)).replace(day=1)
    return {
        "year": year, "month": month, "label": first.strftime("%B %Y"),
        "weeks": weeks, "previous": previous_month, "next": next_month,
        "today": date.today(), "selected": selected,
    }


# --------------------------------------------------------------------------
# Library
# --------------------------------------------------------------------------

def list_books(search: str, status: str, page: int) -> dict[str, Any]:
    params = {"search": search, "pattern": _like(search), "status": status,
              "limit": PER_PAGE, "offset": (page - 1) * PER_PAGE}
    where = """
        (%(search)s = '' OR code ILIKE %(pattern)s OR title ILIKE %(pattern)s
         OR subject ILIKE %(pattern)s OR author ILIKE %(pattern)s)
        AND (%(status)s = '' OR status = %(status)s)
    """
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) AS count FROM books WHERE {where}", params)
            total = cursor.fetchone()["count"]
            cursor.execute(
                f"""
                SELECT id, code, title, author, subject, classes, publish_year, status
                FROM books WHERE {where}
                ORDER BY code LIMIT %(limit)s OFFSET %(offset)s
                """,
                params,
            )
            rows = cursor.fetchall()
    return {"rows": rows, **_page(total, page)}


def create_book(values: dict[str, Any], actor_id: int, ip_address: str | None) -> None:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO books (code, title, author, subject, classes, publish_year)
                VALUES (%(code)s, %(title)s, %(author)s, %(subject)s, %(classes)s,
                        %(publish_year)s)
                """,
                values,
            )
            record_audit_event(actor_id, "book_created", ip_address,
                               connection=connection)


def set_book_status(book_id: int, status: str, actor_id: int, ip_address: str | None) -> bool:
    if status not in BOOK_STATUSES:
        raise ValueError("Choose a valid status.")
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE books SET status = %s WHERE id = %s RETURNING id",
                (status, book_id),
            )
            if cursor.fetchone() is None:
                return False
            record_audit_event(actor_id, "book_status_changed", ip_address,
                               connection=connection)
    return True


def delete_book(book_id: int, actor_id: int, ip_address: str | None) -> bool:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM books WHERE id = %s RETURNING id", (book_id,))
            if cursor.fetchone() is None:
                return False
            record_audit_event(actor_id, "book_deleted", ip_address, connection=connection)
    return True


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------

def list_conversations(user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.id, c.is_group,
                       COALESCE(c.title, other.full_name, 'Conversation') AS title,
                       other.role AS other_role,
                       last.body AS last_body, last.created_at AS last_at,
                       sender.full_name AS last_sender,
                       (SELECT COUNT(*) FROM messages m
                        WHERE m.conversation_id = c.id AND m.sender_id <> %(user)s
                          AND m.created_at > me.last_read_at) AS unread
                FROM conversation_members me
                JOIN conversations c ON c.id = me.conversation_id
                LEFT JOIN LATERAL (
                    SELECT u.full_name, u.role FROM conversation_members cm
                    JOIN users u ON u.id = cm.user_id
                    WHERE cm.conversation_id = c.id AND cm.user_id <> %(user)s
                    ORDER BY u.full_name LIMIT 1
                ) other ON NOT c.is_group
                LEFT JOIN LATERAL (
                    SELECT body, created_at, sender_id FROM messages m
                    WHERE m.conversation_id = c.id
                    ORDER BY created_at DESC, id DESC LIMIT 1
                ) last ON TRUE
                LEFT JOIN users sender ON sender.id = last.sender_id
                WHERE me.user_id = %(user)s
                ORDER BY COALESCE(last.created_at, c.created_at) DESC
                LIMIT %(limit)s
                """,
                {"user": user_id, "limit": limit},
            )
            return cursor.fetchall()


def get_conversation(conversation_id: int, user_id: int) -> dict[str, Any] | None:
    """Messages and members for a conversation the user belongs to; marks it read."""
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.id, c.title, c.is_group, c.description, c.created_at
                FROM conversations c
                JOIN conversation_members me
                  ON me.conversation_id = c.id AND me.user_id = %s
                WHERE c.id = %s
                """,
                (user_id, conversation_id),
            )
            conversation = cursor.fetchone()
            if conversation is None:
                return None
            cursor.execute(
                """
                SELECT u.id, u.full_name, u.role
                FROM conversation_members cm JOIN users u ON u.id = cm.user_id
                WHERE cm.conversation_id = %s ORDER BY u.full_name
                """,
                (conversation_id,),
            )
            conversation["members"] = cursor.fetchall()
            cursor.execute(
                """
                SELECT * FROM (
                    SELECT m.id, m.body, m.created_at, m.sender_id,
                           u.full_name AS sender, u.role AS sender_role
                    FROM messages m JOIN users u ON u.id = m.sender_id
                    WHERE m.conversation_id = %s
                    ORDER BY m.created_at DESC, m.id DESC LIMIT 100
                ) recent ORDER BY created_at, id
                """,
                (conversation_id,),
            )
            conversation["messages"] = cursor.fetchall()
            cursor.execute(
                """
                UPDATE conversation_members SET last_read_at = CURRENT_TIMESTAMP
                WHERE conversation_id = %s AND user_id = %s
                """,
                (conversation_id, user_id),
            )
    if not conversation["is_group"]:
        others = [m for m in conversation["members"] if m["id"] != user_id]
        conversation["title"] = others[0]["full_name"] if others else "Conversation"
    return conversation


def send_message(conversation_id: int, user_id: int, body: str) -> bool:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO messages (conversation_id, sender_id, body)
                SELECT %s, %s, %s
                WHERE EXISTS (SELECT 1 FROM conversation_members
                              WHERE conversation_id = %s AND user_id = %s)
                RETURNING id
                """,
                (conversation_id, user_id, body, conversation_id, user_id),
            )
            if cursor.fetchone() is None:
                return False
            cursor.execute(
                """
                UPDATE conversation_members SET last_read_at = CURRENT_TIMESTAMP
                WHERE conversation_id = %s AND user_id = %s
                """,
                (conversation_id, user_id),
            )
    return True


def _allowed_recipient_roles(role: str) -> tuple[str, ...]:
    # Students may only start conversations with staff.
    return ROLES if role in ("admin", "teacher") else ("admin", "teacher")


def message_recipients(user_id: int, role: str) -> list[dict[str, Any]]:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, full_name, role FROM users
                WHERE is_active AND id <> %s AND role = ANY(%s)
                ORDER BY role, full_name
                LIMIT 500
                """,
                (user_id, list(_allowed_recipient_roles(role))),
            )
            return cursor.fetchall()


def start_direct_conversation(user_id: int, role: str, other_id: int) -> int | None:
    """Reuse or create a one-to-one conversation. None if the recipient is not allowed."""
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM users WHERE id = %s AND id <> %s AND is_active AND role = ANY(%s)",
                (other_id, user_id, list(_allowed_recipient_roles(role))),
            )
            if cursor.fetchone() is None:
                return None
            cursor.execute(
                """
                SELECT c.id FROM conversations c
                JOIN conversation_members a ON a.conversation_id = c.id AND a.user_id = %s
                JOIN conversation_members b ON b.conversation_id = c.id AND b.user_id = %s
                WHERE NOT c.is_group LIMIT 1
                """,
                (user_id, other_id),
            )
            existing = cursor.fetchone()
            if existing:
                return existing["id"]
            cursor.execute(
                "INSERT INTO conversations (is_group, created_by) VALUES (FALSE, %s) RETURNING id",
                (user_id,),
            )
            conversation_id = cursor.fetchone()["id"]
            cursor.executemany(
                "INSERT INTO conversation_members (conversation_id, user_id) VALUES (%s, %s)",
                [(conversation_id, user_id), (conversation_id, other_id)],
            )
    return conversation_id


def create_group(
    user_id: int,
    role: str,
    title: str,
    description: str,
    member_ids: list[int],
    ip_address: str | None,
) -> int:
    if role not in ("admin", "teacher"):
        raise PermissionError("Only staff can create groups.")
    members = sorted(set(member_ids) - {user_id})
    if not members or len(members) > 200:
        raise ValueError("Choose between 1 and 200 members.")
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM users WHERE id = ANY(%s) AND is_active",
                (members,),
            )
            if len(cursor.fetchall()) != len(members):
                raise ValueError("Choose active members only.")
            cursor.execute(
                """
                INSERT INTO conversations (title, is_group, description, created_by)
                VALUES (%s, TRUE, %s, %s) RETURNING id
                """,
                (title, description, user_id),
            )
            conversation_id = cursor.fetchone()["id"]
            cursor.executemany(
                "INSERT INTO conversation_members (conversation_id, user_id) VALUES (%s, %s)",
                [(conversation_id, member) for member in [user_id, *members]],
            )
            record_audit_event(user_id, "message_group_created", ip_address,
                               connection=connection)
    return conversation_id


def unread_message_count(user_id: int) -> int:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM conversation_members me
                JOIN messages m ON m.conversation_id = me.conversation_id
                WHERE me.user_id = %s AND m.sender_id <> %s
                  AND m.created_at > me.last_read_at
                """,
                (user_id, user_id),
            )
            return cursor.fetchone()["count"]


# --------------------------------------------------------------------------
# Assignments
# --------------------------------------------------------------------------

def teacher_assignment_options(teacher_id: int) -> list[dict[str, Any]]:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT ta.class_id, ta.subject_id, c.name AS class_name,
                       s.name AS subject_name
                FROM teacher_assignments ta
                JOIN classes c ON c.id = ta.class_id
                JOIN subjects s ON s.id = ta.subject_id
                WHERE ta.teacher_id = %s
                ORDER BY c.id, s.name
                """,
                (teacher_id,),
            )
            return cursor.fetchall()


def create_assignment(
    teacher_id: int,
    class_id: int,
    subject_id: int,
    title: str,
    description: str,
    due_at: datetime,
    ip_address: str | None,
) -> bool:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO assignments
                    (teacher_id, class_id, subject_id, title, description, due_at)
                SELECT %s, %s, %s, %s, %s, %s
                WHERE EXISTS (SELECT 1 FROM teacher_assignments
                              WHERE teacher_id = %s AND class_id = %s AND subject_id = %s)
                RETURNING id
                """,
                (teacher_id, class_id, subject_id, title, description, due_at,
                 teacher_id, class_id, subject_id),
            )
            if cursor.fetchone() is None:
                return False
            record_audit_event(teacher_id, "assignment_created", ip_address,
                               connection=connection)
    return True


def delete_assignment(assignment_id: int, teacher_id: int, ip_address: str | None) -> bool:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM assignments WHERE id = %s AND teacher_id = %s RETURNING id",
                (assignment_id, teacher_id),
            )
            if cursor.fetchone() is None:
                return False
            record_audit_event(teacher_id, "assignment_deleted", ip_address,
                               connection=connection)
    return True


def list_teacher_assignments(teacher_id: int) -> list[dict[str, Any]]:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT a.id, a.title, a.description, a.due_at, c.name AS class_name,
                       s.name AS subject_name,
                       (SELECT COUNT(*) FROM students st WHERE st.class_id = a.class_id)
                           AS class_size,
                       (SELECT COUNT(*) FROM assignment_submissions sub
                        WHERE sub.assignment_id = a.id
                          AND sub.status IN ('submitted', 'reviewed')) AS handed_in
                FROM assignments a
                JOIN classes c ON c.id = a.class_id
                JOIN subjects s ON s.id = a.subject_id
                WHERE a.teacher_id = %s
                ORDER BY a.due_at DESC
                LIMIT 100
                """,
                (teacher_id,),
            )
            return cursor.fetchall()


def teacher_submissions(teacher_id: int, search: str, page: int,
                        per_page: int = PER_PAGE) -> dict[str, Any]:
    """Every student's status on each of the teacher's assignments."""
    params = {"teacher": teacher_id, "search": search, "pattern": _like(search),
              "limit": per_page, "offset": (page - 1) * per_page}
    base = """
        FROM assignments a
        JOIN students st ON st.class_id = a.class_id
        JOIN users u ON u.id = st.user_id
        JOIN classes c ON c.id = a.class_id
        JOIN subjects s ON s.id = a.subject_id
        LEFT JOIN assignment_submissions sub
          ON sub.assignment_id = a.id AND sub.student_id = st.id
        WHERE a.teacher_id = %(teacher)s
          AND (%(search)s = '' OR u.full_name ILIKE %(pattern)s
               OR a.title ILIKE %(pattern)s OR s.name ILIKE %(pattern)s)
    """
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) AS count {base}", params)
            total = cursor.fetchone()["count"]
            cursor.execute(
                f"""
                SELECT a.id AS assignment_id, st.id AS student_id, u.full_name,
                       c.name AS class_name, s.name AS subject_name, a.title, a.due_at,
                       COALESCE(sub.status, 'not_started') AS status
                {base}
                ORDER BY a.due_at DESC, u.full_name
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                params,
            )
            rows = cursor.fetchall()
    return {"rows": rows, **_page(total, page, per_page)}


def review_submission(teacher_id: int, assignment_id: int, student_id: int,
                      ip_address: str | None) -> bool:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO assignment_submissions (assignment_id, student_id, status)
                SELECT a.id, st.id, 'reviewed'
                FROM assignments a JOIN students st ON st.class_id = a.class_id
                WHERE a.id = %s AND a.teacher_id = %s AND st.id = %s
                ON CONFLICT (assignment_id, student_id)
                DO UPDATE SET status = 'reviewed', updated_at = CURRENT_TIMESTAMP
                RETURNING assignment_id
                """,
                (assignment_id, teacher_id, student_id),
            )
            if cursor.fetchone() is None:
                return False
            record_audit_event(teacher_id, "assignment_reviewed", ip_address,
                               connection=connection)
    return True


def student_assignments(user_id: int, search: str, page: int,
                        per_page: int = 5) -> dict[str, Any]:
    params = {"user": user_id, "search": search, "pattern": _like(search),
              "limit": per_page, "offset": (page - 1) * per_page}
    base = """
        FROM students st
        JOIN assignments a ON a.class_id = st.class_id
        JOIN subjects s ON s.id = a.subject_id
        LEFT JOIN assignment_submissions sub
          ON sub.assignment_id = a.id AND sub.student_id = st.id
        WHERE st.user_id = %(user)s
          AND (%(search)s = '' OR s.name ILIKE %(pattern)s OR a.title ILIKE %(pattern)s)
    """
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT COUNT(*) AS count,
                       COUNT(*) FILTER (WHERE sub.status IN ('submitted', 'reviewed')) AS done,
                       COUNT(*) FILTER (WHERE sub.status = 'in_progress') AS in_progress
                {base}
                """,
                params,
            )
            counts = cursor.fetchone()
            cursor.execute(
                f"""
                SELECT a.id, a.title, a.description, a.due_at, s.name AS subject_name,
                       COALESCE(sub.status, 'not_started') AS status
                {base}
                ORDER BY a.due_at DESC, a.id DESC
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                params,
            )
            rows = cursor.fetchall()
    return {"rows": rows, "done": counts["done"], "in_progress": counts["in_progress"],
            **_page(counts["count"], page, per_page)}


def set_student_assignment_status(user_id: int, assignment_id: int, status: str) -> bool:
    if status not in STUDENT_TASK_STATUSES:
        raise ValueError("Choose a valid status.")
    with connect_database() as connection:
        with connection.cursor() as cursor:
            # Reviewed work is locked; the student's class must match the assignment's.
            cursor.execute(
                """
                INSERT INTO assignment_submissions (assignment_id, student_id, status)
                SELECT a.id, st.id, %s
                FROM assignments a JOIN students st ON st.class_id = a.class_id
                WHERE a.id = %s AND st.user_id = %s
                ON CONFLICT (assignment_id, student_id)
                DO UPDATE SET status = EXCLUDED.status, updated_at = CURRENT_TIMESTAMP
                WHERE assignment_submissions.status <> 'reviewed'
                RETURNING assignment_id
                """,
                (status, assignment_id, user_id),
            )
            return cursor.fetchone() is not None


# --------------------------------------------------------------------------
# Personal tasks
# --------------------------------------------------------------------------

def list_tasks(user_id: int, search: str = "") -> list[dict[str, Any]]:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, title, due_date, done FROM user_tasks
                WHERE user_id = %s AND (%s = '' OR title ILIKE %s)
                ORDER BY done, due_date NULLS LAST, id
                LIMIT 50
                """,
                (user_id, search, _like(search)),
            )
            return cursor.fetchall()


def add_task(user_id: int, title: str, due_date: date | None) -> None:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO user_tasks (user_id, title, due_date) VALUES (%s, %s, %s)",
                (user_id, title, due_date),
            )


def toggle_task(user_id: int, task_id: int) -> bool:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE user_tasks SET done = NOT done WHERE id = %s AND user_id = %s RETURNING id",
                (task_id, user_id),
            )
            return cursor.fetchone() is not None


def delete_task(user_id: int, task_id: int) -> bool:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM user_tasks WHERE id = %s AND user_id = %s RETURNING id",
                (task_id, user_id),
            )
            return cursor.fetchone() is not None


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------

def get_profile(user_id: int) -> dict[str, Any] | None:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT u.id, u.full_name, u.email, u.role, u.created_at, u.mfa_enabled,
                       p.phone, p.address, p.date_of_birth, p.gender, p.bio,
                       st.student_number, c.name AS class_name
                FROM users u
                LEFT JOIN user_profiles p ON p.user_id = u.id
                LEFT JOIN students st ON st.user_id = u.id
                LEFT JOIN classes c ON c.id = st.class_id
                WHERE u.id = %s
                """,
                (user_id,),
            )
            profile = cursor.fetchone()
            if profile and profile["role"] == "teacher":
                cursor.execute(
                    """
                    SELECT c.name AS class_name, s.name AS subject_name
                    FROM teacher_assignments ta
                    JOIN classes c ON c.id = ta.class_id
                    JOIN subjects s ON s.id = ta.subject_id
                    WHERE ta.teacher_id = %s ORDER BY c.id, s.name
                    """,
                    (user_id,),
                )
                profile["teaching"] = cursor.fetchall()
    return profile


def update_profile(user_id: int, values: dict[str, Any], ip_address: str | None) -> None:
    if values.get("gender") not in (None, "female", "male"):
        raise ValueError("Choose a valid gender.")
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO user_profiles
                    (user_id, phone, address, date_of_birth, gender, bio, updated_at)
                VALUES (%(user_id)s, %(phone)s, %(address)s, %(date_of_birth)s,
                        %(gender)s, %(bio)s, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id) DO UPDATE SET
                    phone = EXCLUDED.phone, address = EXCLUDED.address,
                    date_of_birth = EXCLUDED.date_of_birth, gender = EXCLUDED.gender,
                    bio = EXCLUDED.bio, updated_at = CURRENT_TIMESTAMP
                """,
                {**values, "user_id": user_id},
            )
            record_audit_event(user_id, "profile_updated", ip_address,
                               connection=connection)


# --------------------------------------------------------------------------
# Dashboard widgets
# --------------------------------------------------------------------------

def empty_widgets(role: str) -> dict[str, Any]:
    """Widget data with nothing in it: used before the hub migration runs."""
    today = date.today()
    widgets: dict[str, Any] = {
        "available": False,
        "calendar": month_grid(today.year, today.month),
        "agenda": [],
        "notices": [],
        "conversations": [],
        "tasks": [],
    }
    if role == "admin":
        widgets.update({
            "staff": 0, "gender": {"male": 0, "female": 0, "unspecified": 0},
            "weekly_attendance": {"labels": ["Mon", "Tue", "Wed", "Thu", "Fri"],
                                  "present": [0] * 5, "absent": [0] * 5},
            "earnings": {"labels": MONTH_LABELS, "income": [0.0] * 12,
                         "expense": [0.0] * 12},
            "fees": {"collected": 0, "outstanding": 0},
            "books": {"total": 0, "checked_out": 0},
            "top_students": [],
        })
    elif role == "teacher":
        widgets.update({
            "student_count": 0, "subject_count": 0, "pending_reviews": 0,
            "today_attendance": {"present": 0, "absent": 0, "late": 0},
            "performance": {"labels": [], "datasets": []},
            "submissions": [],
        })
    else:
        widgets.update({
            "profile": None, "assignments": {"rows": [], "done": 0, "in_progress": 0,
                                             "total": 0, "page": 1, "pages": 1},
            "fees": [],
        })
    return widgets


def dashboard_widgets(user_id: int, role: str, term: int,
                      assignment_search: str = "", assignment_page: int = 1) -> dict[str, Any]:
    """Load the hub widgets, falling back to empty ones if the tables are missing."""
    try:
        return _load_widgets(user_id, role, term, assignment_search, assignment_page)
    except psycopg.errors.UndefinedTable:
        logger.warning("School hub tables are missing; run migration_school_hub.sql.")
        return empty_widgets(role)


def _load_widgets(user_id: int, role: str, term: int, assignment_search: str,
                  assignment_page: int) -> dict[str, Any]:
    widgets = empty_widgets(role)
    widgets["available"] = True
    today = date.today()
    month_start = today.replace(day=1)
    month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    month_events = events_between(role, month_start, month_end)
    widgets["calendar"] = month_grid(today.year, today.month, month_events, today)
    widgets["agenda"] = events_between(role, today, today + timedelta(days=30))[:4]
    widgets["notices"] = list_notices(role, "", 1, per_page=5)["rows"]
    widgets["conversations"] = list_conversations(user_id, limit=4)
    widgets["tasks"] = list_tasks(user_id)
    with connect_database() as connection:
        with connection.cursor() as cursor:
            if role == "admin":
                _admin_widgets(cursor, widgets, term, today)
            elif role == "teacher":
                _teacher_widgets(cursor, widgets, user_id, term, today)
    if role == "student":
        widgets["profile"] = get_profile(user_id)
        widgets["assignments"] = student_assignments(
            user_id, assignment_search, assignment_page
        )
        widgets["fees"] = student_fees(user_id)
    elif role == "teacher":
        widgets["submissions"] = teacher_submissions(user_id, "", 1, per_page=5)["rows"]
    return widgets


def _admin_widgets(cursor, widgets: dict[str, Any], term: int, today: date) -> None:
    cursor.execute("SELECT COUNT(*) AS count FROM users WHERE role = 'admin' AND is_active")
    widgets["staff"] = cursor.fetchone()["count"]
    cursor.execute(
        """
        SELECT COALESCE(p.gender, 'unspecified') AS gender, COUNT(*) AS count
        FROM students st LEFT JOIN user_profiles p ON p.user_id = st.user_id
        GROUP BY 1
        """
    )
    for row in cursor.fetchall():
        widgets["gender"][row["gender"]] = row["count"]
    monday = today - timedelta(days=today.weekday())
    cursor.execute(
        """
        SELECT attendance_date,
               COUNT(*) FILTER (WHERE status IN ('present', 'late')) AS present,
               COUNT(*) FILTER (WHERE status = 'absent') AS absent
        FROM attendance
        WHERE attendance_date BETWEEN %s AND %s
        GROUP BY attendance_date
        """,
        (monday, monday + timedelta(days=4)),
    )
    for row in cursor.fetchall():
        index = (row["attendance_date"] - monday).days
        widgets["weekly_attendance"]["present"][index] = row["present"]
        widgets["weekly_attendance"]["absent"][index] = row["absent"]
    widgets["earnings"]["income"] = _monthly_sums(
        cursor, "fee_payments", "amount", "paid_on", today.year
    )
    widgets["earnings"]["expense"] = _monthly_sums(
        cursor, "expenses", "amount", "paid_on", today.year
    )
    cursor.execute(
        """
        SELECT COALESCE(SUM(amount_paid), 0) AS collected,
               COALESCE(SUM(tuition_fee + activities_fee + misc_fee - amount_paid), 0)
                   AS outstanding
        FROM fee_records
        """
    )
    widgets["fees"] = cursor.fetchone()
    cursor.execute(
        """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE status = 'checked_out') AS checked_out
        FROM books
        """
    )
    widgets["books"] = cursor.fetchone()
    cursor.execute(
        """
        SELECT u.full_name, c.name AS class_name, AVG(r.score) AS average
        FROM results r
        JOIN students st ON st.id = r.student_id
        JOIN users u ON u.id = st.user_id
        JOIN classes c ON c.id = st.class_id
        WHERE r.term = %s
        GROUP BY u.full_name, c.name, st.id
        ORDER BY AVG(r.score) DESC
        LIMIT 4
        """,
        (term,),
    )
    widgets["top_students"] = [
        {**row, "average": float(row["average"]), "grade": grade_for(row["average"])}
        for row in cursor.fetchall()
    ]


def _teacher_widgets(cursor, widgets: dict[str, Any], teacher_id: int, term: int,
                     today: date) -> None:
    class_ids = _teacher_class_ids(cursor, teacher_id) or [0]
    cursor.execute(
        "SELECT COUNT(*) AS count FROM students WHERE class_id = ANY(%s)",
        (class_ids,),
    )
    widgets["student_count"] = cursor.fetchone()["count"]
    cursor.execute(
        "SELECT COUNT(DISTINCT subject_id) AS count FROM teacher_assignments WHERE teacher_id = %s",
        (teacher_id,),
    )
    widgets["subject_count"] = cursor.fetchone()["count"]
    cursor.execute(
        """
        SELECT COUNT(*) AS count FROM assignment_submissions sub
        JOIN assignments a ON a.id = sub.assignment_id
        WHERE a.teacher_id = %s AND sub.status = 'submitted'
        """,
        (teacher_id,),
    )
    widgets["pending_reviews"] = cursor.fetchone()["count"]
    cursor.execute(
        """
        SELECT a.status, COUNT(*) AS count
        FROM attendance a JOIN students st ON st.id = a.student_id
        WHERE st.class_id = ANY(%s) AND a.attendance_date = %s
        GROUP BY a.status
        """,
        (class_ids, today),
    )
    for row in cursor.fetchall():
        widgets["today_attendance"][row["status"]] = row["count"]
    cursor.execute(
        """
        SELECT c.name AS class_name, s.name AS subject_name, AVG(r.score) AS average
        FROM teacher_assignments ta
        JOIN classes c ON c.id = ta.class_id
        JOIN subjects s ON s.id = ta.subject_id
        LEFT JOIN students st ON st.class_id = ta.class_id
        LEFT JOIN results r ON r.student_id = st.id AND r.subject_id = ta.subject_id
                           AND r.term = %s
        WHERE ta.teacher_id = %s
        GROUP BY c.id, c.name, s.name
        ORDER BY c.id, s.name
        """,
        (term, teacher_id),
    )
    rows = cursor.fetchall()
    labels = list(dict.fromkeys(row["class_name"] for row in rows))
    subjects = list(dict.fromkeys(row["subject_name"] for row in rows))
    lookup = {(row["class_name"], row["subject_name"]): row["average"] for row in rows}
    widgets["performance"] = {
        "labels": labels,
        "datasets": [
            {
                "label": subject,
                "data": [
                    round(float(lookup[(label, subject)]), 1)
                    if lookup.get((label, subject)) is not None else 0
                    for label in labels
                ],
            }
            for subject in subjects
        ],
    }


def gpa_from_results(results: list[dict[str, Any]]) -> float | None:
    if not results:
        return None
    points = [GPA_POINTS[grade_for(result["score"])] for result in results]
    return round(sum(points) / len(points), 2)


def nav_counts(user_id: int, role: str) -> dict[str, int]:
    try:
        return {
            "messages": unread_message_count(user_id),
            "notices": unread_notice_count(user_id, role),
        }
    except psycopg.Error:
        return {"messages": 0, "notices": 0}
