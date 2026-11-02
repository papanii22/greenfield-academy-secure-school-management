import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).with_name(".env"))


def _required_setting(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required setting {name}. Set it in the project .env file."
        )
    return value


def _boolean_setting(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false.")


def _term_setting() -> int:
    value = os.getenv("CURRENT_TERM", "1").strip()
    try:
        term = int(value)
    except ValueError:
        raise RuntimeError("CURRENT_TERM must be 1, 2, or 3.") from None
    if term not in (1, 2, 3):
        raise RuntimeError("CURRENT_TERM must be 1, 2, or 3.")
    return term


class Config:
    SECRET_KEY = _required_setting("SECRET_KEY")
    DATABASE_URL = _required_setting("DATABASE_URL")
    JWT_SECRET = _required_setting("JWT_SECRET")
    FLASK_DEBUG = _boolean_setting("FLASK_DEBUG", False)
    SESSION_COOKIE_SECURE = _boolean_setting("SESSION_COOKIE_SECURE", False)
    CURRENT_TERM = _term_setting()

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 30 * 60
