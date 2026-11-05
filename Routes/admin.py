import math
import secrets
from datetime import date, timedelta
from typing import Any

from argon2 import PasswordHasher
from flask import (
    Blueprint,
    abort,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_wtf import FlaskForm
from psycopg import errors
from wtforms import (
    SelectChoice,
    SelectField,
    SelectMultipleField,
    StringField,
    SubmitField,
)
from wtforms.validators import InputRequired, Length, Optional, Regexp

from Routes.auth import role_required
from Routes.validation import parse_page, parse_term
from database import accounts
from database import school
from grading import grade_for
from reporting import MAX_REPORT_ROWS, report_response


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")
password_hasher = PasswordHasher()


class UserFieldsForm(FlaskForm):
    full_name = StringField(
        "Full name",
        validators=[InputRequired(), Length(max=120)],
    )
    email = StringField(
        "Email address",
        validators=[
            InputRequired(),
            Length(max=254),
            Regexp(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", message="Enter a valid email address."),
        ],
    )
    submit = SubmitField("Save changes")


class CreateUserForm(UserFieldsForm):
    role = SelectField(
        "Account type",
        choices=[
            SelectChoice("teacher", "Teacher"),
            SelectChoice("student", "Student"),
        ],
        validators=[InputRequired()],
    )
    class_id = SelectField("Class", choices=[], validators=[Optional()])
    student_number = StringField(
        "Student number",
        validators=[
            Optional(),
            Length(max=20),
            Regexp(
                r"^GA-2026-\d{3}$",
                message="Use the format GA-2026-001.",
            ),
        ],
    )
    subject_ids = SelectMultipleField("Assigned subjects", choices=[])


class SignupReviewForm(FlaskForm):
    class_id = SelectField("Class", choices=[], validators=[InputRequired()])
    student_number = StringField(
        "Student number",
        validators=[
            Optional(),
            Length(max=20),
            Regexp(
                r"^GA-2026-\d{3}$",
                message="Use the format GA-2026-001.",
            ),
        ],
    )
    subject_ids = SelectMultipleField("Assigned subjects", choices=[])


@admin_bp.get("")
@role_required("admin")
def index() -> str:
    term = parse_term(request.args.get("term"))
    dashboard = school.get_admin_dashboard(term)
    return render_template(
        "admin/dashboard.html",
        dashboard=dashboard,
        role_label="Administrator",
        with_charts=True,
    )


@admin_bp.get("/users")
@role_required("admin")
def users() -> str:
    search = request.args.get("q", "").strip()
    role = request.args.get("role", "")
    if len(search) > 100 or role not in {"", "admin", "teacher", "student"}:
        abort(400)
    page_number = parse_page(request.args.get("page"))
    result = school.list_users(search, role or None, page_number)
    page_count = max(1, math.ceil(result["total"] / result["per_page"]))
    if page_number > page_count:
        abort(404)
    return render_template(
        "admin/users.html",
        result=result,
        search=search,
        role=role,
        page_count=page_count,
        role_label="Administrator",
    )


@admin_bp.get("/pending-accounts")
@role_required("admin")
def pending_accounts() -> str:
    return render_template(
        "admin/pending_accounts.html",
        signups=accounts.list_pending_signups(),
        role_label="Administrator",
    )


@admin_bp.route("/pending-accounts/<int:user_id>", methods=["GET", "POST"])
@role_required("admin")
def review_signup(user_id: int) -> Any:
    signup = accounts.get_pending_signup(user_id)
    if signup is None:
        abort(404)
    options = school.get_create_user_options()
    form = SignupReviewForm()
    form.class_id.choices = [
        SelectChoice("", "Choose a class"),
        *[
            SelectChoice(str(item["id"]), item["name"])
            for item in options["classes"]
        ],
    ]
    form.subject_ids.choices = [
        SelectChoice(str(item["id"]), item["name"])
        for item in options["subjects"]
    ]
    if form.validate_on_submit():
        class_id = int(form.class_id.data)
        student_number = (form.student_number.data or "").strip() or None
        subject_ids = [int(value) for value in form.subject_ids.data]
        if signup["role"] == "student" and student_number is None:
            form.student_number.errors.append("Student number is required.")
        elif signup["role"] == "teacher" and len(subject_ids) not in {2, 3}:
            form.subject_ids.errors.append("Choose two or three subjects.")
        else:
            try:
                approved = accounts.approve_signup(
                    user_id,
                    class_id,
                    student_number,
                    subject_ids,
                    session["user_id"],
                    request.remote_addr,
                )
            except errors.UniqueViolation:
                flash("That student number is already in use.", "error")
            except errors.ForeignKeyViolation:
                flash("The selected class or subject is no longer available.", "error")
            except ValueError as error:
                flash(str(error), "error")
            else:
                if not approved:
                    abort(404)
                flash("Account request approved.", "success")
                return redirect(url_for("admin.pending_accounts"))

    return render_template(
        "admin/signup_review.html",
        signup=signup,
        form=form,
        role_label="Administrator",
    )


@admin_bp.post("/pending-accounts/<int:user_id>/reject")
@role_required("admin")
def reject_signup(user_id: int):
    if not accounts.reject_signup(
        user_id,
        session["user_id"],
        request.remote_addr,
    ):
        abort(404)
    flash("Account request rejected.", "success")
    return redirect(url_for("admin.pending_accounts"))


@admin_bp.get("/password-reset-requests")
@role_required("admin")
def password_reset_requests() -> str:
    return render_template(
        "admin/password_reset_requests.html",
        reset_requests=accounts.list_password_reset_requests(),
        role_label="Administrator",
    )


@admin_bp.route(
    "/users/<int:user_id>/reset-password",
    methods=["GET", "POST"],
)
@role_required("admin")
def reset_user_password(user_id: int) -> Any:
    user = school.get_user_edit_data(user_id)
    if user is None or user["role"] not in {"teacher", "student"} or not user["is_active"]:
        abort(404)

    if request.method == "GET":
        return render_template(
            "admin/password_reset_confirm.html",
            user=user,
            role_label="Administrator",
        )

    temporary_password = secrets.token_urlsafe(18)
    if not accounts.reset_school_user_password(
        user_id,
        password_hasher.hash(temporary_password),
        session["user_id"],
        request.remote_addr,
    ):
        abort(404)
    response = make_response(
        render_template(
            "admin/password_reset_complete.html",
            user=user,
            temporary_password=temporary_password,
            role_label="Administrator",
        )
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@admin_bp.route("/users/new", methods=["GET", "POST"])
@role_required("admin")
def create_user() -> Any:
    form = CreateUserForm()
    options = school.get_create_user_options()
    form.class_id.choices = [SelectChoice("", "Choose a class")] + [
        SelectChoice(str(item["id"]), item["name"]) for item in options["classes"]
    ]
    form.subject_ids.choices = [
        SelectChoice(str(item["id"]), item["name"]) for item in options["subjects"]
    ]

    if form.validate_on_submit():
        role = form.role.data
        class_id = int(form.class_id.data) if form.class_id.data else None
        subject_ids = [int(value) for value in form.subject_ids.data]
        student_number = (form.student_number.data or "").strip() or None
        if role == "student" and student_number is None:
            form.student_number.errors.append("Student number is required.")
        elif role == "teacher" and len(subject_ids) not in {2, 3}:
            form.subject_ids.errors.append("Choose two or three subjects.")
        else:
            temporary_password = secrets.token_urlsafe(18)
            try:
                school.create_school_user(
                    full_name=form.full_name.data.strip(),
                    email=form.email.data.strip().lower(),
                    role=role,
                    password_hash=password_hasher.hash(temporary_password),
                    class_id=class_id,
                    student_number=student_number,
                    subject_ids=subject_ids,
                    actor_id=session["user_id"],
                    ip_address=request.remote_addr,
                )
            except ValueError as error:
                flash(str(error), "error")
            except errors.UniqueViolation:
                flash(
                    "That email or student number is already in use.",
                    "error",
                )
            except errors.ForeignKeyViolation:
                flash(
                    "The selected class or subject is no longer available.",
                    "error",
                )
            else:
                response = make_response(
                    render_template(
                        "admin/user_created.html",
                        full_name=form.full_name.data.strip(),
                        email=form.email.data.strip().lower(),
                        role=role,
                        temporary_password=temporary_password,
                        role_label="Administrator",
                    ),
                    201,
                )
                response.headers["Cache-Control"] = "no-store"
                return response

    return render_template(
        "admin/user_form.html",
        form=form,
        options=options,
        editing=False,
        role_label="Administrator",
    )


@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@role_required("admin")
def edit_user(user_id: int) -> Any:
    user = school.get_user_edit_data(user_id)
    if user is None:
        abort(404)

    form = UserFieldsForm()
    if request.method == "GET":
        form.full_name.data = user["full_name"]
        form.email.data = user["email"]
    if form.validate_on_submit():
        try:
            updated = school.update_user(
                user_id,
                form.full_name.data.strip(),
                form.email.data.strip().lower(),
                session["user_id"],
                request.remote_addr,
            )
        except errors.UniqueViolation:
            flash("That email address is already in use.", "error")
        else:
            if not updated:
                abort(404)
            flash("User details updated.", "success")
            return redirect(url_for("admin.users"))
    return render_template(
        "admin/user_form.html",
        form=form,
        user=user,
        editing=True,
        role_label="Administrator",
    )


@admin_bp.post("/users/<int:user_id>/<action>")
@role_required("admin")
def set_user_status(user_id: int, action: str):
    if action not in {"activate", "deactivate"}:
        abort(404)
    active = action == "activate"
    if not active and user_id == session["user_id"]:
        flash("You cannot deactivate your own administrator account.", "error")
        return redirect(url_for("admin.users"))
    updated = school.set_user_active(
        user_id,
        active,
        session["user_id"],
        request.remote_addr,
    )
    if not updated:
        abort(404)
    flash(
        "User account reactivated." if active else "User account deactivated.",
        "success",
    )
    return redirect(url_for("admin.users"))


@admin_bp.get("/audit")
@role_required("admin")
def audit() -> str:
    raw_user_id = request.args.get("user_id", "")
    if raw_user_id:
        try:
            user_id = int(raw_user_id)
        except ValueError:
            abort(400)
        if user_id < 1:
            abort(400)
    else:
        user_id = None

    action = request.args.get("action", "").strip()
    if len(action) > 120:
        abort(400)
    start_date = _parse_optional_date(request.args.get("start_date"))
    end_date = _parse_optional_date(request.args.get("end_date"))
    if start_date and end_date and end_date < start_date:
        abort(400)

    page_number = parse_page(request.args.get("page"))
    result = school.get_audit_logs(
        user_id,
        action,
        start_date,
        end_date,
        page_number,
    )
    page_count = max(1, math.ceil(result["total"] / result["per_page"]))
    if page_number > page_count:
        abort(404)
    return render_template(
        "admin/audit.html",
        result=result,
        filters={
            "user_id": raw_user_id,
            "action": action,
            "start_date": start_date.isoformat() if start_date else "",
            "end_date": end_date.isoformat() if end_date else "",
        },
        page_count=page_count,
        role_label="Administrator",
    )


@admin_bp.get("/exports/<report>/<output_format>")
@role_required("admin")
def export_report(report: str, output_format: str) -> Any:
    if report not in {"attendance", "results", "grades", "audit"}:
        abort(404)
    if output_format not in {"csv", "pdf"}:
        abort(404)

    term = parse_term(request.args.get("term"))
    end_date = _parse_optional_date(request.args.get("end_date")) or date.today()
    start_date = (
        _parse_optional_date(request.args.get("start_date"))
        or end_date - timedelta(days=29)
    )
    if end_date < start_date:
        abort(400)

    if report == "attendance":
        rows = school.get_admin_attendance_export_rows(start_date, end_date)
        if len(rows) > MAX_REPORT_ROWS:
            abort(413)
        return report_response(
            [
                (
                    row["attendance_date"],
                    row["class_name"],
                    row["student_number"],
                    row["student_name"],
                    row["status"],
                    row["marked_by"],
                )
                for row in rows
            ],
            ["Date", "Class", "Student number", "Student", "Status", "Marked by"],
            f"School attendance report ({start_date} to {end_date})",
            f"admin-attendance-{start_date}-{end_date}",
            output_format,
        )

    if report == "audit":
        raw_user_id = request.args.get("user_id", "")
        if raw_user_id:
            try:
                user_id = int(raw_user_id)
            except ValueError:
                abort(400)
            if user_id < 1:
                abort(400)
        else:
            user_id = None
        action = request.args.get("action", "").strip()
        if len(action) > 120:
            abort(400)
        rows = school.get_admin_audit_export_rows(
            start_date,
            end_date,
            user_id,
            action,
        )
        if len(rows) > MAX_REPORT_ROWS:
            abort(413)
        return report_response(
            [
                (row["created_at"], row["actor"], row["action"], row["ip_address"])
                for row in rows
            ],
            ["Time", "Account", "Action", "IP address"],
            f"Audit report ({start_date} to {end_date})",
            f"admin-audit-{start_date}-{end_date}",
            output_format,
        )

    results = school.get_admin_results_export_rows(term)
    if len(results) > MAX_REPORT_ROWS:
        abort(413)
    if report == "results":
        return report_response(
            [
                (
                    row["class_name"],
                    row["student_number"],
                    row["student_name"],
                    row["subject_name"],
                    row["term"],
                    row["score"],
                    grade_for(row["score"]),
                    row["entered_by"],
                )
                for row in results
            ],
            [
                "Class",
                "Student number",
                "Student",
                "Subject",
                "Term",
                "Score",
                "Grade",
                "Entered by",
            ],
            f"School results report (Term {term})",
            f"admin-results-term-{term}",
            output_format,
        )

    grade_counts = {grade: 0 for grade in "ABCDF"}
    for row in results:
        grade_counts[grade_for(row["score"])] += 1
    return report_response(
        [(grade, count) for grade, count in grade_counts.items()],
        ["Grade", "Result count"],
        f"School grade distribution (Term {term})",
        f"admin-grades-term-{term}",
        output_format,
    )


def _parse_optional_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        abort(400)
