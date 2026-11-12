from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import psycopg

from database.db import connect_database, record_audit_event
from config import Config
from grading import grade_for
from reporting import MAX_REPORT_ROWS


CURRENT_TERM = Config.CURRENT_TERM
VALID_ROLES = {"teacher", "student"}
VALID_ATTENDANCE_STATUSES = {"present", "absent", "late"}


def get_admin_dashboard(term: int) -> dict[str, Any]:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS count FROM students")
            total_students = cursor.fetchone()["count"]
            cursor.execute(
                "SELECT COUNT(*) AS count FROM users WHERE role = 'teacher' AND is_active"
            )
            total_teachers = cursor.fetchone()["count"]
            cursor.execute(
                """
                SELECT COALESCE(
                    100.0 * COUNT(*) FILTER (WHERE status IN ('present', 'late'))
                    / NULLIF(COUNT(*), 0),
                    0
                ) AS rate
                FROM attendance
                WHERE attendance_date = CURRENT_DATE
                """
            )
            attendance_rate = float(cursor.fetchone()["rate"])
            cursor.execute(
                "SELECT AVG(score) AS average FROM results WHERE term = %s",
                (term,),
            )
            school_average = cursor.fetchone()["average"]

            cursor.execute(
                """
                SELECT day::date AS attendance_date,
                       COUNT(a.student_id) AS recorded,
                       COUNT(a.student_id) FILTER (WHERE a.status IN ('present', 'late')) AS attended
                FROM generate_series(CURRENT_DATE - INTERVAL '29 days',
                                     CURRENT_DATE,
                                     INTERVAL '1 day') AS day
                LEFT JOIN attendance a ON a.attendance_date = day::date
                GROUP BY day
                ORDER BY day
                """
            )
            attendance_trend = [
                {
                    "label": f"{row['attendance_date'].strftime('%b')} {row['attendance_date'].day}",
                    "rate": round(100 * row["attended"] / row["recorded"], 1)
                    if row["recorded"]
                    else 0,
                    "recorded": row["recorded"],
                }
                for row in cursor.fetchall()
            ]
            cursor.execute(
                """
                SELECT s.name, AVG(r.score) AS average
                FROM subjects s
                LEFT JOIN results r ON r.subject_id = s.id AND r.term = %s
                GROUP BY s.id, s.name
                ORDER BY s.name
                """,
                (term,),
            )
            subject_averages = [
                {"label": row["name"], "average": float(row["average"] or 0)}
                for row in cursor.fetchall()
            ]
            cursor.execute(
                "SELECT score FROM results WHERE term = %s ORDER BY id",
                (term,),
            )
            grade_counts = {grade: 0 for grade in "ABCDF"}
            for row in cursor.fetchall():
                grade_counts[grade_for(row["score"])] += 1

            cursor.execute(
                """
                SELECT a.action, a.created_at, COALESCE(u.full_name, 'System') AS actor
                FROM audit_logs a
                LEFT JOIN users u ON u.id = a.user_id
                ORDER BY a.created_at DESC, a.id DESC
                LIMIT 8
                """
            )
            recent_activity = cursor.fetchall()

    return {
        "term": term,
        "total_students": total_students,
        "total_teachers": total_teachers,
        "attendance_rate": attendance_rate,
        "school_average": float(school_average) if school_average is not None else None,
        "attendance_trend": attendance_trend,
        "subject_averages": subject_averages,
        "grade_counts": grade_counts,
        "recent_activity": recent_activity,
    }


def list_users(search: str, role: str | None, page: int, per_page: int = 20) -> dict[str, Any]:
    offset = (page - 1) * per_page
    pattern = f"%{search.replace('%', '\\%').replace('_', '\\_')}%"
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM users u
                WHERE (%s = '' OR u.full_name ILIKE %s OR u.email ILIKE %s)
                  AND (%s::text IS NULL OR u.role = %s)
                """,
                (search, pattern, pattern, role, role),
            )
            total = cursor.fetchone()["count"]
            cursor.execute(
                """
                SELECT u.id, u.full_name, u.email, u.role, u.is_active,
                       u.must_change_password, s.student_number, c.name AS class_name,
                       sr.status AS signup_status
                FROM users u
                LEFT JOIN signup_requests sr ON sr.user_id = u.id
                LEFT JOIN students s ON s.user_id = u.id
                LEFT JOIN classes c ON c.id = s.class_id
                WHERE (%s = '' OR u.full_name ILIKE %s OR u.email ILIKE %s)
                  AND (%s::text IS NULL OR u.role = %s)
                ORDER BY u.full_name, u.id
                LIMIT %s OFFSET %s
                """,
                (search, pattern, pattern, role, role, per_page, offset),
            )
            users = cursor.fetchall()
    return {"users": users, "total": total, "page": page, "per_page": per_page}


def get_user_edit_data(user_id: int) -> dict[str, Any] | None:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, full_name, email, role, is_active
                FROM users
                WHERE id = %s
                """,
                (user_id,),
            )
            return cursor.fetchone()


