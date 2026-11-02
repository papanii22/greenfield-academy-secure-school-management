from flask import abort

from database.school import CURRENT_TERM


def parse_term(value: str | None) -> int:
    if value is None:
        return CURRENT_TERM
    try:
        term = int(value)
    except ValueError:
        abort(400)
    if term not in (1, 2, 3):
        abort(400)
    return term


def parse_page(value: str | None) -> int:
    if value is None:
        return 1
    try:
        page = int(value)
    except ValueError:
        abort(400)
    if page < 1:
        abort(400)
    return page
