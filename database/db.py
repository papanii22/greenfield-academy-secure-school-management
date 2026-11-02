from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
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
                SELECT id, full_name, email, password_hash, role, is_active,
                       must_change_password, mfa_secret, mfa_enabled,
                       failed_attempts, locked_until,
                       (locked_until > CURRENT_TIMESTAMP) AS is_locked
                FROM users
                WHERE email = %s
                """,
                (email,),
            )
            return cursor.fetchone()


def get_user_auth_data(user_id: int) -> dict[str, Any] | None:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, full_name, email, password_hash, role, is_active,
                       must_change_password, mfa_secret, mfa_enabled,
                       failed_attempts, locked_until,
                       (locked_until > CURRENT_TIMESTAMP) AS is_locked
                FROM users
                WHERE id = %s
                """,
                (user_id,),
            )
            return cursor.fetchone()


def get_user_profile(user_id: int) -> dict[str, Any] | None:
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


def register_failed_login(user_id: int) -> bool:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE users
                SET failed_attempts = failed_attempts + 1,
                    locked_until = CASE
                        WHEN failed_attempts + 1 >= 5
                        THEN CURRENT_TIMESTAMP + INTERVAL '15 minutes'
                        ELSE NULL
                    END
                WHERE id = %s
                RETURNING locked_until > CURRENT_TIMESTAMP AS is_locked
                """,
                (user_id,),
            )
            row = cursor.fetchone()
            return bool(row and row["is_locked"])


def clear_login_failures(user_id: int) -> None:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE users
                SET failed_attempts = 0, locked_until = NULL
                WHERE id = %s
                """,
                (user_id,),
            )


def save_pending_mfa_secret(user_id: int, encrypted_secret: str) -> bool:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE users
                SET mfa_secret = %s
                WHERE id = %s AND is_active AND NOT mfa_enabled
                  AND mfa_secret IS NULL
                RETURNING id
                """,
                (encrypted_secret, user_id),
            )
            return cursor.fetchone() is not None


def enable_user_mfa(
    user_id: int,
    backup_code_hashes: list[str],
    ip_address: str | None,
) -> bool:
    if not backup_code_hashes:
        raise ValueError("MFA requires backup codes.")
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE users
                SET mfa_enabled = TRUE
                WHERE id = %s AND is_active AND NOT mfa_enabled
                  AND mfa_secret IS NOT NULL
                RETURNING id
                """,
                (user_id,),
            )
            if cursor.fetchone() is None:
                return False
            cursor.execute(
                "DELETE FROM mfa_backup_codes WHERE user_id = %s",
                (user_id,),
            )
            cursor.executemany(
                """
                INSERT INTO mfa_backup_codes (user_id, code_hash)
                VALUES (%s, %s)
                """,
                [(user_id, code_hash) for code_hash in backup_code_hashes],
            )
            record_audit_event(
                user_id,
                "mfa_enabled",
                ip_address,
                connection=connection,
            )
    return True


def consume_mfa_backup_code(user_id: int, code: str, ip_address: str | None) -> bool:
    hasher = PasswordHasher()
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, code_hash
                FROM mfa_backup_codes
                WHERE user_id = %s AND NOT used
                FOR UPDATE
                """,
                (user_id,),
            )
            for row in cursor.fetchall():
                try:
                    matches = hasher.verify(row["code_hash"], code)
                except (InvalidHashError, VerificationError, VerifyMismatchError):
                    matches = False
                if not matches:
                    continue
                cursor.execute(
                    """
                    UPDATE mfa_backup_codes
                    SET used = TRUE
                    WHERE id = %s AND NOT used
                    RETURNING id
                    """,
                    (row["id"],),
                )
                if cursor.fetchone() is None:
                    continue
                record_audit_event(
                    user_id,
                    "mfa_backup_code_used",
                    ip_address,
                    connection=connection,
                )
                return True
    return False


def change_user_password(
    user_id: int,
    password_hash: str,
    ip_address: str | None,
) -> bool:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE users
                SET password_hash = %s, must_change_password = FALSE
                WHERE id = %s AND is_active
                RETURNING id
                """,
                (password_hash, user_id),
            )
            if cursor.fetchone() is None:
                return False
            record_audit_event(
                user_id,
                "password_changed",
                ip_address,
                connection=connection,
            )
    return True


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
