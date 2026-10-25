"""National Drug Code (NDC) normalization."""

from typing import Final

lazy import polars as pl

NDC_LENGTH: Final = 11


def normalize_ndc(raw: str) -> str:
    """Remove every '-' and left-pad with '0' to 11 characters."""
    return raw.replace("-", "").rjust(NDC_LENGTH, "0")


def normalize_ndc_expr(column: str | pl.Expr = "NDC") -> pl.Expr:
    """Vectorized equivalent of `normalize_ndc` for a polars string column."""
    expr = pl.col(column) if isinstance(column, str) else column
    return expr.str.replace_all("-", "", literal=True).str.pad_start(NDC_LENGTH, "0")
