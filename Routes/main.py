from flask import Blueprint, redirect, render_template, session, url_for

from Routes.auth import login_required


main_bp = Blueprint("main", __name__)


@main_bp.get("/")
def index():
    if "user_id" in session:
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("auth.login"))


@main_bp.get("/dashboard")
@login_required
def dashboard():
    role_endpoints = {
        "admin": "admin.index",
        "teacher": "main.teacher_dashboard",
        "student": "main.student_dashboard",
    }
    endpoint = role_endpoints.get(session.get("role"))
    if endpoint is None:
        session.clear()
        return redirect(url_for("auth.login"))
    return redirect(url_for(endpoint))


@main_bp.get("/teacher")
@login_required
def teacher_dashboard():
    if session.get("role") != "teacher":
        return render_template(
            "errors/error.html",
            title="Access denied",
            message="You do not have permission to view this page.",
        ), 403
    return render_template("dashboard.html", role_label="Teacher")


@main_bp.get("/student")
@login_required
def student_dashboard():
    if session.get("role") != "student":
        return render_template(
            "errors/error.html",
            title="Access denied",
            message="You do not have permission to view this page.",
        ), 403
    return render_template("dashboard.html", role_label="Student")
