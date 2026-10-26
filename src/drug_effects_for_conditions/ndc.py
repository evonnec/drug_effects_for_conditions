"""National Drug Code (NDC) normalization and validation."""

import re
from typing import Final

lazy import polars as pl

NDC_LENGTH: Final = 11


def normalize_ndc(raw: str) -> str:
    """Remove every '-' and left-pad with '0' to 11 characters."""
    return raw.replace("-", "").rjust(NDC_LENGTH, "0")


def normalize_ndc_expr(column: str | pl.Expr = "NDC") -> pl.Expr:
    """Vectorized equivalent of `normalize_ndc` for a polars string column."""
    return _dashless(column).str.pad_start(NDC_LENGTH, "0")


def is_valid_ndc(raw: str | None) -> bool:
    """True if the dash-stripped value is 1-11 ASCII digits and not all zeros."""
    if raw is None:
        return False
    digits = raw.replace("-", "")
    return re.fullmatch(r"[0-9]{1,11}", digits) is not None and digits.strip("0") != ""


def _dashless(column: str | pl.Expr) -> pl.Expr:
    expr = pl.col(column) if isinstance(column, str) else column
    return expr.str.replace_all("-", "", literal=True)


def ndc_is_valid_expr(column: str | pl.Expr = "NDC") -> pl.Expr:
    """Vectorized equivalent of `is_valid_ndc`; null is invalid, never null."""
    digits = _dashless(column)
    valid = digits.str.contains(r"^[0-9]{1,11}$") & ~digits.str.contains(r"^0+$")
    return valid.fill_null(False)


def ndc_invalid_reason_expr(column: str | pl.Expr = "NDC") -> pl.Expr:
    """Why `ndc_is_valid_expr` is false: a short message, or null for a valid NDC."""
    digits = _dashless(column)
    return (
        pl.when(digits.is_null())
        .then(pl.lit("NDC is missing"))
        .when(digits.str.len_chars() == 0)
        .then(pl.lit("NDC has no digits"))
        .when(digits.str.contains("[^0-9]"))
        .then(pl.lit("NDC has characters other than digits and dashes"))
        .when(digits.str.len_chars() > NDC_LENGTH)
        .then(pl.lit(f"NDC has more than {NDC_LENGTH} digits"))
        .when(digits.str.contains(r"^0+$"))
        .then(pl.lit("NDC is all zeros"))
    )
