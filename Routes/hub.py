"""School hub pages shared by the three roles.

Identity always comes from the session. Anything the browser sends (ids,
filters, form fields) is validated here and access is re-checked in SQL.
"""
from datetime import date, datetime, time, timedelta
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
from psycopg import errors

from Routes.auth import role_required
from Routes.validation import parse_page
from database import hub


hub_bp = Blueprint("hub", __name__)

ALL_ROLES = ("admin", "teacher", "student")
STAFF = ("admin", "teacher")
MAX_MONEY = Decimal("99999999.99")


class FormError(ValueError):
    """A submitted field failed validation; the message is safe to show."""


def _search_arg(name: str = "q") -> str:
    value = request.args.get(name, "").strip()
    if len(value) > 100:
        abort(400)
    return value


def _int_arg(name: str) -> int | None:
    raw = request.args.get(name, "")
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        abort(400)
    if value < 1:
        abort(400)
    return value


def _choice_arg(name: str, choices: tuple[str, ...]) -> str:
    value = request.args.get(name, "")
    if value and value not in choices:
        abort(400)
    return value


def _text(name: str, label: str, max_length: int, required: bool = True) -> str:
    value = request.form.get(name, "").strip()
    if required and not value:
        raise FormError(f"{label} is required.")
    if len(value) > max_length:
        raise FormError(f"{label} must be at most {max_length} characters.")
    return value


def _choice(name: str, label: str, choices: tuple[str, ...]) -> str:
    value = request.form.get(name, "")
    if value not in choices:
        raise FormError(f"Choose a valid {label.lower()}.")
    return value


def _form_int(name: str, label: str) -> int:
    try:
        value = int(request.form.get(name, ""))
    except ValueError:
        raise FormError(f"Choose a valid {label.lower()}.") from None
    if value < 1:
        raise FormError(f"Choose a valid {label.lower()}.")
    return value


def _money(name: str, label: str, allow_zero: bool = True) -> Decimal:
    raw = request.form.get(name, "").strip() or "0"
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise FormError(f"{label} must be a number.") from None
    if (
        not value.is_finite()
        or value < 0
        or (value == 0 and not allow_zero)
        or value > MAX_MONEY
        or value != value.quantize(Decimal("0.01"))
    ):
        raise FormError(f"{label} must be a positive amount with at most two decimals.")
    return value


def _form_date(name: str, label: str, required: bool = True) -> date | None:
    raw = request.form.get(name, "").strip()
    if not raw:
        if required:
            raise FormError(f"{label} is required.")
        return None
    try:
        value = date.fromisoformat(raw)
    except ValueError:
        raise FormError(f"{label} must be a valid date.") from None
    if not date(2000, 1, 1) <= value <= date(2100, 12, 31):
        raise FormError(f"{label} is out of range.")
    return value


def _form_time(name: str, label: str) -> time | None:
    raw = request.form.get(name, "").strip()
    if not raw:
        return None
    try:
        return time.fromisoformat(raw)
    except ValueError:
        raise FormError(f"{label} must be a valid time.") from None


def _ip() -> str | None:
    return request.remote_addr


def _user() -> tuple[int, str]:
    return session["user_id"], session["role"]


# --------------------------------------------------------------------------
# People
# --------------------------------------------------------------------------

@hub_bp.get("/teachers")
@role_required("admin")
def teachers() -> str:
    search = _search_arg()
    page = parse_page(request.args.get("page"))
    result = hub.list_teachers(search, page)
    return render_template("hub/teachers.html", result=result, search=search)


@hub_bp.get("/students")
@role_required("admin", "teacher")
def students() -> str:
    user_id, role = _user()
    search = _search_arg()
    class_id = _int_arg("class_id")
    page = parse_page(request.args.get("page"))
    result = hub.list_students(user_id, role, search, class_id, page)
    if class_id and class_id not in {item["id"] for item in result["classes"]}:
        abort(403)
    return render_template("hub/students.html", result=result, search=search,
                           class_id=class_id)


