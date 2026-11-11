import click
import csv
import hashlib
import os
import secrets
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from flask import Flask, g, jsonify, render_template, request, session
from flask.sessions import SecureCookieSessionInterface
from flask_wtf.csrf import CSRFError, CSRFProtect
from psycopg import errors
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import safe_join

from Routes.admin import admin_bp
from Routes.api import api_bp
from Routes.auth import auth_bp
from Routes.hub import hub_bp
from Routes.main import main_bp
from Routes.teacher import teacher_bp
from config import Config
from database import hub
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
_EARLY_YEARS = ("Nursery", "Reception")
_KS1 = ("Year 1", "Year 2")
_KS2 = ("Year 3", "Year 4", "Year 5", "Year 6")
_KS3 = ("Year 7", "Year 8", "Year 9")
_KS4 = ("Year 10", "Year 11")
_POST_16 = ("Year 12", "Year 13")
# Which classes take each subject, following the stages on the curriculum page.
SUBJECT_CLASSES = {
    "English": _EARLY_YEARS + _KS1 + _KS2 + _KS3,
    "English Language": _KS4,
    "English Literature": _KS4 + _POST_16,
    "Mathematics": _EARLY_YEARS + _KS1 + _KS2 + _KS3 + _KS4 + _POST_16,
    "Science": _KS1 + _KS2,
    "Biology": _KS3 + _KS4 + _POST_16,
    "Chemistry": _KS3 + _KS4 + _POST_16,
    "Physics": _KS3 + _KS4 + _POST_16,
    "Computing": _KS1 + _KS2 + _KS3,
    "ICT": _KS4,
    "Computer Science": _KS4 + _POST_16,
    "Art and Design": _EARLY_YEARS + _KS1 + _KS2 + _KS3 + _KS4,
    "Design and Technology": _KS1 + _KS2 + _KS3,
    "Geography": _KS1 + _KS2 + _KS3 + _KS4 + _POST_16,
    "History": _KS1 + _KS2 + _KS3 + _KS4 + _POST_16,
    "Music": _EARLY_YEARS + _KS1 + _KS2 + _KS3 + _KS4,
    "Physical Education": _EARLY_YEARS + _KS1 + _KS2 + _KS3 + _KS4,
    "PSHE": _EARLY_YEARS + _KS1 + _KS2 + _KS3,
    "Religious Education": _KS1 + _KS2 + _KS3,
    "French": _KS2 + _KS3 + _KS4,
    "Spanish": _KS2 + _KS3 + _KS4,
    "Business Studies": _KS4 + _POST_16,
    "Economics": _KS4 + _POST_16,
    "Accounting": _KS4 + _POST_16,
    "Ghanaian Language": _KS1 + _KS2 + _KS3 + _KS4,
}

MALE_FIRST_NAMES = (
    "Kwame", "Kofi", "Kwesi", "Yaw", "Kojo", "Kwabena", "Nii", "Ebo",
    "Selorm", "Edem", "Mawuli",
)
FEMALE_FIRST_NAMES = (
    "Ama", "Akosua", "Abena", "Adwoa", "Efua", "Esi", "Naa", "Afia", "Dzifa",
)
STUDENT_FIRST_NAMES = MALE_FIRST_NAMES + FEMALE_FIRST_NAMES
MALE_ENGLISH_NAMES = (
    "Emmanuel", "Samuel", "Daniel", "Michael", "Joseph", "David", "Benjamin",
    "Isaac", "Stephen", "Richard", "Francis", "Prince", "Eric", "Kelvin",
    "Bright", "Felix",
)
FEMALE_ENGLISH_NAMES = (
    "Grace", "Mary", "Abigail", "Esther", "Sarah", "Deborah", "Priscilla",
    "Gifty", "Linda", "Patience", "Mercy", "Rebecca", "Felicia", "Comfort",
    "Vivian", "Gloria",
)
# Students get a 10-digit index number that starts with 10100.
STUDENT_INDEX_FIRST = 1_010_000_000
STUDENT_INDEX_SPAN = 100_000
# Teachers get a 5-digit index number that starts with 10 (10000-10999).
TEACHER_INDEX_FIRST = 10_000
TEACHER_INDEX_SPAN = 1_000