def get_create_user_options() -> dict[str, list[dict[str, Any]]]:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, name FROM classes ORDER BY name")
            classes = cursor.fetchall()
            cursor.execute("SELECT id, name FROM subjects ORDER BY name")
            subjects = cursor.fetchall()
    return {"classes": classes, "subjects": subjects}


def create_school_user(
    *,
    full_name: str,
    email: str,
    role: str,
    password_hash: str,
    class_id: int | None,
    student_number: str | None,
    subject_ids: list[int],
    actor_id: int,
    ip_address: str | None,
) -> int:
    if role not in VALID_ROLES:
        raise ValueError("Choose a teacher or student account.")
    if role == "student" and (class_id is None or not student_number):
        raise ValueError("Students require a class and student number.")
    if role == "teacher" and (
        class_id is None
        or len(subject_ids) not in {2, 3}
        or len(set(subject_ids)) != len(subject_ids)
    ):
        raise ValueError("Teachers require a class and two or three subjects.")

    with connect_database() as connection:
        with connection.cursor() as cursor:
            if role == "teacher":
                cursor.execute("SELECT id FROM classes WHERE id = %s", (class_id,))
                if cursor.fetchone() is None:
                    raise ValueError("Choose an existing class.")
                cursor.execute(
                    "SELECT id FROM subjects WHERE id = ANY(%s)",
                    (subject_ids,),
                )
                if {row["id"] for row in cursor.fetchall()} != set(subject_ids):
                    raise ValueError("Choose existing subjects.")

            cursor.execute(
                """
                INSERT INTO users
                    (full_name, email, password_hash, role, must_change_password)
                VALUES (%s, %s, %s, %s, TRUE)
                RETURNING id
                """,
                (full_name, email, password_hash, role),
            )
            user_id = cursor.fetchone()["id"]

            if role == "student":
                cursor.execute(
                    """
                    INSERT INTO students (user_id, student_number, class_id)
                    VALUES (%s, %s, %s)
                    """,
                    (user_id, student_number, class_id),
                )
            else:
                cursor.execute(
                    "UPDATE classes SET class_teacher_id = %s WHERE id = %s",
                    (user_id, class_id),
                )
                cursor.executemany(
                    """
                    INSERT INTO teacher_assignments (teacher_id, class_id, subject_id)
                    VALUES (%s, %s, %s)
                    """,
                    [(user_id, class_id, subject_id) for subject_id in subject_ids],
                )
            record_audit_event(
                actor_id,
                "user_created",
                ip_address,
                connection=connection,
            )
    return user_id


