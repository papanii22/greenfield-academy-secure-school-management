from decimal import Decimal, InvalidOperation


def grade_for(score: int | float | Decimal | str) -> str:
    try:
        numeric_score = Decimal(str(score))
    except (InvalidOperation, ValueError):
        raise ValueError("Score must be a number between 0 and 100.") from None

    if not numeric_score.is_finite() or not Decimal("0") <= numeric_score <= Decimal("100"):
        raise ValueError("Score must be a number between 0 and 100.")
    if numeric_score >= Decimal("80"):
        return "A"
    if numeric_score >= Decimal("70"):
        return "B"
    if numeric_score >= Decimal("60"):
        return "C"
    if numeric_score >= Decimal("50"):
        return "D"
    return "F"