def name_lengths(total: int) -> list[int]:
    """How many names each person gets: half have two, 3 in 10 have three and
    the rest have four, in random order."""
    three = round(total * 0.3)
    two = round(total * 0.5)
    lengths = [3] * three + [2] * two + [4] * (total - three - two)
    secrets.SystemRandom().shuffle(lengths)
    return lengths


def compose_name(english: str, ghanaian: str, surname: str, length: int) -> str:
    """Build a two, three or four part name from one person's name parts."""
    if length == 2:
        return f"{secrets.choice((english, ghanaian))} {surname}"
    if length == 3:
        return f"{english} {ghanaian} {surname}"
    second_surname = secrets.choice(
        [name for name in STUDENT_LAST_NAMES if name != surname]
    )
    return f"{english} {ghanaian} {second_surname} {surname}"


def person_name(base_name: str, length: int) -> str:
    """Turn a 'Ghanaian-first-name Surname' pair into a full name, adding an
    English first name matched to gender where the length calls for one."""
    ghanaian, surname = base_name.split(" ", 1)
    english_names = (
        FEMALE_ENGLISH_NAMES if ghanaian in FEMALE_FIRST_NAMES else MALE_ENGLISH_NAMES
    )
    return compose_name(secrets.choice(english_names), ghanaian, surname, length)


def teacher_display_name(full_name: str) -> str:
    """Prefix a teacher's name with Sir (male) or Mrs. (female)."""
    first_name = full_name.split(" ", 1)[0]
    female = first_name in FEMALE_FIRST_NAMES or first_name in FEMALE_ENGLISH_NAMES
    title = "Mrs." if female else "Sir"
    return f"{title} {full_name}"
STUDENT_LAST_NAMES = (
    "Mensah", "Boateng", "Owusu", "Asante", "Osei", "Appiah", "Agyeman",
    "Addo", "Lamptey", "Quaye", "Tetteh", "Ansah", "Amoah", "Darko",
    "Gyamfi", "Sarpong", "Ofori", "Nkansah", "Dogbe", "Agbeko",
)


LIVE_REQUEST_HEADER = "X-Greenfield-Live"


