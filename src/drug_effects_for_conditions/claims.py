"""Helpers shared by the pharmacy and medical claims cleaners."""

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

lazy import polars as pl

type StrPath = str | os.PathLike[str]
# An alias, not `pl.LazyFrame` inline: dataclasses resolve annotations and would import polars.
type Lazy = pl.LazyFrame


class ClaimsSchemaError(ValueError):
    """The CSV header does not match the expected columns."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CleanStats:
    rows_in: int
    rows_out: int
    blank_rows: int = 0
    rejected: int = 0

    @property
    def duplicates_removed(self) -> int:
        return self.rows_in - self.blank_rows - self.rejected - self.rows_out


def scan_claims(
    path: StrPath,
    columns: Sequence[str],
    *,
    error: type[ClaimsSchemaError] = ClaimsSchemaError,
) -> pl.LazyFrame:
    """Lazily scan a CSV whose header must be `columns`, reading every column as a string.

    A wrong header raises `error`, so each cleaner can raise its own subclass.
    """
    csv_path = Path(path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"No such file: {csv_path}")
    header = pl.scan_csv(csv_path, infer_schema=False).collect_schema().names()
    if tuple(header) != tuple(columns):
        raise error(f"Expected columns {list(columns)}, found {header}")
    return pl.scan_csv(csv_path, schema=dict.fromkeys(columns, pl.String))


def empty_to_null(frame: pl.LazyFrame) -> pl.LazyFrame:
    # polars reads a bare empty field as null but a quoted one ("") as "": make them one value.
    return frame.with_columns(pl.all().replace("", None))


def is_blank_expr() -> pl.Expr:
    return pl.all_horizontal(pl.all().str.strip_chars().fill_null("") == "")


def count_rows(path: StrPath) -> int:
    """Data rows in a CSV written by this package."""
    return int(pl.scan_csv(Path(path), infer_schema=False).select(pl.len()).collect().item())
