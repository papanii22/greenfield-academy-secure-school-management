from typing import Any

import psycopg
from psycopg.rows import dict_row

from config import Config


def connect_database() -> psycopg.Connection:
    return psycopg.connect(
        Config.DATABASE_URL,
        connect_timeout=10,
        row_factory=dict_row,
        sslmode="require",
    )


def get_user_by_email(email: str) -> dict[str, Any] | None:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, full_name, email, password_hash, role, is_active
                FROM users
                WHERE email = %s
                """,
                (email,),
            )
            return cursor.fetchone()


def record_audit_event(
    user_id: int | None,
    action: str,
    ip_address: str | None,
    *,
    connection: psycopg.Connection | None = None,
) -> None:
    if connection is not None:
        _insert_audit_event(connection, user_id, action, ip_address)
        return

    with connect_database() as active_connection:
        _insert_audit_event(active_connection, user_id, action, ip_address)


def _insert_audit_event(
    connection: psycopg.Connection,
    user_id: int | None,
    action: str,
    ip_address: str | None,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO audit_logs (user_id, action, ip_address)
            VALUES (%s, %s, %s)
            """,
            (user_id, action, ip_address),
        )