# --------------------------------------------------------------------------
# Attendance grid
# --------------------------------------------------------------------------

@hub_bp.get("/attendance")
@role_required(*ALL_ROLES)
def attendance() -> str:
    user_id, role = _user()
    raw_start = request.args.get("start", "")
    try:
        start = date.fromisoformat(raw_start) if raw_start else None
    except ValueError:
        abort(400)
    if start is None:
        today = date.today()
        start = today - timedelta(days=today.weekday() + 7)
    grid = hub.attendance_grid(user_id, role, _int_arg("class_id"), start)
    if grid is None:
        abort(403 if role != "student" else 404)
    return render_template(
        "hub/attendance.html",
        grid=grid,
        start=start,
        previous_start=start - timedelta(days=14),
        next_start=start + timedelta(days=14),
    )


# --------------------------------------------------------------------------
# Finance
# --------------------------------------------------------------------------

@hub_bp.get("/finance/fees")
@role_required("admin", "student")
def fees() -> str:
    user_id, role = _user()
    if role == "student":
        return render_template("hub/my_fees.html", fees=hub.student_fees(user_id))
    search = _search_arg()
    status = _choice_arg("status", hub.FEE_STATUSES)
    term_raw = _choice_arg("term", ("1", "2", "3"))
    page = parse_page(request.args.get("page"))
    result = hub.list_fees(search, _int_arg("class_id"), status,
                           int(term_raw) if term_raw else None, page)
    summary = hub.fee_summary(date.today().year)
    return render_template(
        "hub/fees.html",
        result=result,
        summary=summary,
        filters={"q": search, "status": status, "term": term_raw,
                 "class_id": _int_arg("class_id")},
        with_charts=True,
    )


@hub_bp.post("/finance/fees")
@role_required("admin")
def create_fee():
    try:
        due = _form_date("due_date", "Due date")
        hub.create_fee(
            _form_int("student_id", "Student"),
            int(_choice("term", "Term", ("1", "2", "3"))),
            _money("tuition_fee", "Tuition fee"),
            _money("activities_fee", "Activities fee"),
            _money("misc_fee", "Miscellaneous fee"),
            due,
            session["user_id"],
            _ip(),
        )
    except FormError as error:
        flash(str(error), "error")
    except errors.UniqueViolation:
        flash("That student already has a fee record for this term.", "error")
    except errors.ForeignKeyViolation:
        flash("The selected student no longer exists.", "error")
    else:
        flash("Fee record created.", "success")
    return redirect(url_for("hub.fees"))


@hub_bp.post("/finance/fees/<int:fee_id>/payment")
@role_required("admin")
def record_payment(fee_id: int):
    try:
        amount = _money("amount", "Payment", allow_zero=False)
        paid_on = _form_date("paid_on", "Payment date")
        if paid_on > date.today():
            raise FormError("The payment date cannot be in the future.")
        if not hub.record_fee_payment(fee_id, amount, paid_on, session["user_id"], _ip()):
            raise FormError("The payment is more than the amount still owed.")
    except FormError as error:
        flash(str(error), "error")
    else:
        flash("Payment recorded.", "success")
    return redirect(url_for("hub.fees"))


@hub_bp.post("/finance/fees/<int:fee_id>/delete")
@role_required("admin")
def delete_fee(fee_id: int):
    if not hub.delete_fee(fee_id, session["user_id"], _ip()):
        abort(404)
    flash("Fee record deleted.", "success")
    return redirect(url_for("hub.fees"))


@hub_bp.get("/finance/expenses")
@role_required("admin")
def expenses() -> str:
    search = _search_arg()
    category = request.args.get("category", "").strip()
    if len(category) > 60:
        abort(400)
    raw_month = request.args.get("month", "")
    try:
        month = date.fromisoformat(f"{raw_month}-01") if raw_month else None
    except ValueError:
        abort(400)
    page = parse_page(request.args.get("page"))
    result = hub.list_expenses(search, category, month, page)
    return render_template(
        "hub/expenses.html",
        result=result,
        filters={"q": search, "category": category, "month": raw_month},
        today=date.today(),
    )


