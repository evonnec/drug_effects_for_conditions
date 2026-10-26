"""Clean medical claims CSV files."""

from typing import Final

lazy import polars as pl

from drug_effects_for_conditions.claims import (
    ClaimsSchemaError,
    CleanStats,
    StrPath,
    check_buckets,
    empty_to_null,
    file_errors,
    is_blank_expr,
    scan_claims,
    staged_outputs,
    write_unique_csv,
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


def _strip_medical(frame: pl.LazyFrame) -> pl.LazyFrame:
    """Drop blank rows and strip diagnosis padding (blank codes become null); not yet unique."""
    frame = empty_to_null(frame).filter(~is_blank_expr())
    diagnosis = pl.col(DIAGNOSIS_COLUMNS).str.strip_chars().replace("", None)
    return frame.with_columns(diagnosis)


def clean_medical(frame: pl.LazyFrame) -> pl.LazyFrame:
    """Drop blank rows, strip diagnosis padding (blank codes become null) and drop duplicates.

    Duplicates are dropped after stripping, keeping first occurrences. Only exact duplicates
    go: rows that differ in an amount are separate claim lines and are kept.
    """
    return _strip_medical(frame).unique(maintain_order=True)


def dedupe_medical_data(
    input_path: StrPath,
    output_path: StrPath,
    *,
    buckets: int | None = None,
    keep_order: bool = False,
) -> CleanStats:
    """Clean `input_path` and write the result, with a header, to `output_path`.

    The write is atomic: a failed run leaves an existing file as it was. Raises
    `ClaimsFileError` for a malformed input file and `ValueError` for `buckets < 1`.

    `buckets` and `keep_order` work as in `dedupe_ndc_pharm_data`: `buckets=N` trades the single
    in-memory `unique` for N hash partitions de-duplicated one at a time, and `keep_order=True`
    restores input order (a no-op without `buckets`).
    """
    check_buckets(buckets)
    with staged_outputs(output_path) as (temp,), file_errors(input_path):
        frame = empty_to_null(scan_medical(input_path))
        counts = frame.select(pl.len().alias("rows_in"), is_blank_expr().sum().alias("blank_rows"))
        rows_out, counted = write_unique_csv(
            _strip_medical(frame), temp, counts=counts, buckets=buckets, keep_order=keep_order
        )
    return CleanStats(rows_out=rows_out, **counted.row(0, named=True))
