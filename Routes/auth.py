from datetime import datetime, timedelta, timezone
import base64
import io
import secrets
from functools import wraps
from typing import Any, Callable, TypeVar, cast

import jwt
import pyotp
import qrcode
from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)
from flask import (
    Blueprint,
    current_app,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import EqualTo, InputRequired, Length, Regexp

from database.db import (
    clear_login_failures,
    change_user_password,
    consume_mfa_backup_code,
    enable_user_mfa,
    get_user_auth_data,
    get_user_by_email,
    register_failed_login,
    record_audit_event,
    reset_pending_mfa_secret,
    save_pending_mfa_secret,
)
from extensions import limiter
from mfa_crypto import decrypt_mfa_secret, encrypt_mfa_secret


auth_bp = Blueprint("auth", __name__)
F = TypeVar("F", bound=Callable[..., Any])
password_hasher = PasswordHasher()
LOGIN_FAILURE_MESSAGE = "Invalid email or password."
MFA_ISSUER = "Greenfield Academy"
BACKUP_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class LoginForm(FlaskForm):
    email = StringField(
        "Email address",
        validators=[
            InputRequired(),
            Length(max=254),
            Regexp(
                r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
                message="Enter a valid email address.",
            ),
        ],
    )
    password = PasswordField(
        "Password",
        validators=[InputRequired(), Length(max=256)],
    )
    submit = SubmitField("Sign in")


class PasswordChangeForm(FlaskForm):
    current_password = PasswordField(
        "Current password",
        validators=[InputRequired(), Length(max=256)],
    )
    new_password = PasswordField(
        "New password",
        validators=[InputRequired(), Length(min=12, max=256)],
    )
    confirm_password = PasswordField(
        "Confirm new password",
        validators=[
            InputRequired(),
            Length(max=256),
            EqualTo("new_password", message="The new passwords do not match."),
        ],
    )
    submit = SubmitField("Update password")


class MfaCodeForm(FlaskForm):
    code = StringField(
        "Authenticator or backup code",
        validators=[
            InputRequired(),
            Length(min=6, max=32),
            Regexp(r"^[A-Za-z0-9 -]+$", message="Enter a valid verification code."),
        ],
    )
    submit = SubmitField("Verify")


def login_required(view: F) -> F:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if "user_id" not in session or not session.get("mfa_verified"):
            session.clear()
            return redirect(url_for("auth.login"))
        if (
            session.get("must_change_password")
            and request.endpoint not in {"auth.change_password", "auth.logout"}
        ):
            return redirect(url_for("auth.change_password"))
        return view(*args, **kwargs)

    return cast(F, wrapped)


def role_required(*allowed_roles: str) -> Callable[[F], F]:
    def decorate(view: F) -> F:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if "user_id" not in session or not session.get("mfa_verified"):
                session.clear()
                return redirect(url_for("auth.login"))
            if (
                session.get("must_change_password")
                and request.endpoint not in {"auth.change_password", "auth.logout"}
            ):
                return redirect(url_for("auth.change_password"))
            if session.get("role") not in allowed_roles:
                return render_template(
                    "errors/error.html",
                    title="Access denied",
                    message="You do not have permission to view this page.",
                ), 403
            return view(*args, **kwargs)

        return cast(F, wrapped)

    return decorate


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _record_auth_failure(
    user: dict[str, Any] | None,
    action: str,
    ip_address: str | None,
) -> bool:
    locked = False
    if user and user["is_active"] and not user.get("is_locked", False):
        locked = register_failed_login(user["id"])
    record_audit_event(user["id"] if user else None, action, ip_address)
    if locked:
        record_audit_event(user["id"], "account_locked", ip_address)
    return locked


def _establish_authenticated_session(
    user: dict[str, Any],
    ip_address: str | None,
) -> bool:
    clear_login_failures(user["id"])
    record_audit_event(user["id"], "login_success", ip_address)
    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["full_name"] = user["full_name"]
    session["role"] = user["role"]
    session["must_change_password"] = user.get("must_change_password", False)
    session["mfa_verified"] = True
    session["show_branded_loader"] = True
    return session["must_change_password"]


def _pending_mfa_user(mode: str) -> dict[str, Any] | None:
    user_id = session.get("mfa_pending_user_id")
    if not user_id or session.get("mfa_pending_mode") != mode:
        return None
    user = get_user_auth_data(user_id)
    if user is None or not user["is_active"] or user.get("is_locked", False):
        session.clear()
        return None
    return user


def _new_backup_codes() -> list[str]:
    return [
        "-".join(
            "".join(secrets.choice(BACKUP_CODE_ALPHABET) for _ in range(4))
            for _ in range(3)
        )
        for _ in range(10)
    ]


def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def login() -> Any:
    if "user_id" in session:
        if not session.get("mfa_verified"):
            session.clear()
        elif session.get("must_change_password"):
            return redirect(url_for("auth.change_password"))
        else:
            return redirect(url_for("main.dashboard"))
    if session.get("mfa_pending_user_id"):
        if session.get("mfa_pending_mode") == "setup":
            return redirect(url_for("auth.mfa_setup"))
        return redirect(url_for("auth.mfa_verify"))

    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = get_user_by_email(email)
        authenticated = bool(
            user
            and user["is_active"]
            and not user.get("is_locked", False)
            and verify_password(user["password_hash"], form.password.data)
        )

        if not authenticated:
            _record_auth_failure(user, "login_failure", request.remote_addr)
            flash(LOGIN_FAILURE_MESSAGE, "error")
            return render_template("auth/login.html", form=form)
        if user.get("mfa_enabled") and not user.get("mfa_secret"):
            record_audit_event(
                user["id"],
                "mfa_configuration_error",
                request.remote_addr,
            )
            flash(
                "MFA needs administrator attention. Contact your school administrator.",
                "error",
            )
            return render_template("auth/login.html", form=form)

        session.clear()
        session.permanent = True
        session["mfa_pending_user_id"] = user["id"]
        session["mfa_pending_mode"] = (
            "verify" if user.get("mfa_enabled") else "setup"
        )
        session["show_branded_loader"] = True
        endpoint = (
            "auth.mfa_verify"
            if session["mfa_pending_mode"] == "verify"
            else "auth.mfa_setup"
        )
        return redirect(url_for(endpoint))

    return render_template("auth/login.html", form=form)


@auth_bp.route("/mfa/setup", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def mfa_setup() -> Any:
    user = _pending_mfa_user("setup")
    if user is None:
        if session.get("user_id"):
            return redirect(url_for("main.dashboard"))
        session.clear()
        return redirect(url_for("auth.login"))
    if user["mfa_enabled"] and user["mfa_secret"]:
        session["mfa_pending_mode"] = "verify"
        return redirect(url_for("auth.mfa_verify"))
    if user["mfa_enabled"]:
        session.clear()
        flash(
            "MFA needs administrator attention. Contact your school administrator.",
            "error",
        )
        return redirect(url_for("auth.login"))

    encrypted_secret = user["mfa_secret"]
    if encrypted_secret is None:
        secret = pyotp.random_base32()
        encrypted_secret = encrypt_mfa_secret(secret)
        if not save_pending_mfa_secret(user["id"], encrypted_secret):
            user = get_user_auth_data(user["id"])
            if user is None or not user["mfa_secret"]:
                session.clear()
                return redirect(url_for("auth.login"))
            if user["mfa_enabled"]:
                session["mfa_pending_mode"] = "verify"
                return redirect(url_for("auth.mfa_verify"))
            encrypted_secret = user["mfa_secret"]

    secret = decrypt_mfa_secret(encrypted_secret)
    form = MfaCodeForm()
    if form.validate_on_submit():
        if not pyotp.TOTP(secret).verify(form.code.data.strip(), valid_window=1):
            locked = _record_auth_failure(
                user,
                "mfa_failure",
                request.remote_addr,
            )
            if locked:
                session.clear()
                flash(LOGIN_FAILURE_MESSAGE, "error")
                return redirect(url_for("auth.login"))
            flash("The verification code is invalid. Try again.", "error")
        else:
            backup_codes = _new_backup_codes()
            backup_hashes = [
                password_hasher.hash(code.replace("-", ""))
                for code in backup_codes
            ]
            if not enable_user_mfa(
                user["id"],
                backup_hashes,
                request.remote_addr,
            ):
                session.clear()
                flash("MFA enrollment could not be completed. Sign in again.", "error")
                return redirect(url_for("auth.login"))
            must_change_password = _establish_authenticated_session(
                user,
                request.remote_addr,
            )
            response = make_response(
                render_template(
                    "auth/mfa_backup_codes.html",
                    backup_codes=backup_codes,
                    must_change_password=must_change_password,
                )
            )
            return _no_store(response)

    provisioning_uri = pyotp.TOTP(secret).provisioning_uri(
        name=user["email"],
        issuer_name=MFA_ISSUER,
    )
    qr_image = qrcode.make(provisioning_uri)
    image_buffer = io.BytesIO()
    qr_image.save(image_buffer, format="PNG")
    qr_data = base64.b64encode(image_buffer.getvalue()).decode("ascii")
    response = make_response(
        render_template(
            "auth/mfa_setup.html",
            form=form,
            secret=secret,
            qr_data=qr_data,
        )
    )
    return _no_store(response)


@auth_bp.post("/mfa/setup/reset")
@limiter.limit("3 per hour")
def reset_mfa_setup() -> Any:
    user = _pending_mfa_user("setup")
    if user is None:
        return redirect(url_for("auth.login"))
    if user["mfa_enabled"]:
        session["mfa_pending_mode"] = "verify"
        return redirect(url_for("auth.mfa_verify"))

    if not reset_pending_mfa_secret(user["id"]):
        latest_user = get_user_auth_data(user["id"])
        if (
            latest_user
            and latest_user["is_active"]
            and latest_user.get("mfa_enabled")
            and latest_user.get("mfa_secret")
        ):
            session["mfa_pending_mode"] = "verify"
            return redirect(url_for("auth.mfa_verify"))
        flash("The setup key could not be reset. Please try again.", "error")
        return redirect(url_for("auth.mfa_setup"))

    record_audit_event(
        user["id"],
        "mfa_setup_reset",
        request.remote_addr,
    )
    flash("A new authenticator setup key has been generated.", "success")
    return redirect(url_for("auth.mfa_setup"))


@auth_bp.route("/mfa/verify", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def mfa_verify() -> Any:
    user = _pending_mfa_user("verify")
    if user is None:
        if session.get("user_id"):
            return redirect(url_for("main.dashboard"))
        session.clear()
        return redirect(url_for("auth.login"))
    if not user["mfa_enabled"]:
        session["mfa_pending_mode"] = "setup"
        return redirect(url_for("auth.mfa_setup"))
    if not user["mfa_secret"]:
        session.clear()
        flash(
            "MFA needs administrator attention. Contact your school administrator.",
            "error",
        )
        return redirect(url_for("auth.login"))

    form = MfaCodeForm()
    if form.validate_on_submit():
        code = form.code.data.strip().upper()
        normalized_code = code.replace("-", "").replace(" ", "")
        secret = decrypt_mfa_secret(user["mfa_secret"])
        valid_code = pyotp.TOTP(secret).verify(code, valid_window=1)
        used_backup_code = False
        if not valid_code and len(normalized_code) == 12:
            used_backup_code = consume_mfa_backup_code(
                user["id"],
                normalized_code,
                request.remote_addr,
            )
            valid_code = used_backup_code
        if not valid_code:
            locked = _record_auth_failure(
                user,
                "mfa_failure",
                request.remote_addr,
            )
            if locked:
                session.clear()
                flash(LOGIN_FAILURE_MESSAGE, "error")
                return redirect(url_for("auth.login"))
            flash("The verification code is invalid. Try again.", "error")
        else:
            if used_backup_code:
                record_audit_event(
                    user["id"],
                    "mfa_backup_login",
                    request.remote_addr,
                )
            must_change_password = _establish_authenticated_session(
                user,
                request.remote_addr,
            )
            if must_change_password:
                return redirect(url_for("auth.change_password"))
            return redirect(url_for("main.dashboard"))

    response = make_response(render_template("auth/mfa_verify.html", form=form))
    return _no_store(response)


@auth_bp.post("/logout")
@login_required
def logout() -> Any:
    record_audit_event(
        session["user_id"],
        "logout",
        request.remote_addr,
    )
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
@limiter.limit("5 per minute")
def change_password() -> Any:
    form = PasswordChangeForm()
    if form.validate_on_submit():
        user = get_user_auth_data(session["user_id"])
        if (
            user is None
            or not user["is_active"]
            or not verify_password(user["password_hash"], form.current_password.data)
        ):
            flash("Current password is incorrect.", "error")
        elif form.current_password.data == form.new_password.data:
            flash("Choose a new password that differs from your current password.", "error")
        else:
            updated = change_user_password(
                session["user_id"],
                password_hasher.hash(form.new_password.data),
                request.remote_addr,
            )
            if not updated:
                session.clear()
                return redirect(url_for("auth.login"))
            session["must_change_password"] = False
            flash("Your password has been updated.", "success")
            return redirect(url_for("main.dashboard"))
    return render_template("auth/change_password.html", form=form)


@auth_bp.post("/api-token")
@login_required
@limiter.limit("5 per minute")
def create_api_token() -> Any:
    issued_at = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": str(session["user_id"]),
            "role": session["role"],
            "scope": "profile:read dashboard:read",
            "iat": issued_at,
            "exp": issued_at + timedelta(minutes=15),
            "iss": "greenfield-academy",
            "aud": "greenfield-api",
        },
        current_app.config["JWT_SECRET"],
        algorithm="HS256",
    )
    response = make_response(
        render_template("auth/api_token.html", access_token=token)
    )
    return _no_store(response)
