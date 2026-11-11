"""Read exam scores from an uploaded CSV file.

Accepted layouts (column names are matched without regard to case, spaces or
underscores):

- the template from the results page: student_number, student, score
- the results export: Class, Student number, Student, Subject, Term, Score, Grade

Only the student number and score columns are read. A blank score means
"not entered" and is skipped. Everything else is checked and reported with
its row number, and nothing is saved unless the whole file is valid.
"""
import csv
import io
from decimal import Decimal, InvalidOperation

from grading import grade_for


MAX_UPLOAD_BYTES = 512 * 1024
MAX_UPLOAD_ROWS = 1000
NUMBER_COLUMNS = ("studentnumber", "indexnumber", "studentid")
SCORE_COLUMNS = ("score", "examscore", "mark")


class ScoreFileError(ValueError):
    """The file can't be used; the message is safe to show the teacher."""


def _key(name: str) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def parse_score_csv(data: bytes) -> dict[str, Decimal]:
    if not data.strip():
        raise ScoreFileError("The file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ScoreFileError("The file is larger than 512 KB.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ScoreFileError("Save the file as CSV (UTF-8) and upload it again.") from None

    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except (StopIteration, csv.Error):
        raise ScoreFileError("The file has no header row.") from None
    keys = [_key(name) for name in header]
    number_col = next((keys.index(k) for k in NUMBER_COLUMNS if k in keys), None)
    score_col = next((keys.index(k) for k in SCORE_COLUMNS if k in keys), None)
    if number_col is None or score_col is None:
        raise ScoreFileError(
            "The file needs a 'student_number' column and a 'score' column. "
            "Download the template to see the layout."
        )

    scores: dict[str, Decimal] = {}
    try:
        for row_number, row in enumerate(reader, start=2):
            if row_number - 1 > MAX_UPLOAD_ROWS:
                raise ScoreFileError(f"The file has more than {MAX_UPLOAD_ROWS} rows.")
            if not any(cell.strip() for cell in row):
                continue
            cells = row + [""] * (max(number_col, score_col) + 1 - len(row))
            # Exports protect cells from spreadsheet formulas with a leading apostrophe.
            number = cells[number_col].strip().lstrip("'").strip()
            raw_score = cells[score_col].strip().lstrip("'").strip()
            if not number:
                raise ScoreFileError(f"Row {row_number}: the student number is missing.")
            if len(number) > 20:
                raise ScoreFileError(f"Row {row_number}: the student number is too long.")
            if not raw_score:
                continue
            try:
                score = Decimal(raw_score)
                grade_for(score)
            except (InvalidOperation, ValueError):
                raise ScoreFileError(
                    f"Row {row_number}: '{raw_score[:20]}' is not a score from 0 to 100."
                ) from None
            if score != score.quantize(Decimal("0.01")):
                raise ScoreFileError(f"Row {row_number}: use at most two decimal places.")
            if number in scores:
                raise ScoreFileError(f"Row {row_number}: student {number} appears twice.")
            scores[number] = score
    except csv.Error:
        raise ScoreFileError("The file is not valid CSV.") from None
    if not scores:
        raise ScoreFileError("The file has no scores filled in.")
    return scores
