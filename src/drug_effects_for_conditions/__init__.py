"""Clean pharmacy claims data."""

from importlib.metadata import PackageNotFoundError, version

from drug_effects_for_conditions.ndc import (
    NDC_LENGTH,
    is_valid_ndc,
    ndc_is_valid_expr,
    normalize_ndc,
    normalize_ndc_expr,
)
from drug_effects_for_conditions.pharmacy import (
    PHARMACY_COLUMNS,
    CleanResult,
    CleanStats,
    PharmacySchemaError,
    StrPath,
    clean_pharmacy,
    dedupe_ndc_pharm_data,
    scan_pharmacy,
)

# Running from a source tree without an install (PYTHONPATH=src) has no package metadata.
try:
    __version__ = version("drug-effects-for-conditions")
except PackageNotFoundError:
    __version__ = "0+unknown"

__all__ = [
    "NDC_LENGTH",
    "PHARMACY_COLUMNS",
    "CleanResult",
    "CleanStats",
    "PharmacySchemaError",
    "StrPath",
    "__version__",
    "clean_pharmacy",
    "dedupe_ndc_pharm_data",
    "is_valid_ndc",
    "ndc_is_valid_expr",
    "normalize_ndc",
    "normalize_ndc_expr",
    "scan_pharmacy",
]
