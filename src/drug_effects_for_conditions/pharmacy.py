"""Clean pharmacy claims CSV files."""

from dataclasses import dataclass
from pathlib import Path
from typing import Final

lazy import polars as pl

from drug_effects_for_conditions.claims import (
    ClaimsSchemaError,
    CleanStats,
    Lazy,
    StrPath,
    check_buckets,
    empty_to_null,
    file_errors,
    is_blank_expr,
    scan_claims,
    staged_outputs,
    write_unique_csv,
)
from drug_effects_for_conditions.ndc import (
    ndc_invalid_reason_expr,
    ndc_is_valid_expr,
    normalize_ndc_expr,
)

__all__ = [
    "PHARMACY_COLUMNS",
    "CleanResult",
    "CleanStats",
    "PharmacySchemaError",
    "StrPath",
    "clean_pharmacy",
    "dedupe_ndc_pharm_data",
    "default_rejects_path",
    "scan_pharmacy",
]

PHARMACY_COLUMNS: Final = (
    "Patient_ID",
    "Date_Submitted",
    "NDC",
    "Amount_Billed",
    "Amount_Paid",
)


class PharmacySchemaError(ClaimsSchemaError):
    """The CSV header does not match `PHARMACY_COLUMNS`."""


@dataclass(frozen=True, slots=True)
class CleanResult:
    """Lazy outputs of `clean_pharmacy`: `rejects` has the input columns plus `reason`."""

    clean: Lazy
    rejects: Lazy


def scan_pharmacy(path: StrPath) -> pl.LazyFrame:
    """Lazily scan a pharmacy CSV with every column read as a string."""
    return scan_claims(path, PHARMACY_COLUMNS, error=PharmacySchemaError)


def _split_pharmacy(frame: pl.LazyFrame) -> tuple[pl.LazyFrame, pl.LazyFrame]:
    """Drop blank rows, then split into normalized valid rows (not yet unique) and rejects."""
    frame = empty_to_null(frame).filter(~is_blank_expr())
    valid = ndc_is_valid_expr("NDC")
    normalized = frame.filter(valid).with_columns(normalize_ndc_expr("NDC"))
    rejects = frame.filter(~valid).with_columns(ndc_invalid_reason_expr("NDC").alias("reason"))
    return normalized, rejects


def clean_pharmacy(frame: pl.LazyFrame) -> CleanResult:
    """Drop blank rows, split off invalid NDCs, normalize the rest and drop duplicates.

    Duplicates are dropped after normalization, keeping first occurrences.
    """
    normalized, rejects = _split_pharmacy(frame)
    return CleanResult(clean=normalized.unique(maintain_order=True), rejects=rejects)


def dedupe_ndc_pharm_data(
    input_path: StrPath,
    output_path: StrPath,
    rejects_path: StrPath | None = None,
    *,
    buckets: int | None = None,
    keep_order: bool = False,
) -> CleanStats:
    """Clean `input_path`; write the result to `output_path` and invalid rows to `rejects_path`.

    Both files get a header and are written atomically: a failed run leaves existing files as
    they were. `rejects_path` defaults to `<output stem>.rejects.csv` next to the output.
    Raises `ClaimsFileError` for a malformed input file and `ValueError` for `buckets < 1`.

    By default one `unique` pass keeps every distinct row in memory and the output in input
    order. With `buckets=N` the rows are hash-partitioned into N Parquet files in a scratch
    directory beside the output, each de-duplicated on its own, so peak memory follows the
    largest bucket instead; the output is then in bucket order, and `keep_order=True` restores
    input order at the cost of holding the distinct rows in memory for one sort. `keep_order`
    without `buckets` does nothing, since the single pass already keeps order.
    """
    check_buckets(buckets)
    out = Path(output_path)
    rejects_out = Path(rejects_path) if rejects_path is not None else default_rejects_path(out)
    with staged_outputs(out, rejects_out) as (clean_tmp, rejects_tmp), file_errors(input_path):
        frame = empty_to_null(scan_pharmacy(input_path))
        normalized, rejects = _split_pharmacy(frame)
        blank = is_blank_expr()
        counts = frame.select(
            pl.len().alias("rows_in"),
            blank.sum().alias("blank_rows"),
            (~blank & ~ndc_is_valid_expr("NDC")).sum().alias("rejected"),
        )
        rows_out, counted = write_unique_csv(
            normalized,
            clean_tmp,
            counts=counts,
            side_sinks=[rejects.sink_csv(rejects_tmp, lazy=True)],
            buckets=buckets,
            keep_order=keep_order,
        )
    return CleanStats(rows_out=rows_out, **counted.row(0, named=True))


def default_rejects_path(output_path: StrPath) -> Path:
    """`out/clean.csv` -> `out/clean.rejects.csv`."""
    out = Path(output_path)
    return out.with_name(f"{out.stem}.rejects.csv")
