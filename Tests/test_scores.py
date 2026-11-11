import csv
import io
from datetime import date
from decimal import Decimal

import pytest

from Routes import teacher
from database import seed_hub
from score_upload import ScoreFileError, parse_score_csv
from test_school_access import new_csrf_token


def sign_in_teacher(client, user_id=17):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["role"] = "teacher"
        session["mfa_verified"] = True
        session["full_name"] = "Sir Kofi Mensah"


# ---------------------------------------------------------------- parser

def test_parser_reads_the_template_layout():
    data = "student_number,student,score\n1010000001,Ama Owusu,78.5\n1010000002,Kojo,\n".encode()

    assert parse_score_csv(data) == {"1010000001": Decimal("78.5")}


def test_parser_reads_the_results_export_with_bom_and_protected_cells():
    data = (
        "﻿Class,Student number,Student,Subject,Term,Score,Grade\n"
        "Year 10,'1010000001,Ama Owusu,History,1,82,A\n"
        "Year 10,1010000002,Kojo Asante,History,1,64.25,C\n"
    ).encode("utf-8")

    assert parse_score_csv(data) == {
        "1010000001": Decimal("82"),
        "1010000002": Decimal("64.25"),
    }


@pytest.mark.parametrize("content,message", [
    ("", "empty"),
    ("name,mark\nAma,50\n", "student_number"),
    ("student_number,score\n1,101\n", "Row 2"),
    ("student_number,score\n1,-1\n", "Row 2"),
    ("student_number,score\n1,abc\n", "Row 2"),
    ("student_number,score\n1,50.123\n", "two decimal"),
    ("student_number,score\n1,50\n1,60\n", "appears twice"),
    ("student_number,score\n,50\n", "missing"),
    ("student_number,score\n1,\n2,\n", "no scores"),
    ("student_number,score\n1,NaN\n", "Row 2"),
])
def test_parser_rejects_bad_files(content, message):
    with pytest.raises(ScoreFileError, match=message):
        parse_score_csv(content.encode())


def test_parser_rejects_large_and_non_utf8_files():
    with pytest.raises(ScoreFileError, match="512 KB"):
        parse_score_csv(b"student_number,score\n" + b"1,50\n" * 200_000)
    with pytest.raises(ScoreFileError, match="UTF-8"):
        parse_score_csv("student_number,score\n1,50\n".encode("utf-16"))


# ---------------------------------------------------------------- routes

def upload(client, token, content, filename="scores.csv", term="2"):
    return client.post(
        "/teacher/classes/3/results/5/upload",
        data={"csrf_token": token, "term": term,
              "scores_file": (io.BytesIO(content.encode()), filename)},
        content_type="multipart/form-data",
    )


def test_upload_saves_scores_for_the_signed_in_teacher(client, monkeypatch):
    calls = []
    monkeypatch.setattr(teacher.school, "save_uploaded_results",
                        lambda *args: calls.append(args) or 2)
    token = new_csrf_token(client)
    sign_in_teacher(client)

    response = upload(client, token, "student_number,score\nA1,80\nA2,55.5\n")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/teacher/classes/3/results/5?term=2")
    assert calls == [(17, 3, 5, 2, {"A1": Decimal("80"), "A2": Decimal("55.5")}, "127.0.0.1")]


def test_upload_refuses_wrong_files_before_saving(client, monkeypatch):
    calls = []
    monkeypatch.setattr(teacher.school, "save_uploaded_results",
                        lambda *args: calls.append(args) or 1)
    token = new_csrf_token(client)
    sign_in_teacher(client)

    upload(client, token, "student_number,score\nA1,80\n", filename="scores.xlsx")
    upload(client, token, "student_number,score\nA1,180\n")
    upload(client, token, "student_number,score\nA1,80\n", term="4")

    assert calls == []


def test_upload_is_forbidden_for_a_class_the_teacher_does_not_teach(client, monkeypatch):
    monkeypatch.setattr(teacher.school, "save_uploaded_results", lambda *args: None)
    token = new_csrf_token(client)
    sign_in_teacher(client)

    assert upload(client, token, "student_number,score\nA1,80\n").status_code == 403


