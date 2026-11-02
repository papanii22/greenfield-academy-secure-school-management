from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_wtf import FlaskForm
from wtforms import DateField
from wtforms.validators import DataRequired

from Routes.auth import role_required
from Routes.validation import parse_term
from database import school
from grading import grade_for


teacher_bp = Blueprint("teacher", __name__)


class AttendanceForm(FlaskForm):
    attendance_date = DateField(
        "Attendance date",
        format="%Y-%m-%d",
        validators=[DataRequired()],
    )


@teacher_bp.get("/teacher")
@role_required("teacher")
def index() -> str:
    term = parse_term(request.args.get("term"))
    dashboard = school.get_teacher_dashboard(session["user_id"], term)
    return render_template(
        "teacher/dashboard.html",
        dashboard=dashboard,
        role_label="Teacher",
        with_charts=True,
    )


@teacher_bp.route(
    "/teacher/classes/<int:class_id>/attendance",
    methods=["GET", "POST"],
)
@role_required("teacher")
def attendance(class_id: int) -> Any:
    form = AttendanceForm()
    if request.method == "POST":
        attendance_date = form.attendance_date.data
    else:
        raw_date = request.args.get("date")
        try:
            attendance_date = date.fromisoformat(raw_date) if raw_date else date.today()
        except ValueError:
            abort(400)
        form.attendance_date.data = attendance_date

    roster = school.get_class_roster_for_attendance(
        session["user_id"],
        class_id,
        attendance_date or date.today(),
    )
    if roster is None:
        abort(403)

    if request.method == "POST" and form.validate_on_submit():
        statuses = {
            student["id"]: request.form.get(f"attendance_{student['id']}", "")
            for student in roster
        }
        try:
            saved = school.save_class_attendance(
                session["user_id"],
                class_id,
                form.attendance_date.data,
                statuses,
                request.remote_addr,
            )
        except ValueError as error:
            flash(str(error), "error")
        else:
            if not saved:
                abort(403)
            flash("Attendance saved for the selected date.", "success")
            return redirect(
                url_for(
                    "teacher.attendance",
                    class_id=class_id,
                    date=form.attendance_date.data.isoformat(),
                )
            )

    return render_template(
        "teacher/attendance.html",
        form=form,
        roster=roster,
        class_id=class_id,
    )


@teacher_bp.route(
    "/teacher/classes/<int:class_id>/results/<int:subject_id>",
    methods=["GET", "POST"],
)
@role_required("teacher")
def results(class_id: int, subject_id: int) -> Any:
    form = FlaskForm()
    term_value = request.form.get("term") if request.method == "POST" else request.args.get("term")
    term = parse_term(term_value)
    roster = school.get_class_roster_for_results(
        session["user_id"],
        class_id,
        subject_id,
        term,
    )
    if roster is None:
        abort(403)

    if request.method == "POST" and form.validate_on_submit():
        scores: dict[int, Decimal] = {}
        try:
            for student in roster:
                raw_score = request.form.get(f"score_{student['id']}", "").strip()
                score = Decimal(raw_score)
                grade_for(score)
                scores[student["id"]] = score
            saved = school.save_class_results(
                session["user_id"],
                class_id,
                subject_id,
                term,
                scores,
                request.remote_addr,
            )
        except (InvalidOperation, ValueError):
            flash(
                "Enter a valid score from 0 to 100 for every student.",
                "error",
            )
        else:
            if not saved:
                abort(403)
            flash("Results saved for the selected term.", "success")
            return redirect(
                url_for(
                    "teacher.results",
                    class_id=class_id,
                    subject_id=subject_id,
                    term=term,
                )
            )

    for student in roster:
        if student["score"] is not None:
            student["grade"] = grade_for(student["score"])
    return render_template(
        "teacher/results.html",
        form=form,
        roster=roster,
        class_id=class_id,
        subject_id=subject_id,
        term=term,
    )
