import polars as pl
import pytest
from hypothesis import given
from hypothesis import strategies as st

from drug_effects_for_conditions.ndc import (
    NDC_LENGTH,
    normalize_ndc,
    normalize_ndc_expr,
)

ndc_text = st.text(alphabet="0123456789- abcxyz", min_size=0, max_size=15)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("00591-0845-10", "00591084510"),
        ("591-0845-10", "00591084510"),
        ("00591084510", "00591084510"),
        ("99999-999999", "99999999999"),
        ("", "00000000000"),
    ],
)
def test_normalize_examples(raw: str, expected: str) -> None:
    assert normalize_ndc(raw) == expected


@given(ndc_text)
def test_expr_matches_function(s: str) -> None:
    frame = pl.DataFrame({"NDC": [s]}, schema={"NDC": pl.String})
    assert frame.select(normalize_ndc_expr()).item() == normalize_ndc(s)


@given(ndc_text)
def test_idempotent(s: str) -> None:
    once = normalize_ndc(s)
    assert normalize_ndc(once) == once


@given(ndc_text)
def test_length(s: str) -> None:
    assert len(normalize_ndc(s)) == max(NDC_LENGTH, len(s.replace("-", "")))