@hub_bp.post("/finance/expenses")
@role_required("admin")
def create_expense():
    try:
        paid_on = _form_date("paid_on", "Payment date")
        hub.create_expense(
            _text("category", "Category", 60),
            _text("description", "Expense", 150),
            _text("quantity", "Quantity", 40, required=False),
            _money("amount", "Amount", allow_zero=False),
            paid_on,
            session["user_id"],
            _ip(),
        )
    except FormError as error:
        flash(str(error), "error")
    else:
        flash("Expense recorded.", "success")
    return redirect(url_for("hub.expenses"))


@hub_bp.post("/finance/expenses/<int:expense_id>/delete")
@role_required("admin")
def delete_expense(expense_id: int):
    if not hub.delete_expense(expense_id, session["user_id"], _ip()):
        abort(404)
    flash("Expense deleted.", "success")
    return redirect(url_for("hub.expenses"))


# --------------------------------------------------------------------------
# Notices
# --------------------------------------------------------------------------

@hub_bp.get("/notices")
@role_required(*ALL_ROLES)
def notices() -> str:
    user_id, role = _user()
    search = _search_arg()
    page = parse_page(request.args.get("page"))
    result = hub.list_notices(role, search, page)
    selected_id = _int_arg("notice")
    if selected_id is None and result["rows"]:
        selected_id = result["rows"][0]["id"]
    selected = hub.get_notice(selected_id, user_id, role) if selected_id else None
    if _int_arg("notice") and selected is None:
        abort(404)
    return render_template("hub/notices.html", result=result, search=search,
                           selected=selected)


@hub_bp.post("/notices")
@role_required(*STAFF)
def create_notice():
    try:
        notice_id = hub.create_notice(
            _text("title", "Title", 150),
            _text("body", "Notice", 5000),
            _choice("audience", "Audience", hub.AUDIENCES),
            _text("tags", "Tags", 120, required=False),
            session["user_id"],
            _ip(),
        )
    except FormError as error:
        flash(str(error), "error")
        return redirect(url_for("hub.notices"))
    flash("Notice published.", "success")
    return redirect(url_for("hub.notices", notice=notice_id))


@hub_bp.post("/notices/<int:notice_id>/delete")
@role_required(*STAFF)
def delete_notice(notice_id: int):
    user_id, role = _user()
    if not hub.delete_notice(notice_id, user_id, role, _ip()):
        abort(404)
    flash("Notice deleted.", "success")
    return redirect(url_for("hub.notices"))


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------

@hub_bp.get("/calendar")
@role_required(*ALL_ROLES)
def calendar_view() -> str:
    _user_id, role = _user()
    raw_day = request.args.get("day", "")
    try:
        selected = date.fromisoformat(raw_day) if raw_day else date.today()
    except ValueError:
        abort(400)
    view = _choice_arg("view", ("month", "week", "day")) or "month"
    first = selected.replace(day=1)
    grid_start = first - timedelta(days=(first.weekday() + 1) % 7)
    events = hub.events_between(role, grid_start, grid_start + timedelta(days=41))
    grid = hub.month_grid(selected.year, selected.month, events, selected)
    week_start = selected - timedelta(days=(selected.weekday() + 1) % 7)
    week = [
        {"date": week_start + timedelta(days=offset),
         "events": [e for e in events if e["event_date"] == week_start + timedelta(days=offset)]}
        for offset in range(7)
    ]
    if any(day["date"] < grid_start or day["date"] > grid_start + timedelta(days=41)
           for day in week):
        week_events = hub.events_between(role, week_start, week_start + timedelta(days=6))
        for day in week:
            day["events"] = [e for e in week_events if e["event_date"] == day["date"]]
    day_events = [e for e in events if e["event_date"] == selected]
    return render_template(
        "hub/calendar.html",
        grid=grid,
        week=week,
        view=view,
        selected=selected,
        day_events=day_events,
        agenda_events=hub.events_between(role, date.today(), date.today() + timedelta(days=30))[:6],
        categories=hub.EVENT_CATEGORIES,
        audiences=hub.AUDIENCES,
        timedelta=timedelta,
    )


