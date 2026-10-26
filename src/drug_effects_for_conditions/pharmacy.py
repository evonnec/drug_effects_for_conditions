"""Clean pharmacy claims CSV files."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

lazy import polars as pl

from drug_effects_for_conditions.ndc import normalize_ndc_expr

PHARMACY_COLUMNS: Final = (
    "Patient_ID",
    "Date_Submitted",
    "NDC",
    "Amount_Billed",
    "Amount_Paid",
)

type StrPath = str | os.PathLike[str]


class PharmacySchemaError(ValueError):
    """The CSV header does not match `PHARMACY_COLUMNS`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CleanStats:
    rows_in: int
    rows_out: int

    @property
    def duplicates_removed(self) -> int:
        return self.rows_in - self.rows_out


def scan_pharmacy(path: StrPath) -> pl.LazyFrame:
    """Lazily scan a pharmacy CSV with every column read as a string."""
    csv_path = Path(path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"No such file: {csv_path}")
    header = pl.scan_csv(csv_path, infer_schema=False).collect_schema().names()
    if tuple(header) != PHARMACY_COLUMNS:
        raise PharmacySchemaError(f"Expected columns {list(PHARMACY_COLUMNS)}, found {header}")
    return pl.scan_csv(csv_path, schema=dict.fromkeys(PHARMACY_COLUMNS, pl.String))


def clean_pharmacy(frame: pl.LazyFrame) -> pl.LazyFrame:
    """Normalize NDCs, then drop duplicate rows, keeping first occurrences."""
    return frame.with_columns(normalize_ndc_expr("NDC")).unique(maintain_order=True)


def dedupe_ndc_pharm_data(input_path: StrPath, output_path: StrPath) -> CleanStats:
    """Clean `input_path` and write the result, with a header, to `output_path`."""
    frame = scan_pharmacy(input_path)
    rows_in = frame.select(pl.len()).collect().item()
    out = Path(output_path)
    clean_pharmacy(frame).sink_csv(out)
    rows_out = pl.scan_csv(out, infer_schema=False).select(pl.len()).collect().item()
    return CleanStats(rows_in=rows_in, rows_out=rows_out)