def update_user(
    user_id: int,
    full_name: str,
    email: str,
    actor_id: int,
    ip_address: str | None,
) -> bool:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE users
                SET full_name = %s, email = %s
                WHERE id = %s
                RETURNING id
                """,
                (full_name, email, user_id),
            )
            if cursor.fetchone() is None:
                return False
            record_audit_event(
                actor_id,
                "user_updated",
                ip_address,
                connection=connection,
            )
    return True


def set_user_active(
    user_id: int,
    active: bool,
    actor_id: int,
    ip_address: str | None,
) -> bool:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            if active:
                cursor.execute(
                    """
                    UPDATE users
                    SET is_active = TRUE
                    WHERE id = %s
                      AND NOT EXISTS (
                          SELECT 1
                          FROM signup_requests
                          WHERE signup_requests.user_id = users.id
                            AND signup_requests.status IN ('pending', 'rejected')
                      )
                    RETURNING id
                    """,
                    (user_id,),
                )
            else:
                cursor.execute(
                    "UPDATE users SET is_active = FALSE WHERE id = %s RETURNING id",
                    (user_id,),
                )
            if cursor.fetchone() is None:
                return False
            record_audit_event(
                actor_id,
                "user_reactivated" if active else "user_deactivated",
                ip_address,
                connection=connection,
            )
    return True


def get_audit_logs(
    user_id: int | None,
    action: str,
    start_date: date | None,
    end_date: date | None,
    page: int,
    per_page: int = 25,
) -> dict[str, Any]:
    offset = (page - 1) * per_page
    end_exclusive = end_date + timedelta(days=1) if end_date else None
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) AS count
                FROM audit_logs a
                WHERE (%s::bigint IS NULL OR a.user_id = %s)
                  AND (%s = '' OR a.action ILIKE %s)
                  AND (%s::date IS NULL OR a.created_at >= %s)
                  AND (%s::date IS NULL OR a.created_at < %s)
                """,
                (user_id, user_id, action, f"%{action}%", start_date, start_date,
                 end_exclusive, end_exclusive),
            )
            total = cursor.fetchone()["count"]
            cursor.execute(
                """
                SELECT a.id, a.user_id, a.action, a.ip_address, a.created_at,
                       COALESCE(u.full_name, 'System') AS actor
                FROM audit_logs a
                LEFT JOIN users u ON u.id = a.user_id
                WHERE (%s::bigint IS NULL OR a.user_id = %s)
                  AND (%s = '' OR a.action ILIKE %s)
                  AND (%s::date IS NULL OR a.created_at >= %s)
                  AND (%s::date IS NULL OR a.created_at < %s)
                ORDER BY a.created_at DESC, a.id DESC
                LIMIT %s OFFSET %s
                """,
                (
                    user_id, user_id, action, f"%{action}%",
                    start_date, start_date, end_exclusive, end_exclusive,
                    per_page, offset,
                ),
            )
            logs = cursor.fetchall()
            cursor.execute(
                """
                SELECT id, full_name
                FROM users
                ORDER BY full_name
                """
            )
            users = cursor.fetchall()
    return {"logs": logs, "users": users, "total": total, "page": page, "per_page": per_page}


def get_teacher_dashboard(teacher_id: int, term: int) -> dict[str, Any]:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            # Every class the teacher teaches, not only the one they are class teacher of.
            cursor.execute(
                """
                SELECT c.id, c.name, (c.class_teacher_id = %s) AS is_class_teacher
                FROM classes c
                WHERE c.class_teacher_id = %s
                   OR EXISTS (SELECT 1 FROM teacher_assignments ta
                              WHERE ta.class_id = c.id AND ta.teacher_id = %s)
                ORDER BY c.id
                """,
                (teacher_id, teacher_id, teacher_id),
            )
            class_rows = cursor.fetchall()
            classes = []
            for class_row in class_rows:
                cursor.execute(
                    """
                    SELECT s.id AS subject_id, s.name AS subject_name,
                           AVG(r.score) AS average,
                           COUNT(DISTINCT st.id) AS student_count
                    FROM teacher_assignments ta
                    JOIN subjects s ON s.id = ta.subject_id
                    LEFT JOIN students st ON st.class_id = ta.class_id
                    LEFT JOIN results r
                      ON r.student_id = st.id
                     AND r.subject_id = ta.subject_id
                     AND r.term = %s
                    WHERE ta.teacher_id = %s
                      AND ta.class_id = %s
                    GROUP BY s.id, s.name
                    ORDER BY s.name
                    """,
                    (term, teacher_id, class_row["id"]),
                )
                classes.append(
                    {
                        "id": class_row["id"],
                        "name": class_row["name"],
                        "is_class_teacher": class_row["is_class_teacher"],
                        "subjects": cursor.fetchall(),
                    }
                )
            cursor.execute(
                """
                SELECT day::date AS attendance_date,
                       COUNT(a.student_id) AS recorded,
                       COUNT(a.student_id) FILTER (WHERE a.status IN ('present', 'late')) AS attended
                FROM generate_series(CURRENT_DATE - INTERVAL '29 days',
                                     CURRENT_DATE,
                                     INTERVAL '1 day') AS day
                LEFT JOIN classes c
                  ON c.class_teacher_id = %s
                  OR EXISTS (SELECT 1 FROM teacher_assignments ta
                             WHERE ta.class_id = c.id AND ta.teacher_id = %s)
                LEFT JOIN students st ON st.class_id = c.id
                LEFT JOIN attendance a
                  ON a.student_id = st.id
                 AND a.attendance_date = day::date
                GROUP BY day
                ORDER BY day
                """,
                (teacher_id, teacher_id),
            )
            attendance_trend = [
                {
                    "label": f"{row['attendance_date'].strftime('%b')} {row['attendance_date'].day}",
                    "rate": round(100 * row["attended"] / row["recorded"], 1)
                    if row["recorded"]
                    else 0,
                }
                for row in cursor.fetchall()
            ]
    return {
        "term": term,
        "classes": classes,
        "attendance_trend": attendance_trend,
    }


