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
from database import hub
from database import school
from grading import grade_for
from reporting import csv_response, report_response
from score_upload import MAX_UPLOAD_BYTES, ScoreFileError, parse_score_csv


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
        widgets=hub.dashboard_widgets(session["user_id"], "teacher", term),
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

@teacher_bp.post("/teacher/classes/<int:class_id>/results/<int:subject_id>/upload")
@role_required("teacher")
def upload_results(class_id: int, subject_id: int) -> Any:
    term = parse_term(request.form.get("term"))
    back = url_for("teacher.results", class_id=class_id, subject_id=subject_id, term=term)
    upload = request.files.get("scores_file")
    if upload is None or not upload.filename:
        flash("Choose a CSV file to upload.", "error")
        return redirect(back)
    if not upload.filename.lower().endswith(".csv"):
        flash("Upload a .csv file. In Excel use Save As > CSV UTF-8.", "error")
        return redirect(back)
    try:
        scores = parse_score_csv(upload.stream.read(MAX_UPLOAD_BYTES + 1))
        saved = school.save_uploaded_results(
            session["user_id"], class_id, subject_id, term, scores, request.remote_addr
        )
    except (ScoreFileError, ValueError) as error:
        flash(str(error), "error")
        return redirect(back)
    if saved is None:
        abort(403)
    flash(f"Uploaded {saved} score{'s' if saved != 1 else ''} for term {term}.", "success")
    return redirect(back)


@teacher_bp.get("/teacher/classes/<int:class_id>/results/<int:subject_id>/template.csv")
@role_required("teacher")
def results_template(class_id: int, subject_id: int) -> Any:
    """The class list with any scores already entered, ready to fill in and upload."""
    term = parse_term(request.args.get("term"))
    roster = school.get_class_roster_for_results(session["user_id"], class_id, subject_id, term)
    if roster is None:
        abort(403)
    class_name = roster[0]["class_name"] if roster else f"class-{class_id}"
    subject_name = roster[0]["subject_name"] if roster else f"subject-{subject_id}"
    slug = "-".join(f"{class_name} {subject_name} term {term}".lower().split())
    return csv_response(
        [(student["student_number"], student["full_name"], student["score"]) for student in roster],
        ["student_number", "student", "score"],
        f"scores-{slug}.csv",
    )


@teacher_bp.get(
    "/teacher/classes/<int:class_id>/exports/<report>/<output_format>"
)
@role_required("teacher")
def export_class_report(class_id: int, report: str, output_format: str) -> Any:
    if report not in {"attendance", "results"}:
        abort(404)
    if output_format not in {"csv", "pdf"}:
        abort(404)

    if report == "attendance":
        raw_date = request.args.get("date")
        try:
            attendance_date = date.fromisoformat(raw_date) if raw_date else date.today()
        except ValueError:
            abort(400)
        roster = school.get_class_roster_for_attendance(
            session["user_id"],
            class_id,
            attendance_date,
        )
        if roster is None:
            abort(403)
        class_name = roster[0]["class_name"] if roster else f"Class {class_id}"
        return report_response(
            [
                (
                    attendance_date,
                    class_name,
                    student["student_number"],
                    student["full_name"],
                    student["status"],
                )
                for student in roster
            ],
            ["Date", "Class", "Student number", "Student", "Status"],
            f"Attendance report for {class_name} ({attendance_date})",
            f"teacher-class-{class_id}-attendance-{attendance_date}",
            output_format,
        )

    raw_subject_id = request.args.get("subject_id", "")
    try:
        subject_id = int(raw_subject_id)
    except ValueError:
        abort(400)
    if subject_id < 1:
        abort(400)
    term = parse_term(request.args.get("term"))
    roster = school.get_class_roster_for_results(
        session["user_id"],
        class_id,
        subject_id,
        term,
    )
    if roster is None:
        abort(403)
    class_name = roster[0]["class_name"] if roster else f"Class {class_id}"
    subject_name = roster[0]["subject_name"] if roster else f"Subject {subject_id}"
    return report_response(
        [
            (
                class_name,
                student["student_number"],
                student["full_name"],
                subject_name,
                term,
                student["score"],
                grade_for(student["score"]) if student["score"] is not None else "",
            )
            for student in roster
        ],
        [
            "Class",
            "Student number",
            "Student",
            "Subject",
            "Term",
            "Score",
            "Grade",
        ],
        f"Results report for {class_name} - {subject_name} (Term {term})",
        f"teacher-class-{class_id}-subject-{subject_id}-term-{term}",
        output_format,
    )
