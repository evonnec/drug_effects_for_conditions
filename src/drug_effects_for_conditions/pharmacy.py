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
    count_rows,
    empty_to_null,
    file_errors,
    is_blank_expr,
    scan_claims,
    staged_outputs,
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


def clean_pharmacy(frame: pl.LazyFrame) -> CleanResult:
    """Drop blank rows, split off invalid NDCs, normalize the rest and drop duplicates.

    Duplicates are dropped after normalization, keeping first occurrences.
    """
    frame = empty_to_null(frame).filter(~is_blank_expr())
    valid = ndc_is_valid_expr("NDC")
    clean = frame.filter(valid).with_columns(normalize_ndc_expr("NDC")).unique(maintain_order=True)
    rejects = frame.filter(~valid).with_columns(ndc_invalid_reason_expr("NDC").alias("reason"))
    return CleanResult(clean=clean, rejects=rejects)


def dedupe_ndc_pharm_data(
    input_path: StrPath, output_path: StrPath, rejects_path: StrPath | None = None
) -> CleanStats:
    """Clean `input_path`; write the result to `output_path` and invalid rows to `rejects_path`.

    Both files get a header and are written atomically: a failed run leaves existing files as
    they were. `rejects_path` defaults to `<output stem>.rejects.csv` next to the output.
    Raises `ClaimsFileError` for a malformed input file.
    """
    out = Path(output_path)
    rejects_out = Path(rejects_path) if rejects_path is not None else default_rejects_path(out)
    with staged_outputs(out, rejects_out) as (clean_tmp, rejects_tmp), file_errors(input_path):
        frame = empty_to_null(scan_pharmacy(input_path))
        result = clean_pharmacy(frame)
        result.clean.sink_csv(clean_tmp)
        result.rejects.sink_csv(rejects_tmp)
        blank = is_blank_expr()
        counts = frame.select(
            pl.len().alias("rows_in"),
            blank.sum().alias("blank_rows"),
            (~blank & ~ndc_is_valid_expr("NDC")).sum().alias("rejected"),
        ).collect()
        rows_out = count_rows(clean_tmp)
    return CleanStats(rows_out=rows_out, **counts.row(0, named=True))


def default_rejects_path(output_path: StrPath) -> Path:
    """`out/clean.csv` -> `out/clean.rejects.csv`."""
    out = Path(output_path)
    return out.with_name(f"{out.stem}.rejects.csv")
