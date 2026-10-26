"""Clean medical claims CSV files."""

from typing import Final

lazy import polars as pl

from drug_effects_for_conditions.claims import (
    ClaimsSchemaError,
    CleanStats,
    StrPath,
    count_rows,
    empty_to_null,
    file_errors,
    is_blank_expr,
    scan_claims,
    staged_outputs,
)

MEDICAL_COLUMNS: Final = (
    "Patient_ID",
    "Diagnosis_Code_1",
    "Diagnosis_Code_2",
    "Diagnosis_Code_3",
    "Diagnosis_Code_4",
    "Service_Date",
    "Amount_Paid",
    "Amount_Billed",
)
DIAGNOSIS_COLUMNS: Final = MEDICAL_COLUMNS[1:5]


class MedicalSchemaError(ClaimsSchemaError):
    """The CSV header does not match `MEDICAL_COLUMNS`."""


def scan_medical(path: StrPath) -> pl.LazyFrame:
    """Lazily scan a medical CSV with every column read as a string."""
    return scan_claims(path, MEDICAL_COLUMNS, error=MedicalSchemaError)


def clean_medical(frame: pl.LazyFrame) -> pl.LazyFrame:
    """Drop blank rows, strip diagnosis padding (blank codes become null) and drop duplicates.

    Duplicates are dropped after stripping, keeping first occurrences. Only exact duplicates
    go: rows that differ in an amount are separate claim lines and are kept.
    """
    frame = empty_to_null(frame).filter(~is_blank_expr())
    diagnosis = pl.col(DIAGNOSIS_COLUMNS).str.strip_chars().replace("", None)
    return frame.with_columns(diagnosis).unique(maintain_order=True)


def dedupe_medical_data(input_path: StrPath, output_path: StrPath) -> CleanStats:
    """Clean `input_path` and write the result, with a header, to `output_path`.

    The write is atomic: a failed run leaves an existing file as it was. Raises
    `ClaimsFileError` for a malformed input file.
    """
    with staged_outputs(output_path) as (temp,), file_errors(input_path):
        frame = empty_to_null(scan_medical(input_path))
        clean_medical(frame).sink_csv(temp)
        counts = frame.select(
            pl.len().alias("rows_in"), is_blank_expr().sum().alias("blank_rows")
        ).collect()
        rows_out = count_rows(temp)
    return CleanStats(rows_out=rows_out, **counts.row(0, named=True))
