import os

import pytest


os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-not-for-production")
os.environ.setdefault("FLASK_DEBUG", "false")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")


@pytest.fixture
def app():
    from app import create_app

    test_app = create_app({"TESTING": True})
    yield test_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin_dashboard(monkeypatch):
    dashboard = {
        "term": 1,
        "total_students": 0,
        "total_teachers": 0,
        "attendance_rate": 0,
        "school_average": None,
        "attendance_trend": [],
        "subject_averages": [],
        "grade_counts": {grade: 0 for grade in "ABCDF"},
        "recent_activity": [],
    }

    def get_dashboard(term):
        return {**dashboard, "term": term}

    monkeypatch.setattr("Routes.admin.school.get_admin_dashboard", get_dashboard)
    return dashboard