class LiveAwareSessionInterface(SecureCookieSessionInterface):
    """Background live-refresh requests never re-issue the session cookie.

    Sessions expire after 30 idle minutes because each request pushes the
    expiry forward. Automatic polling would keep an unattended screen signed in
    forever, so requests the page makes on its own (marked with the live
    header) leave the cookie, and therefore the expiry, untouched.
    """

    def should_set_cookie(self, app: Flask, session) -> bool:
        if request.headers.get(LIVE_REQUEST_HEADER) == "1":
            return False
        return super().should_set_cookie(app, session)


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(
        __name__,
        template_folder="Templates",
        static_folder="public/Static",
        static_url_path="/Static",
    )
    app.config.from_object(Config)
    app.session_interface = LiveAwareSessionInterface()
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
    app.register_blueprint(hub_bp)
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
    def template_context() -> dict[str, Any]:
        def nav_counts() -> dict[str, int]:
            """Unread badges for the top bar, loaded only when a page shows them."""
            if "nav_counts" not in g:
                signed_in = session.get("user_id") and session.get("mfa_verified")
                g.nav_counts = (
                    hub.nav_counts(session["user_id"], session.get("role", ""))
                    if signed_in and not app.testing
                    else {"messages": 0, "notices": 0}
                )
            return g.nav_counts

        return {
            "csp_nonce": g.csp_nonce,
            "nav_counts": nav_counts,
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

    def new_index_numbers(
        cursor,
        domain: str,
        count: int,
        first: int = 1_000_000_000,
        span: int = 9_000_000_000,
    ) -> list[str]:
        """Random numbers in [first, first + span) not yet used by any student
        or account email. The default range gives 10-digit numbers."""
        cursor.execute("SELECT student_number FROM students")
        taken = {row["student_number"] for row in cursor.fetchall()}
        cursor.execute(
            "SELECT email FROM users WHERE email LIKE %s",
            (f"%@{domain}",),
        )
        taken |= {row["email"].split("@", 1)[0] for row in cursor.fetchall()}
        free = span - sum(
            1 for value in taken
            if value.isdigit() and first <= int(value) < first + span
        )
        if free < count:
            raise click.ClickException(
                f"Only {free} unused index numbers are left in this range; "
                f"{count} are needed."
            )
        index_numbers: list[str] = []
        while len(index_numbers) < count:
            candidate = str(secrets.randbelow(span) + first)
            if candidate not in taken:
                taken.add(candidate)
                index_numbers.append(candidate)
        return index_numbers

    @app.cli.command("seed-hub-data")
    @click.option("--output-dir", default="seed_data", show_default=True,
                  type=click.Path(file_okay=False, writable=True),
                  help="Folder that receives the generated CSV files.")
    @click.option("--weeks", default=8, show_default=True, type=click.IntRange(1, 52),
                  help="How many weeks of attendance to generate.")
    @click.option("--seed", default=2026, show_default=True, type=int,
                  help="Random seed, so the same command gives the same files.")
    @click.option("--generate-only", is_flag=True, help="Write the CSVs without importing.")
    @click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
    def seed_hub_data(output_dir: str, weeks: int, seed: int, generate_only: bool,
                      yes: bool) -> None:
        """Generate fictional gender, attendance, fee, payment and expense CSVs
        from the existing students, then import them."""
        from database import seed_hub

        out_dir = Path(output_dir)
        click.echo(
            f"This writes gender.csv, attendance.csv ({weeks} weeks), fees.csv, "
            f"payments.csv and expenses.csv to {out_dir}"
            + ("." if generate_only else
               " and imports them into the configured database. Existing attendance, "
               "fee records and profile genders are kept; only missing rows are added.")
        )
        if not yes and not click.confirm("Continue?", default=False):
            click.echo("Cancelled.")
            return
        counts = seed_hub.generate_csvs(out_dir, weeks=weeks, seed=seed)
        for name, count in counts.items():
            click.echo(f"  {name}.csv: {count} rows written")
        if generate_only:
            return
        _echo_import_report(seed_hub.import_csvs(out_dir, _seed_actor_id()))

    @app.cli.command("seed-results")
    @click.option("--terms", default="1,2", show_default=True,
                  help="Comma-separated terms to generate exam scores for.")
    @click.option("--output-dir", default="seed_data/results", show_default=True,
                  type=click.Path(file_okay=False, writable=True),
                  help="Folder that receives results.csv.")
    @click.option("--seed", default=2026, show_default=True, type=int,
                  help="Random seed, so the same command gives the same file.")
    @click.option("--generate-only", is_flag=True, help="Write the CSV without importing.")
    @click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
    def seed_results(terms: str, output_dir: str, seed: int, generate_only: bool,
                     yes: bool) -> None:
        """Generate fictional exam scores for every student and subject in the
        given terms, credited to each subject's assigned teacher, then import them."""
        from database import seed_hub

        try:
            chosen = tuple(sorted({int(value) for value in terms.split(",") if value.strip()}))
        except ValueError as error:
            raise click.ClickException("Terms must be numbers such as 1,2.") from error
        if not chosen or any(term not in (1, 2, 3) for term in chosen):
            raise click.ClickException("Terms must be 1, 2 or 3.")
        out_dir = Path(output_dir)
        click.echo(
            f"This writes {out_dir / 'results.csv'} with exam scores for term(s) "
            f"{', '.join(map(str, chosen))}"
            + ("." if generate_only else
               " and imports it. Scores already in the database are kept, never overwritten.")
        )
        if not yes and not click.confirm("Continue?", default=False):
            click.echo("Cancelled.")
            return
        rows = seed_hub.generate_results_csv(out_dir, chosen, seed=seed)
        click.echo(f"  results.csv: {rows} rows written")
        if generate_only:
            return
        _echo_import_report(seed_hub.import_csvs(out_dir, _seed_actor_id()))

    @app.cli.command("assign-class-teachers")
    @click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
    def assign_class_teachers(yes: bool) -> None:
        """Give every class a teacher as its class teacher.

        Only classes whose class teacher is not an active teacher (for example
        the administrator placed there by seed-curriculum) are changed. Each is
        given a teacher who already teaches that class, preferring the English
        or Mathematics teacher, and no teacher becomes class teacher of two
        classes. Existing teacher class teachers are kept.
        """
        preferred = ("English", "English Language", "Mathematics", "Science",
                     "English Literature")
        with connect_database() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT c.id, c.name
                    FROM classes c
                    JOIN users u ON u.id = c.class_teacher_id
                    WHERE NOT (u.role = 'teacher' AND u.is_active)
                    ORDER BY c.id
                    """
                )
                classes = cursor.fetchall()
                if not classes:
                    click.echo("Every class already has a teacher as class teacher.")
                    return
                cursor.execute(
                    """
                    SELECT c.class_teacher_id AS id FROM classes c
                    JOIN users u ON u.id = c.class_teacher_id
                    WHERE u.role = 'teacher' AND u.is_active
                    """
                )
                taken = {row["id"] for row in cursor.fetchall()}
                plan = []
                for school_class in classes:
                    cursor.execute(
                        """
                        SELECT u.id, u.full_name, s.name AS subject,
                               (SELECT COUNT(DISTINCT ta2.class_id) FROM teacher_assignments ta2
                                WHERE ta2.teacher_id = u.id) AS class_count
                        FROM teacher_assignments ta
                        JOIN users u ON u.id = ta.teacher_id AND u.role = 'teacher' AND u.is_active
                        JOIN subjects s ON s.id = ta.subject_id
                        WHERE ta.class_id = %s
                        """,
                        (school_class["id"],),
                    )
                    candidates = [row for row in cursor.fetchall() if row["id"] not in taken]
                    if not candidates:
                        click.echo(f"  {school_class['name']}: no free teacher teaches this class; skipped")
                        continue
                    rank = {name: index for index, name in enumerate(preferred)}
                    choice = min(candidates, key=lambda row: (
                        rank.get(row["subject"], len(preferred)), row["class_count"], row["id"],
                    ))
                    taken.add(choice["id"])
                    plan.append((school_class, choice))

                for school_class, choice in plan:
                    click.echo(f"  {school_class['name']}: {choice['full_name']} ({choice['subject']})")
                if not plan:
                    return
                if not yes and not click.confirm(
                    f"Make these {len(plan)} teachers class teachers?", default=False
                ):
                    click.echo("Cancelled.")
                    connection.rollback()
                    return
                cursor.executemany(
                    "UPDATE classes SET class_teacher_id = %s WHERE id = %s",
                    [(choice["id"], school_class["id"]) for school_class, choice in plan],
                )
                record_audit_event(None, "class_teachers_assigned", None, connection=connection)
        click.echo(f"Assigned {len(plan)} class teachers.")

    @app.cli.command("import-hub-csv")
    @click.argument("csv_dir", type=click.Path(exists=True, file_okay=False))
    @click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
    def import_hub_csv(csv_dir: str, yes: bool) -> None:
        """Import gender/attendance/fees/payments/expenses CSVs from a folder.
        Only the files that exist are imported; see database/seed_hub.py for columns."""
        from database import seed_hub

        in_dir = Path(csv_dir)
        present = [name for name in seed_hub.FILES if (in_dir / name).exists()]
        if not present:
            raise click.ClickException(
                f"None of {', '.join(seed_hub.FILES)} were found in {in_dir}."
            )
        click.echo(f"Importing {', '.join(present)} from {in_dir} into the configured database.")
        if not yes and not click.confirm("Continue?", default=False):
            click.echo("Cancelled.")
            return
        try:
            report = seed_hub.import_csvs(in_dir, _seed_actor_id())
        except ValueError as error:
            raise click.ClickException(str(error)) from error
        _echo_import_report(report)

    def _seed_actor_id() -> int | None:
        """The first active administrator, recorded as who created seeded fee rows."""
        with connect_database() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM users WHERE role = 'admin' AND is_active ORDER BY id LIMIT 1"
                )
                row = cursor.fetchone()
        return row["id"] if row else None

    def _echo_import_report(report: dict[str, dict[str, int]]) -> None:
        for name, numbers in report.items():
            summary = ", ".join(f"{key.replace('_', ' ')} {value}" for key, value in numbers.items())
            click.echo(f"  {name}: {summary}")

    @app.cli.command("seed-teachers")
    @click.option("--per-subject", default=2, show_default=True, type=click.IntRange(1, 10))
    @click.option("--domain", default="gamail.edu.gh", show_default=True)
    @click.option(
        "--output",
        default="teacher_accounts.csv",
        show_default=True,
        type=click.Path(dir_okay=False, writable=True),
        help="CSV file that receives the one-time temporary passwords.",
    )
    @click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
    def seed_teachers(per_subject: int, domain: str, output: str, yes: bool) -> None:
        """Create fictional teacher accounts, a fixed number for every subject."""
        domain = domain.strip().lower()
        if not domain or "@" in domain or "." not in domain:
            raise click.ClickException("Enter a domain such as gamail.edu.gh.")
        if os.path.exists(output):
            raise click.ClickException(
                f"{output} already exists. Move it or choose another --output file "
                "so earlier temporary passwords are not overwritten."
            )
        click.echo(
            f"This command creates {per_subject} teacher accounts for every subject "
            f"in the configured database, with emails of the form "
            f"<index number>@{domain}. Each teacher is assigned their one subject "
            "in the classes where the curriculum teaches it. Class teachers are "
            f"not changed. Temporary passwords are written to {output}."
        )
        if not yes and not click.confirm("Continue?", default=False):
            click.echo("Teacher seeding cancelled.")
            return

        hasher = PasswordHasher()
        random_source = secrets.SystemRandom()
        with connect_database() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id, name FROM subjects ORDER BY id")
                subjects = cursor.fetchall()
                cursor.execute("SELECT id, name FROM classes ORDER BY id")
                classes_by_name = {row["name"]: row["id"] for row in cursor.fetchall()}
                if not subjects or not classes_by_name:
                    raise click.ClickException(
                        "No subjects or classes exist yet. Run seed-curriculum first."
                    )

                index_numbers = new_index_numbers(
                    cursor,
                    domain,
                    len(subjects) * per_subject,
                    first=TEACHER_INDEX_FIRST,
                    span=TEACHER_INDEX_SPAN,
                )
                names = [
                    f"{first} {last}"
                    for first in STUDENT_FIRST_NAMES
                    for last in STUDENT_LAST_NAMES
                ]
                random_source.shuffle(names)
                lengths = name_lengths(len(index_numbers))
                rows = []
                for subject in subjects:
                    # Classes that take this subject; unknown subjects fall back to all.
                    taught_in = [
                        name
                        for name in SUBJECT_CLASSES.get(subject["name"], classes_by_name)
                        if name in classes_by_name
                    ] or list(classes_by_name)
                    for slot in range(per_subject):
                        # Deal the classes out in turn, so colleagues share the year groups.
                        own_classes = taught_in[slot::per_subject] or taught_in
                        position = len(rows)
                        index_number = index_numbers[position]
                        full_name = teacher_display_name(
                            person_name(names[position % len(names)], lengths[position])
                        )
                        email = f"{index_number}@{domain}"
                        temporary_password = secrets.token_urlsafe(12)
                        cursor.execute(
                            """
                            INSERT INTO users
                                (full_name, email, password_hash, role, must_change_password)
                            VALUES (%s, %s, %s, 'teacher', TRUE)
                            RETURNING id
                            """,
                            (full_name, email, hasher.hash(temporary_password)),
                        )
                        user_id = cursor.fetchone()["id"]
                        cursor.executemany(
                            """
                            INSERT INTO teacher_assignments (teacher_id, class_id, subject_id)
                            VALUES (%s, %s, %s)
                            """,
                            [
                                (user_id, classes_by_name[name], subject["id"])
                                for name in own_classes
                            ],
                        )
                        rows.append((
                            index_number, full_name, email, subject["name"],
                            "; ".join(own_classes), temporary_password,
                        ))
                record_audit_event(
                    None,
                    "teacher_accounts_seeded",
                    None,
                    connection=connection,
                )
                # Written inside the transaction: if the file cannot be saved the
                # accounts roll back, so no account exists without a known password.
                with open(output, "x", newline="", encoding="utf-8") as handle:
                    writer = csv.writer(handle)
                    writer.writerow((
                        "index_number", "full_name", "email", "subject",
                        "classes", "temporary_password",
                    ))
                    writer.writerows(rows)

        click.echo(f"Created {len(rows)} teacher accounts. Credentials saved to {output}.")
        click.echo("Keep that file private and delete it once the passwords are handed out.")

    @app.cli.command("seed-students")
    @click.option("--count", default=100, show_default=True, type=click.IntRange(1, 1000))
    @click.option("--domain", default="gamail.edu.gh", show_default=True)
    @click.option(
        "--output",
        default="student_accounts.csv",
        show_default=True,
        type=click.Path(dir_okay=False, writable=True),
        help="CSV file that receives the one-time temporary passwords.",
    )
    @click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
    def seed_students(count: int, domain: str, output: str, yes: bool) -> None:
        """Create fictional student accounts with 10-digit index numbers (10100xxxxx)."""
        domain = domain.strip().lower()
        if not domain or "@" in domain or "." not in domain:
            raise click.ClickException("Enter a domain such as gamail.edu.gh.")
        if os.path.exists(output):
            raise click.ClickException(
                f"{output} already exists. Move it or choose another --output file "
                "so earlier temporary passwords are not overwritten."
            )
        click.echo(
            f"This command creates {count} student accounts in the configured "
            f"database with emails of the form <index number>@{domain}, spread "
            "evenly across the existing classes. Temporary passwords are written "
            f"to {output}; each student must change theirs at first sign-in."
        )
        if not yes and not click.confirm("Continue?", default=False):
            click.echo("Student seeding cancelled.")
            return

        hasher = PasswordHasher()
        # Unbiased shuffle, so names and classes are not tied to creation order.
        random_source = secrets.SystemRandom()
        with connect_database() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT c.id, c.name, COUNT(s.id) AS size
                    FROM classes c
                    LEFT JOIN students s ON s.class_id = c.id
                    GROUP BY c.id, c.name
                    ORDER BY c.id
                    """
                )
                classes = cursor.fetchall()
                if not classes:
                    raise click.ClickException(
                        "No classes exist yet. Run seed-curriculum first."
                    )
                index_numbers = new_index_numbers(
                    cursor,
                    domain,
                    count,
                    first=STUDENT_INDEX_FIRST,
                    span=STUDENT_INDEX_SPAN,
                )

                names = [
                    f"{first} {last}"
                    for first in STUDENT_FIRST_NAMES
                    for last in STUDENT_LAST_NAMES
                ]
                random_source.shuffle(names)
                lengths = name_lengths(count)
                rows = []
                for position, index_number in enumerate(index_numbers):
                    # Always fill the smallest class, so class sizes stay level
                    # however many students already exist.
                    school_class = min(classes, key=lambda item: (item["size"], item["id"]))
                    school_class["size"] += 1
                    full_name = person_name(names[position % len(names)], lengths[position])
                    email = f"{index_number}@{domain}"
                    temporary_password = secrets.token_urlsafe(12)
                    cursor.execute(
                        """
                        INSERT INTO users
                            (full_name, email, password_hash, role, must_change_password)
                        VALUES (%s, %s, %s, 'student', TRUE)
                        RETURNING id
                        """,
                        (full_name, email, hasher.hash(temporary_password)),
                    )
                    user_id = cursor.fetchone()["id"]
                    cursor.execute(
                        """
                        INSERT INTO students (user_id, student_number, class_id)
                        VALUES (%s, %s, %s)
                        """,
                        (user_id, index_number, school_class["id"]),
                    )
                    rows.append(
                        (index_number, full_name, email, school_class["name"], temporary_password)
                    )
                record_audit_event(
                    None,
                    "student_accounts_seeded",
                    None,
                    connection=connection,
                )
                # Written inside the transaction: if the file cannot be saved the
                # accounts roll back, so no account exists without a known password.
                with open(output, "x", newline="", encoding="utf-8") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(
                        ("index_number", "full_name", "email", "class", "temporary_password")
                    )
                    writer.writerows(rows)

        click.echo(f"Created {len(rows)} student accounts. Credentials saved to {output}.")
        click.echo("Keep that file private and delete it once the passwords are handed out.")

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=app.config["FLASK_DEBUG"])