@hub_bp.post("/calendar/events")
@role_required(*STAFF)
def create_event():
    try:
        event_date = _form_date("event_date", "Date")
        hub.create_event(
            _text("title", "Title", 150),
            _text("description", "Description", 500, required=False),
            event_date,
            _form_time("start_time", "Start time"),
            _form_time("end_time", "End time"),
            _choice("category", "Category", hub.EVENT_CATEGORIES),
            _choice("audience", "Audience", hub.AUDIENCES),
            session["user_id"],
            _ip(),
        )
    except (FormError, ValueError) as error:
        flash(str(error), "error")
        return redirect(url_for("hub.calendar_view"))
    flash("Event added to the calendar.", "success")
    return redirect(url_for("hub.calendar_view", day=event_date.isoformat()))


@hub_bp.post("/calendar/events/<int:event_id>/delete")
@role_required(*STAFF)
def delete_event(event_id: int):
    user_id, role = _user()
    if not hub.delete_event(event_id, user_id, role, _ip()):
        abort(404)
    flash("Event deleted.", "success")
    return redirect(url_for("hub.calendar_view"))


# --------------------------------------------------------------------------
# Library
# --------------------------------------------------------------------------

@hub_bp.get("/library")
@role_required(*ALL_ROLES)
def library() -> str:
    search = _search_arg()
    status = _choice_arg("status", hub.BOOK_STATUSES)
    page = parse_page(request.args.get("page"))
    result = hub.list_books(search, status, page)
    return render_template("hub/library.html", result=result, search=search,
                           status=status)


@hub_bp.post("/library/books")
@role_required("admin")
def create_book():
    try:
        raw_year = request.form.get("publish_year", "").strip()
        try:
            year = int(raw_year) if raw_year else None
        except ValueError:
            raise FormError("Publish year must be a number.") from None
        if year is not None and not 1400 <= year <= 2100:
            raise FormError("Publish year is out of range.")
        hub.create_book(
            {
                "code": _text("code", "Book ID", 30),
                "title": _text("title", "Book name", 200),
                "author": _text("author", "Writer", 120),
                "subject": _text("subject", "Subject", 100),
                "classes": _text("classes", "Classes", 60, required=False),
                "publish_year": year,
            },
            session["user_id"],
            _ip(),
        )
    except FormError as error:
        flash(str(error), "error")
    except errors.UniqueViolation:
        flash("A book with that ID already exists.", "error")
    else:
        flash("Book added to the library.", "success")
    return redirect(url_for("hub.library"))


@hub_bp.post("/library/books/<int:book_id>/status")
@role_required("admin")
def set_book_status(book_id: int):
    try:
        status = _choice("status", "Status", hub.BOOK_STATUSES)
    except FormError as error:
        flash(str(error), "error")
        return redirect(url_for("hub.library"))
    if not hub.set_book_status(book_id, status, session["user_id"], _ip()):
        abort(404)
    flash("Book status updated.", "success")
    return redirect(url_for("hub.library"))


@hub_bp.post("/library/books/<int:book_id>/delete")
@role_required("admin")
def delete_book(book_id: int):
    if not hub.delete_book(book_id, session["user_id"], _ip()):
        abort(404)
    flash("Book removed.", "success")
    return redirect(url_for("hub.library"))


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------

