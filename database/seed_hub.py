"""Fictional demo data for the school hub as CSV files, and a CSV importer.

Five files share one layout between the generator and the importer:

- gender.csv      email, gender
- attendance.csv  student_number, attendance_date, status, marked_by_email
- fees.csv        student_number, term, tuition_fee, activities_fee, misc_fee, due_date
- payments.csv    student_number, term, amount, paid_on
- expenses.csv    category, description, quantity, amount, paid_on

The importer is idempotent: existing attendance and fee records are kept,
payments are only added to fee records created in the same import, gender is
only filled where it is blank, and duplicate expenses are skipped. Generated
numbers come from a seeded `random.Random`, so this is demo data, not a
source of secrets.
"""
import csv
import random
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from database.db import connect_database, record_audit_event


FILES = ("gender.csv", "attendance.csv", "fees.csv", "payments.csv", "expenses.csv")
STATUSES = ("present", "late", "absent")
FEMALE_TITLES = ("mrs", "mrs.", "ms", "ms.", "miss", "madam")
MALE_TITLES = ("mr", "mr.", "sir")

# Termly fees by stage, in the school's currency.
FEE_LEVELS = (
    (("Nursery", "Reception"), Decimal("1500"), Decimal("200"), Decimal("100")),
    (tuple(f"Year {n}" for n in range(1, 7)), Decimal("2200"), Decimal("250"), Decimal("120")),
    (tuple(f"Year {n}" for n in range(7, 10)), Decimal("3000"), Decimal("300"), Decimal("150")),
    (("Year 10", "Year 11"), Decimal("3600"), Decimal("300"), Decimal("180")),
    (("Year 12", "Year 13"), Decimal("4200"), Decimal("350"), Decimal("200")),
)
DEFAULT_LEVEL = (Decimal("3000"), Decimal("300"), Decimal("150"))
# Term due dates within the calendar year the data is generated for.
TERM_DUE = {1: (2, 1), 2: (6, 1), 3: (10, 1)}

EXPENSE_CATALOGUE = (
    ("Laboratory", "Chemicals", "100 units", 500),
    ("Laboratory", "Glassware replacement", "40 pieces", 650),
    ("Maintenance", "HVAC repair", "1 service", 2000),
    ("Maintenance", "Painting school building", "1 service", 7000),
    ("Maintenance", "Plumbing repairs", "1 service", 900),
    ("Boarding Equipment", "Bedding sets", "50 sets", 2500),
    ("Library", "Books acquisition", "200 books", 3000),
    ("Sports", "Basketball gear", "30 items", 1500),
    ("Sports", "Football kits", "25 kits", 1800),
    ("IT Infrastructure", "Computers upgrade", "10 pcs", 10000),
    ("IT Infrastructure", "Internet service", "1 month", 600),
    ("Transportation", "Bus maintenance", "3 buses", 4500),
    ("Transportation", "Fuel", "1 month", 2200),
    ("Cafeteria", "Kitchen equipment upgrade", "5 items", 5000),
    ("Cafeteria", "Food supplies", "1 month", 3800),
    ("Arts & Crafts", "Supplies purchase", "100 kits", 1000),
    ("Utilities", "Electricity", "1 month", 2600),
    ("Utilities", "Water", "1 month", 700),
    ("Stationery", "Exercise books and pens", "500 items", 1200),
)


def infer_gender(full_name: str) -> str | None:
    """Female or male from a title or a known first name; None when unsure."""
    from app import FEMALE_ENGLISH_NAMES, FEMALE_FIRST_NAMES, MALE_ENGLISH_NAMES, MALE_FIRST_NAMES

    parts = full_name.split()
    if not parts:
        return None
    title = parts[0].lower()
    if title in FEMALE_TITLES:
        return "female"
    if title in MALE_TITLES:
        return "male"
    female = set(FEMALE_FIRST_NAMES) | set(FEMALE_ENGLISH_NAMES)
    male = set(MALE_FIRST_NAMES) | set(MALE_ENGLISH_NAMES)
    for part in parts:
        if part in female:
            return "female"
        if part in male:
            return "male"
    return None


def fee_level(class_name: str) -> tuple[Decimal, Decimal, Decimal]:
    for names, tuition, activities, misc in FEE_LEVELS:
        if class_name in names:
            return tuition, activities, misc
    return DEFAULT_LEVEL


