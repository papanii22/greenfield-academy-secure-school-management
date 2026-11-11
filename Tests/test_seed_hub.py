import csv
from datetime import date
from decimal import Decimal

import pytest

from database import seed_hub


def test_gender_is_inferred_from_title_or_known_name():
    assert seed_hub.infer_gender("Mrs. Ama Mensah") == "female"
    assert seed_hub.infer_gender("Sir Kofi Boateng") == "male"
    assert seed_hub.infer_gender("Mr Yaw Joans") == "male"
    assert seed_hub.infer_gender("Comfort Efua Addo") == "female"
    assert seed_hub.infer_gender("Emmanuel Kwame Owusu") == "male"
    assert seed_hub.infer_gender("Zed Zedson") is None
    assert seed_hub.infer_gender("") is None


def test_fee_level_follows_the_stage():
    assert seed_hub.fee_level("Nursery")[0] == Decimal("1500")
    assert seed_hub.fee_level("Year 3")[0] == Decimal("2200")
    assert seed_hub.fee_level("Year 9")[0] == Decimal("3000")
    assert seed_hub.fee_level("Year 11")[0] == Decimal("3600")
    assert seed_hub.fee_level("Year 13")[0] == Decimal("4200")
    assert seed_hub.fee_level("Unknown") == seed_hub.DEFAULT_LEVEL


def test_school_days_are_weekdays_only():
    days = seed_hub.school_days(date(2026, 11, 11), weeks=2)

    assert days[0] == date(2026, 10, 28)
    assert days[-1] == date(2026, 11, 11)
    assert all(day.weekday() < 5 for day in days)
    assert len(days) == 11


@pytest.fixture
def generated(tmp_path, monkeypatch):
    users = [
        {"id": 1, "full_name": "Admin One", "email": "a@x.test", "role": "admin"},
        {"id": 2, "full_name": "Mrs. Ama Mensah", "email": "t@x.test", "role": "teacher"},
        {"id": 3, "full_name": "Kofi Boateng", "email": "s1@x.test", "role": "student"},
        {"id": 4, "full_name": "Zed Zedson", "email": "s2@x.test", "role": "student"},
    ]
    students = [
        {"student_number": "1010000001", "class_name": "Year 10", "teacher_email": "t@x.test"},
        {"student_number": "1010000002", "class_name": "Nursery", "teacher_email": "t@x.test"},
    ]
    monkeypatch.setattr(seed_hub, "_load_people", lambda: (users, students))
    counts = seed_hub.generate_csvs(tmp_path, weeks=2, seed=7, today=date(2026, 11, 11))
    return tmp_path, counts


def read(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_generated_csvs_have_the_documented_shape(generated):
    out_dir, counts = generated

    gender = read(out_dir / "gender.csv")
    assert {row["gender"] for row in gender} <= {"female", "male"}
    assert dict((row["email"], row["gender"]) for row in gender)["t@x.test"] == "female"
    assert counts["gender"] == 4

    attendance = read(out_dir / "attendance.csv")
    assert counts["attendance"] == len(attendance) == 2 * 11
    assert {row["status"] for row in attendance} <= set(seed_hub.STATUSES)
    assert all(row["marked_by_email"] == "t@x.test" for row in attendance)
    assert all(date.fromisoformat(row["attendance_date"]).weekday() < 5 for row in attendance)

    fees = read(out_dir / "fees.csv")
    assert counts["fees"] == len(fees) == 6
    nursery = [row for row in fees if row["student_number"] == "1010000002"]
    assert {row["tuition_fee"] for row in nursery} == {"1500"}
    assert {row["due_date"] for row in fees} == {"2026-02-01", "2026-06-01", "2026-10-01"}

    payments = read(out_dir / "payments.csv")
    assert counts["payments"] == len(payments)
    assert all(date.fromisoformat(row["paid_on"]) <= date(2026, 11, 11) for row in payments)
    paid = {}
    for row in payments:
        key = (row["student_number"], row["term"])
        paid[key] = paid.get(key, Decimal(0)) + Decimal(row["amount"])
    totals = {(row["student_number"], row["term"]): Decimal(row["tuition_fee"])
              + Decimal(row["activities_fee"]) + Decimal(row["misc_fee"]) for row in fees}
    assert all(paid[key] <= totals[key] for key in paid)

    expenses = read(out_dir / "expenses.csv")
    assert counts["expenses"] == len(expenses) > 0
    assert all(Decimal(row["amount"]) > 0 for row in expenses)
    assert all(date.fromisoformat(row["paid_on"]) <= date(2026, 11, 11) for row in expenses)


def test_generation_is_repeatable_for_the_same_seed(tmp_path, monkeypatch):
    users = [{"id": 3, "full_name": "Kofi Boateng", "email": "s1@x.test", "role": "student"}]
    students = [{"student_number": "1010000001", "class_name": "Year 10",
                 "teacher_email": "s1@x.test"}]
    monkeypatch.setattr(seed_hub, "_load_people", lambda: (users, students))
    first, second = tmp_path / "a", tmp_path / "b"
    seed_hub.generate_csvs(first, weeks=1, seed=3, today=date(2026, 11, 11))
    seed_hub.generate_csvs(second, weeks=1, seed=3, today=date(2026, 11, 11))

    written = sorted(path.name for path in first.iterdir())
    assert written == sorted(name for name in seed_hub.FILES if name != "results.csv")
    for name in written:
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_reader_rejects_files_with_missing_columns(tmp_path):
    bad = tmp_path / "fees.csv"
    bad.write_text("student_number,term\n1,1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing columns"):
        seed_hub._read(bad, ("student_number", "term", "tuition_fee"))


def test_money_and_date_validation():
    assert seed_hub._money(" 12.5 ", "x") == Decimal("12.50")
    for value in ("abc", "-1", "NaN", "100000000"):
        with pytest.raises(seed_hub.RowError):
            seed_hub._money(value, "x")
    with pytest.raises(seed_hub.RowError):
        seed_hub._date("11/11/2026", "x")
