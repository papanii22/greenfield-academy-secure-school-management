import click
import hashlib
import os
import secrets
from argon2 import PasswordHasher
from flask import Flask, g, jsonify, render_template, request, session
from flask_wtf.csrf import CSRFError, CSRFProtect
from psycopg import errors
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import safe_join

from Routes.admin import admin_bp
from Routes.api import api_bp
from Routes.auth import auth_bp
from Routes.main import main_bp
from Routes.teacher import teacher_bp
from config import Config
from database.db import connect_database, record_audit_event
from extensions import limiter


csrf = CSRFProtect()

CURRICULUM_SUBJECTS = (
    "English",
    "English Language",
    "English Literature",
    "Mathematics",
    "Science",
    "Biology",
    "Chemistry",
    "Physics",
    "Computing",
    "ICT",
    "Computer Science",
    "Art and Design",
    "Design and Technology",
    "Geography",
    "History",
    "Music",
    "Physical Education",
    "PSHE",
    "Religious Education",
    "French",
    "Spanish",
    "Business Studies",
    "Economics",
    "Accounting",
    "Ghanaian Language",
)
CURRICULUM_CLASSES = (
    "Nursery",
    "Reception",
    "Year 1",
    "Year 2",
    "Year 3",
    "Year 4",
    "Year 5",
    "Year 6",
    "Year 7",
    "Year 8",
    "Year 9",
    "Year 10",
    "Year 11",
    "Year 12",
    "Year 13",
)


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

    static_versions: dict[str, tuple[int, str]] = {}

    @app.url_defaults
    def add_static_version(endpoint: str, values: dict) -> None:
        """Fingerprint static URLs so browsers can cache them for a year."""
        if endpoint != "static" or "v" in values:
            return
        filename = values.get("filename")
        try:
            path = safe_join(app.static_folder, filename)
            modified = os.stat(path).st_mtime_ns
            cached = static_versions.get(filename)
            if cached is None or cached[0] != modified:
                with open(path, "rb") as asset:
                    digest = hashlib.sha256(asset.read()).hexdigest()[:10]
                cached = static_versions[filename] = (modified, digest)
            values["v"] = cached[1]
        except (OSError, TypeError):
            return

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
        if (
            request.endpoint == "static"
            and request.args.get("v")
            and response.status_code == 200
        ):
            # The URL changes whenever the file does, so it never needs revalidating.
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error: CSRFError) -> tuple[str, int]:
        return render_template(
            "errors/error.html",
            title="Request could not be verified",
            status_code=400,
            message="The request could not be processed. Refresh the page and try again.",
        ), 400

    @app.errorhandler(403)
    def forbidden(_error: Exception) -> tuple[str, int]:
        return render_template(
            "errors/error.html",
            title="Access denied",
            status_code=403,
            message="You do not have permission to view this page.",
        ), 403

    @app.errorhandler(404)
    def not_found(_error: Exception) -> tuple[str, int]:
        return render_template(
            "errors/error.html",
            title="Page not found",
            status_code=404,
            message="The page you requested could not be found.",
        ), 404

    @app.errorhandler(400)
    def bad_request(_error: Exception) -> tuple[str, int]:
        return render_template(
            "errors/error.html",
            title="Invalid request",
            status_code=400,
            message="The request contains invalid information. Review your input and try again.",
        ), 400

    @app.errorhandler(413)
    def export_too_large(_error: Exception) -> tuple[str, int]:
        return render_template(
            "errors/error.html",
            title="Export is too large",
            status_code=413,
            message="This report exceeds the 5,000-row export limit. Narrow the date range or select a smaller report.",
        ), 413

    @app.errorhandler(429)
    def too_many_requests(_error: Exception) -> tuple[str, int]:
        return render_template(
            "errors/error.html",
            title="Too many attempts",
            status_code=429,
            message="Too many requests were received. Wait a few minutes and try again.",
        ), 429

    @app.errorhandler(500)
    def internal_error(_error: Exception) -> tuple[str, int]:
        return render_template(
            "errors/error.html",
            title="Something went wrong",
            status_code=500,
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

    @app.cli.command("seed-curriculum")
    def seed_curriculum() -> None:
        """Add missing curriculum classes and subjects to PostgreSQL."""
        click.echo(
            "This command inserts missing curriculum classes and subjects into "
            "the configured database. New classes are temporarily assigned to "
            "an active administrator because the schema requires a class teacher. "
            "It does not create users or teacher-subject assignments."
        )
        if not click.confirm("Continue?", default=False):
            click.echo("Curriculum seeding cancelled.")
            return

        subjects_added = 0
        subjects_already_existed = 0
        classes_added = 0
        classes_already_existed = 0
        with connect_database() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id
                    FROM users
                    WHERE role = 'admin' AND is_active
                    ORDER BY id
                    """
                )
                admin_ids = [row["id"] for row in cursor.fetchall()]
                if not admin_ids:
                    raise click.ClickException(
                        "No active administrator account is available to assign "
                        "as the temporary class teacher."
                    )
                admin_id = (
                    admin_ids[0]
                    if len(admin_ids) == 1
                    else int(click.prompt(
                        "Active administrator ID for new classes",
                        type=click.Choice([str(value) for value in admin_ids]),
                    ))
                )

                for subject_name in CURRICULUM_SUBJECTS:
                    cursor.execute(
                        """
                        INSERT INTO subjects (name)
                        VALUES (%s)
                        ON CONFLICT (name) DO NOTHING
                        RETURNING id
                        """,
                        (subject_name,),
                    )
                    if cursor.fetchone() is None:
                        subjects_already_existed += 1
                    else:
                        subjects_added += 1

                for class_name in CURRICULUM_CLASSES:
                    cursor.execute(
                        """
                        INSERT INTO classes (name, class_teacher_id)
                        VALUES (%s, %s)
                        ON CONFLICT (name) DO NOTHING
                        RETURNING id
                        """,
                        (class_name, admin_id),
                    )
                    if cursor.fetchone() is None:
                        classes_already_existed += 1
                    else:
                        classes_added += 1

                record_audit_event(
                    None,
                    "curriculum_seeded",
                    None,
                    connection=connection,
                )

        click.echo(
            f"Subjects: added {subjects_added}; "
            f"{subjects_already_existed} already existed."
        )
        click.echo(
            f"Classes: added {classes_added}; "
            f"{classes_already_existed} already existed."
        )
        click.echo(
            "No teacher-subject assignments were created. New classes use the "
            "selected admin temporarily; creating teachers through the admin "
            "form will assign their selected subjects and update the class teacher."
        )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=app.config["FLASK_DEBUG"])