# A teacher may mark a class's register when they are its class teacher or
# teach it any subject. Attendance is one mark per student per day; the last
# teacher to save it is recorded in marked_by.
_CAN_MARK_ATTENDANCE = """
    SELECT 1 FROM classes c
    WHERE c.id = %(class_id)s
      AND (c.class_teacher_id = %(teacher_id)s
           OR EXISTS (SELECT 1 FROM teacher_assignments ta
                      WHERE ta.class_id = c.id AND ta.teacher_id = %(teacher_id)s))
"""


def get_class_roster_for_attendance(
    teacher_id: int,
    class_id: int,
    attendance_date: date,
) -> list[dict[str, Any]] | None:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                _CAN_MARK_ATTENDANCE,
                {"class_id": class_id, "teacher_id": teacher_id},
            )
            if cursor.fetchone() is None:
                return None
            cursor.execute(
                """
                SELECT s.id, s.student_number, u.full_name, c.name AS class_name, a.status
                FROM classes c
                JOIN students s ON s.class_id = c.id
                JOIN users u ON u.id = s.user_id
                LEFT JOIN attendance a
                  ON a.student_id = s.id
                 AND a.attendance_date = %s
                WHERE c.id = %s
                ORDER BY u.full_name
                """,
                (attendance_date, class_id),
            )
            rows = cursor.fetchall()
    return rows


def save_class_attendance(
    teacher_id: int,
    class_id: int,
    attendance_date: date,
    statuses: dict[int, str],
    ip_address: str | None,
) -> bool:
    if not statuses or not set(statuses.values()).issubset(VALID_ATTENDANCE_STATUSES):
        raise ValueError("Choose a valid attendance status for every student.")
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                _CAN_MARK_ATTENDANCE,
                {"class_id": class_id, "teacher_id": teacher_id},
            )
            if cursor.fetchone() is None:
                return False
            cursor.execute("SELECT id FROM students WHERE class_id = %s", (class_id,))
            roster_ids = {row["id"] for row in cursor.fetchall()}
            if set(statuses) != roster_ids:
                raise ValueError("Attendance must be supplied for the full class roster.")
            cursor.executemany(
                """
                INSERT INTO attendance (student_id, attendance_date, status, marked_by)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (student_id, attendance_date)
                DO UPDATE SET status = EXCLUDED.status, marked_by = EXCLUDED.marked_by
                """,
                [
                    (student_id, attendance_date, status, teacher_id)
                    for student_id, status in statuses.items()
                ],
            )
            record_audit_event(
                teacher_id,
                "attendance_marked",
                ip_address,
                connection=connection,
            )
    return True


def get_class_roster_for_results(
    teacher_id: int,
    class_id: int,
    subject_id: int,
    term: int,
) -> list[dict[str, Any]] | None:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM teacher_assignments
                WHERE teacher_id = %s AND class_id = %s AND subject_id = %s
                """,
                (teacher_id, class_id, subject_id),
            )
            if cursor.fetchone() is None:
                return None
            cursor.execute(
                """
                SELECT st.id, st.student_number, u.full_name, c.name AS class_name,
                       sub.name AS subject_name, r.score
                FROM teacher_assignments ta
                JOIN students st ON st.class_id = ta.class_id
                JOIN users u ON u.id = st.user_id
                JOIN classes c ON c.id = ta.class_id
                JOIN subjects sub ON sub.id = ta.subject_id
                LEFT JOIN results r
                  ON r.student_id = st.id
                 AND r.subject_id = ta.subject_id
                 AND r.term = %s
                WHERE ta.teacher_id = %s
                  AND ta.class_id = %s
                  AND ta.subject_id = %s
                ORDER BY u.full_name
                """,
                (term, teacher_id, class_id, subject_id),
            )
            rows = cursor.fetchall()
    return rows


def save_class_results(
    teacher_id: int,
    class_id: int,
    subject_id: int,
    term: int,
    scores: dict[int, Decimal],
    ip_address: str | None,
) -> bool:
    if term not in (1, 2, 3):
        raise ValueError("Choose a valid term.")
    for score in scores.values():
        grade_for(score)
        if score != score.quantize(Decimal("0.01")):
            raise ValueError("Scores can have no more than two decimal places.")
    if not scores:
        raise ValueError("Enter a score for every student.")
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT st.id
                FROM teacher_assignments ta
                JOIN students st ON st.class_id = ta.class_id
                WHERE ta.teacher_id = %s
                  AND ta.class_id = %s
                  AND ta.subject_id = %s
                """,
                (teacher_id, class_id, subject_id),
            )
            roster_ids = {row["id"] for row in cursor.fetchall()}
            if not roster_ids:
                return False
            if set(scores) != roster_ids:
                raise ValueError("Scores must be supplied for the full class roster.")
            cursor.executemany(
                """
                INSERT INTO results (student_id, subject_id, term, score, entered_by)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (student_id, subject_id, term)
                DO UPDATE SET score = EXCLUDED.score, entered_by = EXCLUDED.entered_by
                """,
                [
                    (student_id, subject_id, term, score, teacher_id)
                    for student_id, score in scores.items()
                ],
            )
            record_audit_event(
                teacher_id,
                "results_saved",
                ip_address,
                connection=connection,
            )
    return True


