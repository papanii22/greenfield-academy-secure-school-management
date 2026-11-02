from functools import wraps
from typing import Any, Callable, TypeVar, cast

from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)
from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import EqualTo, InputRequired, Length, Regexp

from database.db import (
    change_user_password,
    get_user_auth_data,
    get_user_by_email,
    record_audit_event,
)


auth_bp = Blueprint("auth", __name__)
F = TypeVar("F", bound=Callable[..., Any])
password_hasher = PasswordHasher()
LOGIN_FAILURE_MESSAGE = "Invalid email or password."


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


def login_required(view: F) -> F:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if "user_id" not in session:
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
            if "user_id" not in session:
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


@auth_bp.route("/login", methods=["GET", "POST"])
def login() -> Any:
    if "user_id" in session:
        if session.get("must_change_password"):
            return redirect(url_for("auth.change_password"))
        return redirect(url_for("main.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = get_user_by_email(email)
        authenticated = bool(
            user
            and user["is_active"]
            and verify_password(user["password_hash"], form.password.data)
        )

        if not authenticated:
            record_audit_event(
                user["id"] if user else None,
                "login_failure",
                request.remote_addr,
            )
            flash(LOGIN_FAILURE_MESSAGE, "error")
            return render_template("auth/login.html", form=form)

        record_audit_event(user["id"], "login_success", request.remote_addr)
        session.clear()
        session.permanent = True
        session["user_id"] = user["id"]
        session["full_name"] = user["full_name"]
        session["role"] = user["role"]
        session["must_change_password"] = user.get("must_change_password", False)
        session["show_branded_loader"] = True
        if session["must_change_password"]:
            return redirect(url_for("auth.change_password"))
        return redirect(url_for("main.dashboard"))

    return render_template("auth/login.html", form=form)


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
