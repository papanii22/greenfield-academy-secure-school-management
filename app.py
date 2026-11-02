import click
import secrets
from argon2 import PasswordHasher
from flask import Flask, g, jsonify, render_template, session
from flask_wtf.csrf import CSRFError, CSRFProtect
from psycopg import errors
from werkzeug.middleware.proxy_fix import ProxyFix

from Routes.admin import admin_bp
from Routes.api import api_bp
from Routes.auth import auth_bp
from Routes.main import main_bp
from Routes.teacher import teacher_bp
from config import Config
from database.db import connect_database, record_audit_event
from extensions import limiter


csrf = CSRFProtect()


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(
        __name__,
        template_folder="Templates",
        static_folder="public/Static",
        static_url_path="/Static",
    )
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    if app.config.get("TRUST_PROXY_HEADERS"):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    if app.config.get("TESTING") and not (
        test_config and "RATELIMIT_ENABLED" in test_config
    ):
        app.config["RATELIMIT_ENABLED"] = False
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(teacher_bp)
    app.register_blueprint(api_bp)
    limiter.init_app(app)

    @app.before_request
    def create_csp_nonce() -> None:
        g.csp_nonce = secrets.token_urlsafe(18)

    csrf.init_app(app)

    @app.get("/healthz")
    def health_check():
        return jsonify({"status": "ok"})

    @app.context_processor
    def template_context() -> dict[str, str | bool]:
        return {
            "csp_nonce": g.csp_nonce,
            "show_branded_loader": bool(session.pop("show_branded_loader", False)),
        }

    @app.after_request
    def set_content_security_policy(response):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{g.csp_nonce}'; "
            "style-src 'self'; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'"
        )
        return response

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error: CSRFError) -> tuple[str, int]:
        return render_template(
            "errors/error.html",
            title="Request could not be verified",
            message="The request could not be processed. Refresh the page and try again.",
        ), 400

    @app.errorhandler(403)
    def forbidden(_error: Exception) -> tuple[str, int]:
        return render_template(
            "errors/error.html",
            title="Access denied",
            message="You do not have permission to view this page.",
        ), 403

    @app.errorhandler(404)
    def not_found(_error: Exception) -> tuple[str, int]:
        return render_template(
            "errors/error.html",
            title="Page not found",
            message="The page you requested could not be found.",
        ), 404

    @app.errorhandler(400)
    def bad_request(_error: Exception) -> tuple[str, int]:
        return render_template(
            "errors/error.html",
            title="Invalid request",
            message="The request contains invalid information. Review your input and try again.",
        ), 400

    @app.errorhandler(413)
    def export_too_large(_error: Exception) -> tuple[str, int]:
        return render_template(
            "errors/error.html",
            title="Export is too large",
            message="This report exceeds the 5,000-row export limit. Narrow the date range or select a smaller report.",
        ), 413

    @app.errorhandler(429)
    def too_many_requests(_error: Exception) -> tuple[str, int]:
        return render_template(
            "errors/error.html",
            title="Too many attempts",
            message="Too many requests were received. Wait a few minutes and try again.",
        ), 429

    @app.errorhandler(500)
    def internal_error(_error: Exception) -> tuple[str, int]:
        return render_template(
            "errors/error.html",
            title="Something went wrong",
            message="The request could not be completed. Please try again later.",
        ), 500

    @app.cli.command("create-admin")
    def create_admin() -> None:
        """Create the first administrator using hidden password prompts."""
        full_name = click.prompt("Administrator full name").strip()
        email = click.prompt("Administrator email").strip().lower()
        if not full_name or len(full_name) > 120:
            raise click.ClickException("Full name must be between 1 and 120 characters.")
        if not email or len(email) > 254 or "@" not in email:
            raise click.ClickException("Enter a valid email address (maximum 254 characters).")

        password = click.prompt(
            "Administrator password",
            hide_input=True,
            confirmation_prompt=True,
        )
        if len(password) < 12:
            raise click.ClickException("Password must be at least 12 characters.")

        hasher = PasswordHasher()
        try:
            with connect_database() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO users (full_name, email, password_hash, role)
                        VALUES (%s, %s, %s, 'admin')
                        RETURNING id
                        """,
                        (full_name, email, hasher.hash(password)),
                    )
                    row = cursor.fetchone()
                    record_audit_event(
                        row["id"],
                        "admin_account_created",
                        None,
                        connection=connection,
                    )
        except errors.UniqueViolation as error:
            raise click.ClickException("An account with that email already exists.") from error

        click.echo("Administrator account created.")

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=app.config["FLASK_DEBUG"])