def save_uploaded_results(
    teacher_id: int,
    class_id: int,
    subject_id: int,
    term: int,
    scores: dict[str, Decimal],
    ip_address: str | None,
) -> int | None:
    """Save scores keyed by student number from an uploaded file.

    Unlike the on-screen form, a file may cover only some of the class: the
    listed students are saved and everyone else is left as they were. Every
    listed student number must belong to this class. Returns the number of
    scores saved, or None when the teacher does not teach this class/subject.
    """
    if term not in (1, 2, 3):
        raise ValueError("Choose a valid term.")
    if not scores:
        raise ValueError("The file has no scores to save.")
    for score in scores.values():
        grade_for(score)
        if score != score.quantize(Decimal("0.01")):
            raise ValueError("Scores can have no more than two decimal places.")
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT st.id, st.student_number
                FROM teacher_assignments ta
                JOIN students st ON st.class_id = ta.class_id
                WHERE ta.teacher_id = %s AND ta.class_id = %s AND ta.subject_id = %s
                """,
                (teacher_id, class_id, subject_id),
            )
            roster = {row["student_number"]: row["id"] for row in cursor.fetchall()}
            if not roster:
                cursor.execute(
                    "SELECT 1 FROM teacher_assignments "
                    "WHERE teacher_id = %s AND class_id = %s AND subject_id = %s",
                    (teacher_id, class_id, subject_id),
                )
                if cursor.fetchone() is None:
                    return None
            unknown = sorted(set(scores) - set(roster))
            if unknown:
                shown = ", ".join(unknown[:5]) + (" and more" if len(unknown) > 5 else "")
                raise ValueError(f"These student numbers are not in this class: {shown}.")
            cursor.executemany(
                """
                INSERT INTO results (student_id, subject_id, term, score, entered_by)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (student_id, subject_id, term)
                DO UPDATE SET score = EXCLUDED.score, entered_by = EXCLUDED.entered_by
                """,
                [
                    (roster[number], subject_id, term, score, teacher_id)
                    for number, score in scores.items()
                ],
            )
            record_audit_event(
                teacher_id,
                "results_uploaded",
                ip_address,
                connection=connection,
            )
    return len(scores)


def get_student_dashboard(user_id: int, term: int) -> dict[str, Any] | None:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT st.id, st.student_number, st.class_id, c.name AS class_name,
                       u.full_name
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

            cursor.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM attendance
                WHERE student_id = %s
                GROUP BY status
                """,
                (student["id"],),
            )
            attendance_counts = {status: 0 for status in VALID_ATTENDANCE_STATUSES}
            attendance_counts.update(
                {row["status"]: row["count"] for row in cursor.fetchall()}
            )
            total_days = sum(attendance_counts.values())
            attendance_percentage = (
                100 * (attendance_counts["present"] + attendance_counts["late"]) / total_days
                if total_days
                else 0
            )
            cursor.execute(
                """
                SELECT attendance_date, status
                FROM attendance
                WHERE student_id = %s
                ORDER BY attendance_date DESC
                LIMIT 10
                """,
                (student["id"],),
            )
            recent_attendance = cursor.fetchall()
            cursor.execute(
                """
                SELECT r.subject_id, s.name AS subject_name, r.score
                FROM results r
                JOIN subjects s ON s.id = r.subject_id
                WHERE r.student_id = %s
                  AND r.term = %s
                ORDER BY s.name
                """,
                (student["id"], term),
            )
            results = cursor.fetchall()
            for result in results:
                result["score"] = float(result["score"])
                result["grade"] = grade_for(result["score"])
            cursor.execute(
                """
                SELECT term, AVG(score) AS average
                FROM results
                WHERE student_id = %s
                GROUP BY term
                ORDER BY term
                """,
                (student["id"],),
            )
            term_averages = [
                {"term": row["term"], "average": float(row["average"])}
                for row in cursor.fetchall()
            ]
    return {
        "student": student,
        "term": term,
        "attendance_counts": attendance_counts,
        "attendance_percentage": round(attendance_percentage, 1),
        "recent_attendance": recent_attendance,
        "results": results,
        "term_averages": term_averages,
    }


