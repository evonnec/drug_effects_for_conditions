"""Load the headerless NDC and diagnosis-code reference files."""

from pathlib import Path
from typing import Final

lazy import polars as pl

from drug_effects_for_conditions.claims import StrPath

__all__ = [
    "ReferenceFileError",
    "load_diagnosis_codes",
    "load_ndc_list",
    "match_ndc",
]

NDC_PATTERN: Final = r"^[0-9]{11}$"
DIAGNOSIS_PATTERN: Final = r"^[0-9A-Z]{3,7}$"
SAMPLE_SIZE: Final = 5


class ReferenceFileError(ValueError):
    """A reference file is empty, has missing values or has a malformed code."""


def _load(path: StrPath, column: str, pattern: str, what: str) -> pl.LazyFrame:
    ref = Path(path)
    if not ref.is_file():
        raise FileNotFoundError(f"No such file: {ref}")
    # These files are tiny, so validation collects them; the result is a lazy frame again.
    try:
        raw = pl.scan_csv(ref, has_header=False, schema={column: pl.String}).collect()
    except pl.exceptions.NoDataError:
        raw = pl.DataFrame(schema={column: pl.String})
    values = raw.get_column(column).str.strip_chars()
    if values.len() == 0:
        raise ReferenceFileError(f"{ref.name}: file is empty")
    missing = values.is_null() | (values == "")
    if missing.any():
        raise ReferenceFileError(f"{ref.name}: {missing.sum()} missing values (blank lines)")
    bad = values.filter(~values.str.contains(pattern)).unique(maintain_order=True)
    if bad.len():
        sample = ", ".join(repr(v) for v in bad.head(SAMPLE_SIZE))
        raise ReferenceFileError(
            f"{ref.name}: {bad.len()} invalid {what} (expected {pattern}), for example {sample}"
        )
    return values.to_frame().lazy().unique(maintain_order=True)


def load_ndc_list(path: StrPath) -> pl.LazyFrame:
    """Distinct 11-digit NDCs from a headerless file, as a single `NDC` column."""
    return _load(path, "NDC", NDC_PATTERN, "NDCs")


def load_diagnosis_codes(path: StrPath) -> pl.LazyFrame:
    """Distinct stripped diagnosis codes from a headerless file, as `Diagnosis_Code`."""
    return _load(path, "Diagnosis_Code", DIAGNOSIS_PATTERN, "diagnosis codes")


def match_ndc(pharmacy: pl.LazyFrame, ndc_list: pl.LazyFrame) -> pl.LazyFrame:
    """The pharmacy rows whose `NDC` is in `ndc_list`."""
    return pharmacy.join(ndc_list, on="NDC", how="semi")
