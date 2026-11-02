from decimal import Decimal

import pytest

from grading import grade_for


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        ("0", "F"),
        ("49.99", "F"),
        ("50", "D"),
        ("59.99", "D"),
        ("60", "C"),
        ("69.99", "C"),
        ("70", "B"),
        ("79.99", "B"),
        ("80", "A"),
        ("100", "A"),
    ],
)
def test_grade_boundaries(score, expected):
    assert grade_for(Decimal(score)) == expected


@pytest.mark.parametrize("score", ["-0.01", "100.01", "NaN", "Infinity", "not-a-score"])
def test_grade_for_rejects_scores_outside_valid_range(score):
    with pytest.raises(ValueError):
        grade_for(score)
