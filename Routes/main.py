from flask import Blueprint, redirect, render_template, request, session, url_for

from Routes.auth import login_required, role_required
from Routes.validation import parse_term
from database import school


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
        "teacher": "teacher.index",
        "student": "main.student_dashboard",
    }
    endpoint = role_endpoints.get(session.get("role"))
    if endpoint is None:
        session.clear()
        return redirect(url_for("auth.login"))
    return redirect(url_for(endpoint))


@main_bp.get("/student")
@role_required("student")
def student_dashboard():
    term = parse_term(request.args.get("term"))
    dashboard = school.get_student_dashboard(session["user_id"], term)
    if dashboard is None:
        return render_template(
            "errors/error.html",
            title="Student record unavailable",
            message="Your student record could not be found. Contact a school administrator.",
        ), 404
    charts = {
        "attendance": {
            "type": "doughnut",
            "labels": ["Present", "Late", "Absent"],
            "datasets": [
                {
                    "label": "Attendance days",
                    "data": [
                        dashboard["attendance_counts"]["present"],
                        dashboard["attendance_counts"]["late"],
                        dashboard["attendance_counts"]["absent"],
                    ],
                }
            ],
        },
        "scores": {
            "type": "bar",
            "labels": [result["subject_name"] for result in dashboard["results"]],
            "datasets": [
                {
                    "label": "Score (%)",
                    "data": [result["score"] for result in dashboard["results"]],
                },
                {
                    "label": "Pass mark (%)",
                    "data": [50 for _result in dashboard["results"]],
                    "type": "line",
                },
            ],
            "max": 100,
        },
        "averages": {
            "type": "line",
            "labels": [f"Term {item['term']}" for item in dashboard["term_averages"]],
            "datasets": [
                {
                    "label": "Average score (%)",
                    "data": [item["average"] for item in dashboard["term_averages"]],
                }
            ],
            "max": 100,
        },
    }
    return render_template(
        "student/dashboard.html",
        dashboard=dashboard,
        charts=charts,
        role_label="Student",
        with_charts=True,
    )