def school_days(end: date, weeks: int) -> list[date]:
    start = end - timedelta(days=7 * weeks)
    return [
        start + timedelta(days=offset)
        for offset in range((end - start).days + 1)
        if (start + timedelta(days=offset)).weekday() < 5
    ]


def _load_people() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, full_name, email, role FROM users WHERE is_active ORDER BY id"
            )
            users = cursor.fetchall()
            cursor.execute(
                """
                SELECT st.student_number, c.name AS class_name, u.email AS teacher_email
                FROM students st
                JOIN users u_s ON u_s.id = st.user_id AND u_s.is_active
                JOIN classes c ON c.id = st.class_id
                JOIN users u ON u.id = c.class_teacher_id
                ORDER BY c.id, st.id
                """
            )
            students = cursor.fetchall()
    return users, students


def generate_csvs(
    out_dir: Path,
    *,
    weeks: int = 8,
    seed: int = 2026,
    today: date | None = None,
) -> dict[str, int]:
    """Write the five CSV files from the people already in the database."""
    rng = random.Random(seed)
    today = today or date.today()
    users, students = _load_people()
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    with (out_dir / "gender.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("email", "gender"))
        rows = 0
        for user in users:
            gender = infer_gender(user["full_name"]) or rng.choice(("female", "male"))
            writer.writerow((user["email"], gender))
            rows += 1
        counts["gender"] = rows

    days = school_days(today, weeks)
    with (out_dir / "attendance.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("student_number", "attendance_date", "status", "marked_by_email"))
        rows = 0
        for student in students:
            # Most students attend well; a few are frequently absent.
            present_rate = rng.choice((0.97, 0.95, 0.93, 0.9, 0.82))
            for day in days:
                roll = rng.random()
                if roll < present_rate:
                    status = "present"
                elif roll < present_rate + (1 - present_rate) * 0.4:
                    status = "late"
                else:
                    status = "absent"
                writer.writerow((student["student_number"], day.isoformat(), status,
                                 student["teacher_email"]))
                rows += 1
        counts["attendance"] = rows

    payments: list[tuple[str, int, Decimal, date]] = []
    with (out_dir / "fees.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("student_number", "term", "tuition_fee", "activities_fee",
                         "misc_fee", "due_date"))
        rows = 0
        for student in students:
            tuition, activities, misc = fee_level(student["class_name"])
            total = tuition + activities + misc
            for term, (month, day) in TERM_DUE.items():
                due = date(today.year, month, day)
                writer.writerow((student["student_number"], term, tuition, activities, misc,
                                 due.isoformat()))
                rows += 1
                # Payments arrive in the eight weeks before the due date; some
                # families pay in two parts and some have not paid yet.
                outcome = rng.random()
                if due > today + timedelta(days=56):
                    continue
                if outcome < 0.6:
                    plan = [total]
                elif outcome < 0.85:
                    first = (total * Decimal(rng.choice((40, 50, 60))) / 100).quantize(Decimal("1"))
                    plan = [first, total - first] if rng.random() < 0.55 else [first]
                else:
                    plan = []
                for index, amount in enumerate(plan):
                    offset = rng.randint(3, 56) if index == 0 else rng.randint(-20, 2)
                    paid_on = min(due - timedelta(days=offset), today)
                    payments.append((student["student_number"], term, amount, paid_on))
        counts["fees"] = rows

    with (out_dir / "payments.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("student_number", "term", "amount", "paid_on"))
        for student_number, term, amount, paid_on in sorted(payments, key=lambda p: p[3]):
            writer.writerow((student_number, term, amount, paid_on.isoformat()))
        counts["payments"] = len(payments)

    with (out_dir / "expenses.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("category", "description", "quantity", "amount", "paid_on"))
        rows = 0
        for month in range(1, today.month + 1):
            last_day = (date(today.year, month, 28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            for category, description, quantity, base in EXPENSE_CATALOGUE:
                monthly = quantity == "1 month"
                if not monthly and rng.random() > 0.3:
                    continue
                amount = Decimal(base) * Decimal(rng.randint(85, 115)) / 100
                paid_on = date(today.year, month, rng.randint(1, last_day.day))
                if paid_on > today:
                    continue
                writer.writerow((category, description, quantity,
                                 amount.quantize(Decimal("0.01")), paid_on.isoformat()))
                rows += 1
        counts["expenses"] = rows
    return counts


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------

class RowError(ValueError):
    """A CSV row that cannot be imported; carries a short reason."""


def _money(value: str, label: str) -> Decimal:
    try:
        amount = Decimal(value.strip())
    except InvalidOperation:
        raise RowError(f"{label} is not a number") from None
    if not amount.is_finite() or amount < 0 or amount > Decimal("99999999.99"):
        raise RowError(f"{label} is out of range")
    return amount.quantize(Decimal("0.01"))


def _date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise RowError(f"{label} must be YYYY-MM-DD") from None


def _read(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [name for name in columns if name not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path.name} is missing columns: {', '.join(missing)}")
        return [{key: (value or "").strip() for key, value in row.items()} for row in reader]


def import_csvs(
    in_dir: Path,
    actor_id: int | None,
    *,
    today: date | None = None,
) -> dict[str, dict[str, int]]:
    """Import whichever of the five CSV files exist in `in_dir`, in one transaction."""
    today = today or date.today()
    report: dict[str, dict[str, int]] = {}
    with connect_database() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, email FROM users")
            user_by_email = {row["email"].lower(): row["id"] for row in cursor.fetchall()}
            cursor.execute("SELECT id, student_number FROM students")
            student_by_number = {row["student_number"]: row["id"] for row in cursor.fetchall()}

            if (in_dir / "gender.csv").exists():
                report["gender"] = _import_gender(cursor, in_dir / "gender.csv", user_by_email)
            if (in_dir / "attendance.csv").exists():
                report["attendance"] = _import_attendance(
                    cursor, in_dir / "attendance.csv", student_by_number, user_by_email, today
                )
            new_fee_ids: dict[tuple[int, int], int] = {}
            if (in_dir / "fees.csv").exists():
                report["fees"], new_fee_ids = _import_fees(
                    cursor, in_dir / "fees.csv", student_by_number, actor_id
                )
            if (in_dir / "payments.csv").exists():
                report["payments"] = _import_payments(
                    cursor, in_dir / "payments.csv", student_by_number, new_fee_ids,
                    actor_id, today
                )
            if (in_dir / "expenses.csv").exists():
                report["expenses"] = _import_expenses(
                    cursor, in_dir / "expenses.csv", actor_id, today
                )
            if report:
                record_audit_event(actor_id, "hub_data_imported", None, connection=connection)
    return report


def _import_gender(cursor, path: Path, user_by_email: dict[str, int]) -> dict[str, int]:
    rows = _read(path, ("email", "gender"))
    valid: list[tuple[int, str]] = []
    skipped = 0
    for row in rows:
        user_id = user_by_email.get(row["email"].lower())
        if user_id is None or row["gender"] not in ("female", "male"):
            skipped += 1
            continue
        valid.append((user_id, row["gender"]))
    cursor.executemany(
        """
        INSERT INTO user_profiles (user_id, gender) VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE SET gender = EXCLUDED.gender
        WHERE user_profiles.gender IS NULL
        """,
        valid,
    )
    return {"read": len(rows), "applied": len(valid), "skipped": skipped}


def _import_attendance(cursor, path: Path, student_by_number, user_by_email, today) -> dict[str, int]:
    rows = _read(path, ("student_number", "attendance_date", "status", "marked_by_email"))
    valid: list[tuple[int, date, str, int]] = []
    skipped = 0
    for row in rows:
        student_id = student_by_number.get(row["student_number"])
        marker_id = user_by_email.get(row["marked_by_email"].lower())
        try:
            day = _date(row["attendance_date"], "attendance_date")
        except RowError:
            skipped += 1
            continue
        if student_id is None or marker_id is None or row["status"] not in STATUSES or day > today:
            skipped += 1
            continue
        valid.append((student_id, day, row["status"], marker_id))
    # Bulk load through a temporary table: one round trip for many rows.
    cursor.execute(
        """
        CREATE TEMP TABLE attendance_import (
            student_id BIGINT, attendance_date DATE, status VARCHAR(10), marked_by BIGINT
        ) ON COMMIT DROP
        """
    )
    with cursor.copy(
        "COPY attendance_import (student_id, attendance_date, status, marked_by) FROM STDIN"
    ) as copy:
        for record in valid:
            copy.write_row(record)
    cursor.execute(
        """
        INSERT INTO attendance (student_id, attendance_date, status, marked_by)
        SELECT DISTINCT ON (student_id, attendance_date)
               student_id, attendance_date, status, marked_by
        FROM attendance_import
        ON CONFLICT (student_id, attendance_date) DO NOTHING
        """
    )
    return {"read": len(rows), "inserted": cursor.rowcount, "skipped": skipped,
            "already_recorded": len(valid) - cursor.rowcount}


def _import_fees(cursor, path: Path, student_by_number, actor_id):
    rows = _read(path, ("student_number", "term", "tuition_fee", "activities_fee",
                        "misc_fee", "due_date"))
    created: dict[tuple[int, int], int] = {}
    skipped = existing = 0
    for row in rows:
        student_id = student_by_number.get(row["student_number"])
        try:
            term = int(row["term"])
            values = (
                student_id, term,
                _money(row["tuition_fee"], "tuition_fee"),
                _money(row["activities_fee"], "activities_fee"),
                _money(row["misc_fee"], "misc_fee"),
                _date(row["due_date"], "due_date"),
            )
        except (ValueError, RowError):
            skipped += 1
            continue
        if student_id is None or term not in (1, 2, 3):
            skipped += 1
            continue
        cursor.execute(
            """
            INSERT INTO fee_records
                (student_id, term, tuition_fee, activities_fee, misc_fee, due_date, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (student_id, term) DO NOTHING
            RETURNING id
            """,
            (*values, actor_id),
        )
        inserted = cursor.fetchone()
        if inserted is None:
            existing += 1
        else:
            created[(student_id, term)] = inserted["id"]
    return {"read": len(rows), "inserted": len(created), "skipped": skipped,
            "already_recorded": existing}, created


def _import_payments(cursor, path: Path, student_by_number, new_fee_ids, actor_id, today):
    rows = _read(path, ("student_number", "term", "amount", "paid_on"))
    cursor.execute(
        "SELECT id, tuition_fee + activities_fee + misc_fee - amount_paid AS owed "
        "FROM fee_records WHERE id = ANY(%s)",
        (list(new_fee_ids.values()),),
    )
    owed = {row["id"]: row["owed"] for row in cursor.fetchall()}
    valid: list[tuple[int, Decimal, date, int | None]] = []
    totals: dict[int, Decimal] = defaultdict(Decimal)
    skipped = not_new = 0
    for row in rows:
        student_id = student_by_number.get(row["student_number"])
        try:
            term = int(row["term"])
            amount = _money(row["amount"], "amount")
            paid_on = _date(row["paid_on"], "paid_on")
        except (ValueError, RowError):
            skipped += 1
            continue
        fee_id = new_fee_ids.get((student_id, term))
        if fee_id is None:
            not_new += 1
            continue
        if amount <= 0 or paid_on > today or totals[fee_id] + amount > owed[fee_id]:
            skipped += 1
            continue
        totals[fee_id] += amount
        valid.append((fee_id, amount, paid_on, actor_id))
    cursor.executemany(
        "INSERT INTO fee_payments (fee_id, amount, paid_on, recorded_by) VALUES (%s, %s, %s, %s)",
        valid,
    )
    cursor.executemany(
        "UPDATE fee_records SET amount_paid = amount_paid + %s WHERE id = %s",
        [(total, fee_id) for fee_id, total in totals.items()],
    )
    return {"read": len(rows), "inserted": len(valid), "skipped": skipped,
            "fee_not_in_this_import": not_new}


def _import_expenses(cursor, path: Path, actor_id, today) -> dict[str, int]:
    rows = _read(path, ("category", "description", "quantity", "amount", "paid_on"))
    valid = []
    skipped = 0
    for row in rows:
        try:
            amount = _money(row["amount"], "amount")
            paid_on = _date(row["paid_on"], "paid_on")
        except RowError:
            skipped += 1
            continue
        if (not row["category"] or not row["description"] or amount <= 0 or paid_on > today
                or len(row["category"]) > 60 or len(row["description"]) > 150
                or len(row["quantity"]) > 40):
            skipped += 1
            continue
        valid.append((row["category"], row["description"], row["quantity"], amount, paid_on,
                      actor_id))
    inserted = 0
    for record in valid:
        cursor.execute(
            """
            INSERT INTO expenses (category, description, quantity, amount, paid_on, recorded_by)
            SELECT %s, %s, %s, %s, %s, %s
            WHERE NOT EXISTS (
                SELECT 1 FROM expenses
                WHERE category = %s AND description = %s AND amount = %s AND paid_on = %s
            )
            """,
            (*record, record[0], record[1], record[3], record[4]),
        )
        inserted += cursor.rowcount
    return {"read": len(rows), "inserted": inserted, "skipped": skipped,
            "duplicates": len(valid) - inserted}