def get_admin_attendance_export_rows(
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT a.attendance_date, c.name AS class_name,
                       s.student_number, u.full_name AS student_name,
                       a.status, COALESCE(marker.full_name, 'Unknown') AS marked_by
                FROM attendance a
                JOIN students s ON s.id = a.student_id
                JOIN users u ON u.id = s.user_id
                JOIN classes c ON c.id = s.class_id
                LEFT JOIN users marker ON marker.id = a.marked_by
                WHERE a.attendance_date >= %s AND a.attendance_date <= %s
                ORDER BY a.attendance_date, c.name, u.full_name
                LIMIT %s
                """,
                (start_date, end_date, MAX_REPORT_ROWS + 1),
            )
            return cursor.fetchall()


def get_admin_results_export_rows(term: int) -> list[dict[str, Any]]:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT r.term, c.name AS class_name, s.student_number,
                       u.full_name AS student_name, sub.name AS subject_name,
                       r.score, COALESCE(teacher.full_name, 'Unknown') AS entered_by
                FROM results r
                JOIN students s ON s.id = r.student_id
                JOIN users u ON u.id = s.user_id
                JOIN classes c ON c.id = s.class_id
                JOIN subjects sub ON sub.id = r.subject_id
                LEFT JOIN users teacher ON teacher.id = r.entered_by
                WHERE r.term = %s
                ORDER BY c.name, u.full_name, sub.name
                LIMIT %s
                """,
                (term, MAX_REPORT_ROWS + 1),
            )
            return cursor.fetchall()


def get_admin_audit_export_rows(
    start_date: date,
    end_date: date,
    user_id: int | None = None,
    action: str = "",
) -> list[dict[str, Any]]:
    end_exclusive = end_date + timedelta(days=1)
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT a.created_at, COALESCE(u.full_name, 'System') AS actor,
                       a.action, a.ip_address
                FROM audit_logs a
                LEFT JOIN users u ON u.id = a.user_id
                WHERE a.created_at >= %s AND a.created_at < %s
                  AND (%s::bigint IS NULL OR a.user_id = %s)
                  AND (%s = '' OR a.action ILIKE %s)
                ORDER BY a.created_at DESC, a.id DESC
                LIMIT %s
                """,
                (
                    start_date,
                    end_exclusive,
                    user_id,
                    user_id,
                    action,
                    f"%{action}%",
                    MAX_REPORT_ROWS + 1,
                ),
            )
            return cursor.fetchall()


def get_student_attendance_export_rows(user_id: int) -> list[dict[str, Any]]:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT a.attendance_date, a.status
                FROM students s
                JOIN attendance a ON a.student_id = s.id
                WHERE s.user_id = %s
                ORDER BY a.attendance_date DESC
                LIMIT %s
                """,
                (user_id, MAX_REPORT_ROWS + 1),
            )
            return cursor.fetchall()


def get_student_results_export_rows(
    user_id: int,
    term: int,
) -> list[dict[str, Any]]:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT r.term, sub.name AS subject_name, r.score
                FROM students s
                JOIN results r ON r.student_id = s.id
                JOIN subjects sub ON sub.id = r.subject_id
                WHERE s.user_id = %s AND r.term = %s
                ORDER BY sub.name
                LIMIT %s
                """,
                (user_id, term, MAX_REPORT_ROWS + 1),
            )
            return cursor.fetchall()