@hub_bp.get("/messages")
@hub_bp.get("/messages/<int:conversation_id>")
@role_required(*ALL_ROLES)
def messages(conversation_id: int | None = None) -> str:
    user_id, role = _user()
    conversations = hub.list_conversations(user_id)
    search = _search_arg()
    if search:
        lowered = search.lower()
        conversations = [c for c in conversations if lowered in (c["title"] or "").lower()]
    if conversation_id is None and conversations and not search:
        conversation_id = conversations[0]["id"]
    current = hub.get_conversation(conversation_id, user_id) if conversation_id else None
    if conversation_id and current is None:
        abort(404)
    # Opening a conversation marks it read; reflect that in the list.
    for item in conversations:
        if current and item["id"] == current["id"]:
            item["unread"] = 0
    return render_template(
        "hub/messages.html",
        conversations=conversations,
        current=current,
        recipients=hub.message_recipients(user_id, role),
        search=search,
    )


@hub_bp.post("/messages/<int:conversation_id>")
@role_required(*ALL_ROLES)
def send_message(conversation_id: int):
    try:
        body = _text("body", "Message", 2000)
    except FormError as error:
        flash(str(error), "error")
        return redirect(url_for("hub.messages", conversation_id=conversation_id))
    if not hub.send_message(conversation_id, session["user_id"], body):
        abort(404)
    return redirect(url_for("hub.messages", conversation_id=conversation_id))


@hub_bp.post("/messages/new")
@role_required(*ALL_ROLES)
def new_conversation():
    user_id, role = _user()
    try:
        other_id = _form_int("recipient_id", "Recipient")
        body = _text("body", "Message", 2000, required=False)
    except FormError as error:
        flash(str(error), "error")
        return redirect(url_for("hub.messages"))
    conversation_id = hub.start_direct_conversation(user_id, role, other_id)
    if conversation_id is None:
        flash("You cannot message that person.", "error")
        return redirect(url_for("hub.messages"))
    if body:
        hub.send_message(conversation_id, user_id, body)
    return redirect(url_for("hub.messages", conversation_id=conversation_id))


@hub_bp.post("/messages/groups")
@role_required(*STAFF)
def create_group():
    user_id, role = _user()
    try:
        title = _text("title", "Group name", 100)
        description = _text("description", "Description", 300, required=False)
        try:
            member_ids = [int(value) for value in request.form.getlist("member_ids")]
        except ValueError:
            raise FormError("Choose valid members.") from None
        conversation_id = hub.create_group(user_id, role, title, description,
                                           member_ids, _ip())
    except (FormError, ValueError) as error:
        flash(str(error), "error")
        return redirect(url_for("hub.messages"))
    flash("Group created.", "success")
    return redirect(url_for("hub.messages", conversation_id=conversation_id))


# --------------------------------------------------------------------------
# Assignments
# --------------------------------------------------------------------------

@hub_bp.get("/assignments")
@role_required("teacher", "student")
def assignments() -> str:
    user_id, role = _user()
    search = _search_arg()
    page = parse_page(request.args.get("page"))
    if role == "student":
        result = hub.student_assignments(user_id, search, page, per_page=10)
        return render_template("hub/assignments_student.html", result=result,
                               search=search, now=datetime.now().astimezone())
    return render_template(
        "hub/assignments_teacher.html",
        options=hub.teacher_assignment_options(user_id),
        assignments=hub.list_teacher_assignments(user_id),
        submissions=hub.teacher_submissions(user_id, search, page),
        search=search,
    )


@hub_bp.post("/assignments")
@role_required("teacher")
def create_assignment():
    try:
        target = request.form.get("target", "")
        try:
            class_id, subject_id = (int(part) for part in target.split(":", 1))
        except ValueError:
            raise FormError("Choose a class and subject.") from None
        due_date = _form_date("due_date", "Due date")
        due_time = _form_time("due_time", "Due time") or time(23, 59)
        if due_date < date.today():
            raise FormError("The due date cannot be in the past.")
        created = hub.create_assignment(
            session["user_id"],
            class_id,
            subject_id,
            _text("title", "Title", 150),
            _text("description", "Description", 1000, required=False),
            datetime.combine(due_date, due_time).astimezone(),
            _ip(),
        )
        if not created:
            abort(403)
    except FormError as error:
        flash(str(error), "error")
    else:
        flash("Assignment created.", "success")
    return redirect(url_for("hub.assignments"))


