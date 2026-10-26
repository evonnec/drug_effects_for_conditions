"""Clean pharmacy claims CSV files."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

lazy import polars as pl

from drug_effects_for_conditions.ndc import (
    ndc_invalid_reason_expr,
    ndc_is_valid_expr,
    normalize_ndc_expr,
)

PHARMACY_COLUMNS: Final = (
    "Patient_ID",
    "Date_Submitted",
    "NDC",
    "Amount_Billed",
    "Amount_Paid",
)

type StrPath = str | os.PathLike[str]
# An alias, not `pl.LazyFrame` inline: the dataclass below would resolve that and import polars.
type Lazy = pl.LazyFrame


class PharmacySchemaError(ValueError):
    """The CSV header does not match `PHARMACY_COLUMNS`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CleanStats:
    rows_in: int
    rows_out: int
    blank_rows: int = 0
    rejected: int = 0

    @property
    def duplicates_removed(self) -> int:
        return self.rows_in - self.blank_rows - self.rejected - self.rows_out


@dataclass(frozen=True, slots=True)
class CleanResult:
    """Lazy outputs of `clean_pharmacy`: `rejects` has the input columns plus `reason`."""

    clean: Lazy
    rejects: Lazy


def scan_pharmacy(path: StrPath) -> pl.LazyFrame:
    """Lazily scan a pharmacy CSV with every column read as a string."""
    csv_path = Path(path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"No such file: {csv_path}")
    header = pl.scan_csv(csv_path, infer_schema=False).collect_schema().names()
    if tuple(header) != PHARMACY_COLUMNS:
        raise PharmacySchemaError(f"Expected columns {list(PHARMACY_COLUMNS)}, found {header}")
    return pl.scan_csv(csv_path, schema=dict.fromkeys(PHARMACY_COLUMNS, pl.String))


def _empty_to_null(frame: pl.LazyFrame) -> pl.LazyFrame:
    # polars reads a bare empty field as null but a quoted one ("") as "": make them one value.
    return frame.with_columns(pl.all().replace("", None))


def _is_blank_expr() -> pl.Expr:
    return pl.all_horizontal(pl.all().str.strip_chars().fill_null("") == "")


def clean_pharmacy(frame: pl.LazyFrame) -> CleanResult:
    """Drop blank rows, split off invalid NDCs, normalize the rest and drop duplicates.

    Duplicates are dropped after normalization, keeping first occurrences.
    """
    frame = _empty_to_null(frame).filter(~_is_blank_expr())
    valid = ndc_is_valid_expr("NDC")
    clean = frame.filter(valid).with_columns(normalize_ndc_expr("NDC")).unique(maintain_order=True)
    rejects = frame.filter(~valid).with_columns(ndc_invalid_reason_expr("NDC").alias("reason"))
    return CleanResult(clean=clean, rejects=rejects)


def dedupe_ndc_pharm_data(
    input_path: StrPath, output_path: StrPath, rejects_path: StrPath | None = None
) -> CleanStats:
    """Clean `input_path`; write the result to `output_path` and invalid rows to `rejects_path`.

    Both files get a header. `rejects_path` defaults to `<output stem>.rejects.csv` next to the
    output.
    """
    out = Path(output_path)
    rejects_out = Path(rejects_path) if rejects_path is not None else default_rejects_path(out)
    frame = _empty_to_null(scan_pharmacy(input_path))
    result = clean_pharmacy(frame)
    result.clean.sink_csv(out)
    result.rejects.sink_csv(rejects_out)
    rows_out = pl.scan_csv(out, infer_schema=False).select(pl.len()).collect().item()
    blank = _is_blank_expr()
    counts = frame.select(
        pl.len().alias("rows_in"),
        blank.sum().alias("blank_rows"),
        (~blank & ~ndc_is_valid_expr("NDC")).sum().alias("rejected"),
    ).collect()
    return CleanStats(rows_out=rows_out, **counts.row(0, named=True))


def default_rejects_path(output_path: StrPath) -> Path:
    """`out/clean.csv` -> `out/clean.rejects.csv`."""
    out = Path(output_path)
    return out.with_name(f"{out.stem}.rejects.csv")
