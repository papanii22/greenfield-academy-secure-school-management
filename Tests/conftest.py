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
