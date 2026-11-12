from typing import Any

from database.db import connect_database, record_audit_event


def submit_signup(
    full_name: str,
    email: str,
    role: str,
    password_hash: str,
    ip_address: str | None,
) -> int:
    if role not in {"teacher", "student"}:
        raise ValueError("Choose a teacher or student account.")

    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users (full_name, email, password_hash, role, is_active)
                VALUES (%s, %s, %s, %s, FALSE)
                RETURNING id
                """,
                (full_name, email, password_hash, role),
            )
            user_id = cursor.fetchone()["id"]
            cursor.execute(
                "INSERT INTO signup_requests (user_id) VALUES (%s)",
                (user_id,),
            )
            record_audit_event(
                None,
                "signup_submitted",
                ip_address,
                connection=connection,
                target_user_id=user_id,
            )
    return user_id


def list_pending_signups() -> list[dict[str, Any]]:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT sr.user_id, u.full_name, u.email, u.role, sr.requested_at
                FROM signup_requests sr
                JOIN users u ON u.id = sr.user_id
                WHERE sr.status = 'pending'
                ORDER BY sr.requested_at, sr.id
                """
            )
            return cursor.fetchall()


def get_signup_status(user_id: int) -> str | None:
    """'pending', 'rejected', 'approved' or None when the user never signed up."""
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status FROM signup_requests WHERE user_id = %s",
                (user_id,),
            )
            row = cursor.fetchone()
            return row["status"] if row else None


def get_pending_signup(user_id: int) -> dict[str, Any] | None:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT u.id, u.full_name, u.email, u.role
                FROM signup_requests sr
                JOIN users u ON u.id = sr.user_id
                WHERE sr.user_id = %s AND sr.status = 'pending'
                """,
                (user_id,),
            )
            return cursor.fetchone()


def approve_signup(
    user_id: int,
    class_id: int,
    student_number: str | None,
    subject_ids: list[int],
    actor_id: int,
    ip_address: str | None,
) -> bool:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT u.role
                FROM signup_requests sr
                JOIN users u ON u.id = sr.user_id
                WHERE sr.user_id = %s AND sr.status = 'pending'
                  AND NOT u.is_active
                FOR UPDATE OF sr, u
                """,
                (user_id,),
            )
            signup = cursor.fetchone()
            if signup is None:
                return False

            role = signup["role"]
            if role == "student":
                if not student_number or subject_ids:
                    raise ValueError("Students require a class and student number.")
                cursor.execute("SELECT id FROM classes WHERE id = %s", (class_id,))
                if cursor.fetchone() is None:
                    raise ValueError("Choose an existing class.")
                cursor.execute(
                    """
                    INSERT INTO students (user_id, student_number, class_id)
                    VALUES (%s, %s, %s)
                    """,
                    (user_id, student_number, class_id),
                )
            elif role == "teacher":
                if student_number or len(subject_ids) not in {2, 3}:
                    raise ValueError(
                        "Teachers require a class and two or three subjects."
                    )
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
            else:
                raise ValueError("Only teacher and student requests can be approved.")

            cursor.execute(
                """
                UPDATE users
                SET is_active = TRUE
                WHERE id = %s AND NOT is_active
                RETURNING id
                """,
                (user_id,),
            )
            if cursor.fetchone() is None:
                return False
            cursor.execute(
                """
                UPDATE signup_requests
                SET status = 'approved', reviewed_at = CURRENT_TIMESTAMP,
                    reviewed_by = %s
                WHERE user_id = %s AND status = 'pending'
                """,
                (actor_id, user_id),
            )
            record_audit_event(
                actor_id,
                "signup_approved",
                ip_address,
                connection=connection,
                target_user_id=user_id,
            )
    return True


def reject_signup(
    user_id: int,
    actor_id: int,
    ip_address: str | None,
) -> bool:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE signup_requests
                SET status = 'rejected', reviewed_at = CURRENT_TIMESTAMP,
                    reviewed_by = %s
                WHERE user_id = %s AND status = 'pending'
                RETURNING user_id
                """,
                (actor_id, user_id),
            )
            if cursor.fetchone() is None:
                return False
            record_audit_event(
                actor_id,
                "signup_rejected",
                ip_address,
                connection=connection,
                target_user_id=user_id,
            )
    return True


def create_password_reset_request(email: str, ip_address: str | None) -> bool:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id
                FROM users
                WHERE email = %s AND role IN ('teacher', 'student') AND is_active
                FOR UPDATE
                """,
                (email,),
            )
            row = cursor.fetchone()
            if row is None:
                record_audit_event(
                    None,
                    "password_reset_requested",
                    ip_address,
                    connection=connection,
                )
                return False
            cursor.execute(
                """
                INSERT INTO password_reset_requests (user_id)
                VALUES (%s)
                ON CONFLICT (user_id) WHERE status = 'open' DO NOTHING
                """,
                (row["id"],),
            )
            record_audit_event(
                None,
                "password_reset_requested",
                ip_address,
                connection=connection,
                target_user_id=row["user_id"],
            )
    return True


def list_password_reset_requests() -> list[dict[str, Any]]:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT pr.id, pr.user_id, u.full_name, u.email, u.role,
                       pr.requested_at
                FROM password_reset_requests pr
                JOIN users u ON u.id = pr.user_id
                WHERE pr.status = 'open' AND u.is_active
                  AND u.role IN ('teacher', 'student')
                ORDER BY pr.requested_at, pr.id
                """
            )
            return cursor.fetchall()


def reset_school_user_password(
    user_id: int,
    password_hash: str,
    actor_id: int,
    ip_address: str | None,
) -> bool:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE users
                SET password_hash = %s, must_change_password = TRUE,
                    auth_version = auth_version + 1
                WHERE id = %s AND role IN ('teacher', 'student') AND is_active
                RETURNING id
                """,
                (password_hash, user_id),
            )
            if cursor.fetchone() is None:
                return False
            cursor.execute(
                """
                UPDATE password_reset_requests
                SET status = 'completed', completed_at = CURRENT_TIMESTAMP,
                    completed_by = %s
                WHERE user_id = %s AND status = 'open'
                """,
                (actor_id, user_id),
            )
            record_audit_event(
                actor_id,
                "admin_password_reset",
                ip_address,
                connection=connection,
                target_user_id=user_id,
            )
    return True


def get_auth_version(user_id: int) -> int | None:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT auth_version FROM users WHERE id = %s AND is_active",
                (user_id,),
            )
            row = cursor.fetchone()
            return row["auth_version"] if row else None


def record_login_failure_hint(email: str, ip_address: str) -> int:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO login_failure_hints
                    (email_normalized, ip_address, failures, updated_at)
                VALUES (%s, %s, 1, CURRENT_TIMESTAMP)
                ON CONFLICT (email_normalized, ip_address)
                DO UPDATE SET failures = login_failure_hints.failures + 1,
                              updated_at = CURRENT_TIMESTAMP
                RETURNING failures
                """,
                (email, ip_address),
            )
            return cursor.fetchone()["failures"]


def clear_login_failure_hint(email: str, ip_address: str) -> None:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM login_failure_hints
                WHERE email_normalized = %s AND ip_address = %s
                """,
                (email, ip_address),
            )
