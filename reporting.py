import csv
import html
import io
from pathlib import Path
from typing import Any, Sequence

import reportlab
from flask import Response, abort
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle


MAX_REPORT_ROWS = 5000


class UnsupportedPDFCharacter(ValueError):
    pass


def report_response(
    rows: Sequence[Sequence[Any]],
    headers: Sequence[str],
    title: str,
    filename: str,
    output_format: str,
) -> Response:
    if len(rows) > MAX_REPORT_ROWS:
        abort(413)
    if output_format == "csv":
        return csv_response(rows, headers, f"{filename}.csv")
    if output_format == "pdf":
        try:
            return pdf_response(rows, headers, title, f"{filename}.pdf")
        except UnsupportedPDFCharacter:
            return Response(
                "This PDF contains characters unsupported by the included font. Download the report as CSV instead.",
                status=422,
                content_type="text/plain; charset=utf-8",
            )
    abort(404)


def csv_response(
    rows: Sequence[Sequence[Any]],
    headers: Sequence[str],
    filename: str,
) -> Response:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_csv_value(value) for value in row])

    response = Response(
        output.getvalue().encode("utf-8-sig"),
        content_type="text/csv; charset=utf-8",
    )
    return _set_download_headers(response, filename)


def pdf_response(
    rows: Sequence[Sequence[Any]],
    headers: Sequence[str],
    title: str,
    filename: str,
) -> Response:
    font_name = _register_export_font()
    buffer = io.BytesIO()
    page_size = landscape(A4)
    document = SimpleDocTemplate(
        buffer,
        pagesize=page_size,
        leftMargin=30,
        rightMargin=30,
        topMargin=36,
        bottomMargin=32,
        title=title,
        author="Greenfield Academy",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ExportTitle",
        parent=styles["Title"],
        fontName=font_name,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#153c3a"),
        spaceAfter=12,
    )
    cell_style = ParagraphStyle(
        "ExportCell",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=7.5,
        leading=9,
        spaceAfter=0,
    )
    header_style = ParagraphStyle(
        "ExportHeader",
        parent=cell_style,
        textColor=colors.white,
    )
    table_data = [[
        Paragraph(_pdf_text(value, font_name), header_style) for value in headers
    ]]
    table_data.extend(
        [
            Paragraph(_pdf_text(value, font_name), cell_style)
            for value in row
        ]
        for row in rows
    )
    usable_width = page_size[0] - document.leftMargin - document.rightMargin
    column_width = usable_width / len(headers)
    table = Table(
        table_data,
        colWidths=[column_width] * len(headers),
        repeatRows=1,
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#153c3a")),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5d3")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f6f5")]),
            ]
        )
    )
    document.build(
        [Paragraph(_pdf_text(title, font_name), title_style), table],
        onFirstPage=lambda canvas, doc: _draw_page_number(canvas, doc, font_name),
        onLaterPages=lambda canvas, doc: _draw_page_number(canvas, doc, font_name),
    )

    response = Response(buffer.getvalue(), mimetype="application/pdf")
    return _set_download_headers(response, filename)


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, str) and value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def _pdf_text(value: Any, font_name: str) -> str:
    text = "" if value is None else str(value)
    font = pdfmetrics.getFont(font_name)
    supported = font.face.charToGlyph
    if any(ord(character) not in supported for character in text):
        raise UnsupportedPDFCharacter(
            "The PDF font does not support every character in this report."
        )
    return html.escape(text, quote=False).replace("\n", "<br/>")


def _register_export_font() -> str:
    font_name = "GreenfieldVera"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        font_path = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
        pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
    return font_name


def _draw_page_number(canvas: Any, document: Any, font_name: str) -> None:
    canvas.saveState()
    canvas.setFont(font_name, 8)
    canvas.setFillColor(colors.HexColor("#526562"))
    canvas.drawRightString(document.pagesize[0] - 30, 18, f"Page {document.page}")
    canvas.restoreState()


def _set_download_headers(response: Response, filename: str) -> Response:
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