@hub_bp.post("/assignments/<int:assignment_id>/delete")
@role_required("teacher")
def delete_assignment(assignment_id: int):
    if not hub.delete_assignment(assignment_id, session["user_id"], _ip()):
        abort(404)
    flash("Assignment deleted.", "success")
    return redirect(url_for("hub.assignments"))


@hub_bp.post("/assignments/<int:assignment_id>/review/<int:student_id>")
@role_required("teacher")
def review_submission(assignment_id: int, student_id: int):
    if not hub.review_submission(session["user_id"], assignment_id, student_id, _ip()):
        abort(404)
    flash("Marked as reviewed.", "success")
    if request.form.get("return_to") == "dashboard":
        return redirect(url_for("teacher.index"))
    return redirect(url_for("hub.assignments"))


@hub_bp.post("/assignments/<int:assignment_id>/status")
@role_required("student")
def set_assignment_status(assignment_id: int):
    try:
        status = _choice("status", "Status", hub.STUDENT_TASK_STATUSES)
    except FormError as error:
        flash(str(error), "error")
        return redirect(url_for("hub.assignments"))
    if not hub.set_student_assignment_status(session["user_id"], assignment_id, status):
        flash("That assignment cannot be changed.", "error")
    else:
        flash("Assignment status updated.", "success")
    if request.form.get("return_to") == "dashboard":
        return redirect(url_for("main.student_dashboard"))
    return redirect(url_for("hub.assignments"))


# --------------------------------------------------------------------------
# Personal tasks (to-do list on the dashboard)
# --------------------------------------------------------------------------

@hub_bp.post("/tasks")
@role_required(*ALL_ROLES)
def add_task():
    try:
        hub.add_task(
            session["user_id"],
            _text("title", "Task", 150),
            _form_date("due_date", "Due date", required=False),
        )
    except FormError as error:
        flash(str(error), "error")
    return redirect(url_for("main.dashboard"))


@hub_bp.post("/tasks/<int:task_id>/toggle")
@role_required(*ALL_ROLES)
def toggle_task(task_id: int):
    if not hub.toggle_task(session["user_id"], task_id):
        abort(404)
    return redirect(url_for("main.dashboard"))


@hub_bp.post("/tasks/<int:task_id>/delete")
@role_required(*ALL_ROLES)
def delete_task(task_id: int):
    if not hub.delete_task(session["user_id"], task_id):
        abort(404)
    return redirect(url_for("main.dashboard"))


# --------------------------------------------------------------------------
# Profile and settings
# --------------------------------------------------------------------------

@hub_bp.route("/profile", methods=["GET", "POST"])
@role_required(*ALL_ROLES)
def profile() -> Any:
    user_id = session["user_id"]
    if request.method == "POST":
        try:
            gender = request.form.get("gender", "")
            if gender not in ("", "female", "male"):
                raise FormError("Choose a valid gender.")
            phone = _text("phone", "Phone number", 30, required=False)
            if phone and not all(ch.isdigit() or ch in " +-()" for ch in phone):
                raise FormError("Phone number may contain digits, spaces, +, - and brackets.")
            birthday = _form_date("date_of_birth", "Date of birth", required=False)
            if birthday and birthday > date.today():
                raise FormError("Date of birth cannot be in the future.")
            hub.update_profile(
                user_id,
                {
                    "phone": phone or None,
                    "address": _text("address", "Address", 200, required=False) or None,
                    "date_of_birth": birthday,
                    "gender": gender or None,
                    "bio": _text("bio", "About", 500, required=False) or None,
                },
                _ip(),
            )
        except FormError as error:
            flash(str(error), "error")
        else:
            flash("Profile updated.", "success")
            return redirect(url_for("hub.profile"))
    details = hub.get_profile(user_id)
    if details is None:
        abort(404)
    return render_template("hub/profile.html", profile=details)


@hub_bp.get("/settings")
@role_required(*ALL_ROLES)
def settings() -> str:
    return render_template("hub/settings.html", profile=hub.get_profile(session["user_id"]))
