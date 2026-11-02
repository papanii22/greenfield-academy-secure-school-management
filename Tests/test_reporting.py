from reporting import (
    UnsupportedPDFCharacter,
    _pdf_text,
    _register_export_font,
    csv_response,
    pdf_response,
    report_response,
)


def test_csv_download_escapes_spreadsheet_formulas():
    response = csv_response(
        [("=1+1", "\t@SUM(A1:A2)", "Avery Student")],
        ["Name", "Comment", "Student"],
        "report.csv",
    )

    content = response.get_data().decode("utf-8-sig")
    assert response.mimetype == "text/csv"
    assert response.headers["Content-Disposition"] == 'attachment; filename="report.csv"'
    assert response.headers["Cache-Control"] == "no-store"
    assert "'=1+1" in content
    assert "'\t@SUM(A1:A2)" in content


def test_pdf_download_renders_unicode_and_attachment_headers():
    response = pdf_response(
        [("José Student", "Mathematics", "A")],
        ["Student", "Subject", "Grade"],
        "Student results",
        "results.pdf",
    )

    assert response.mimetype == "application/pdf"
    assert response.get_data().startswith(b"%PDF-")
    assert response.headers["Content-Disposition"] == 'attachment; filename="results.pdf"'
    assert response.headers["Cache-Control"] == "no-store"


def test_pdf_text_preserves_supported_unicode_and_rejects_unsupported_glyphs():
    font_name = _register_export_font()

    assert _pdf_text("José Student", font_name) == "José Student"
    try:
        _pdf_text("漢", font_name)
    except UnsupportedPDFCharacter:
        pass
    else:
        raise AssertionError("Unsupported PDF characters must not be silently replaced.")


def test_pdf_export_explains_when_csv_is_needed_for_unsupported_characters():
    response = report_response(
        [("漢",)],
        ["Student"],
        "Student report",
        "student-report",
        "pdf",
    )

    assert response.status_code == 422
    assert b"Download the report as CSV instead." in response.get_data()