def test_upload_requires_teacher_role_and_csrf(client, monkeypatch):
    calls = []
    monkeypatch.setattr(teacher.school, "save_uploaded_results",
                        lambda *args: calls.append(args) or 1)
    token = new_csrf_token(client)
    with client.session_transaction() as session:
        session.update(user_id=501, role="student", mfa_verified=True)
    assert upload(client, token, "student_number,score\nA1,80\n").status_code == 403

    sign_in_teacher(client)
    response = client.post(
        "/teacher/classes/3/results/5/upload",
        data={"term": "1", "scores_file": (io.BytesIO(b"student_number,score\nA1,80\n"), "s.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert calls == []


def test_template_lists_the_class_with_existing_scores(client, monkeypatch):
    monkeypatch.setattr(
        teacher.school, "get_class_roster_for_results",
        lambda teacher_id, class_id, subject_id, term: [
            {"id": 1, "student_number": "1010000001", "full_name": "Ama Owusu",
             "class_name": "Year 10", "subject_name": "History", "score": Decimal("71.00")},
            {"id": 2, "student_number": "1010000002", "full_name": "=HYPERLINK(1)",
             "class_name": "Year 10", "subject_name": "History", "score": None},
        ] if (teacher_id, class_id, subject_id, term) == (17, 3, 5, 2) else None,
    )
    sign_in_teacher(client)

    response = client.get("/teacher/classes/3/results/5/template.csv?term=2")
    rows = list(csv.reader(io.StringIO(response.get_data(as_text=True).lstrip("﻿"))))

    assert response.status_code == 200
    assert "scores-year-10-history-term-2.csv" in response.headers["Content-Disposition"]
    assert rows == [
        ["student_number", "student", "score"],
        ["1010000001", "Ama Owusu", "71.00"],
        ["1010000002", "'=HYPERLINK(1)", ""],
    ]
    assert parse_score_csv(response.data) == {"1010000001": Decimal("71.00")}
    assert client.get("/teacher/classes/9/results/5/template.csv?term=2").status_code == 403


def test_results_page_offers_upload_and_term_tabs(client, monkeypatch):
    monkeypatch.setattr(
        teacher.school, "get_class_roster_for_results",
        lambda *a: [{"id": 1, "student_number": "1", "full_name": "Ama Owusu",
                     "class_name": "Year 10", "subject_name": "History", "score": None}],
    )
    sign_in_teacher(client)

    page = client.get("/teacher/classes/3/results/5?term=2").get_data(as_text=True)

    assert 'enctype="multipart/form-data"' in page
    assert "/teacher/classes/3/results/5/template.csv?term=2" in page
    assert 'name="term" value="2"' in page
    for term in (1, 2, 3):
        assert f"/teacher/classes/3/results/5?term={term}" in page


def test_dashboard_lists_subject_classes_with_score_links(client, monkeypatch):
    monkeypatch.setattr(teacher.school, "get_teacher_dashboard", lambda *a: {
        "term": 1, "attendance_trend": [],
        "classes": [{"id": 3, "name": "Year 10", "is_class_teacher": False, "subjects": [
            {"subject_id": 5, "subject_name": "History", "average": None, "student_count": 23}]}],
    })
    sign_in_teacher(client)

    page = client.get("/teacher").get_data(as_text=True)

    assert "Subject teacher" in page
    assert "/teacher/classes/3/attendance" in page
    assert "Enter or upload scores" in page


# ---------------------------------------------------------------- generator

def test_generated_results_cover_each_pair_once_per_term(tmp_path, monkeypatch):
    teaching = [
        {"student_number": "1", "subject": "History", "teacher_email": "t1@x.test"},
        {"student_number": "1", "subject": "Maths", "teacher_email": "t2@x.test"},
        {"student_number": "1", "subject": "Maths", "teacher_email": "t3@x.test"},
        {"student_number": "2", "subject": "History", "teacher_email": "t1@x.test"},
    ]
    monkeypatch.setattr(seed_hub, "_load_teaching", lambda: teaching)

    rows = seed_hub.generate_results_csv(tmp_path, (2, 1), seed=5)
    with (tmp_path / "results.csv").open(newline="", encoding="utf-8") as handle:
        written = list(csv.DictReader(handle))

    assert rows == len(written) == 6
    assert {(r["student_number"], r["subject"], r["term"]) for r in written} == {
        (s, subj, t) for s, subj in (("1", "History"), ("1", "Maths"), ("2", "History"))
        for t in ("1", "2")
    }
    assert all(15 <= float(r["score"]) <= 99 for r in written)
    maths = [r for r in written if r["subject"] == "Maths"]
    assert {r["entered_by_email"] for r in maths} == {"t2@x.test"}


def test_assign_class_teachers_command_is_registered(app):
    result = app.test_cli_runner().invoke(args=["assign-class-teachers", "--help"])

    assert result.exit_code == 0
    assert "class teacher" in result.output


def test_seed_results_command_rejects_bad_terms(app):
    runner = app.test_cli_runner()

    for value in ("4", "a,b", ""):
        result = runner.invoke(args=["seed-results", "--terms", value, "--yes"])
        assert result.exit_code != 0
