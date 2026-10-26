"""Clean pharmacy and medical claims data."""

from importlib.metadata import PackageNotFoundError, version

from drug_effects_for_conditions.claims import (
    ClaimsSchemaError,
    CleanStats,
    StrPath,
    scan_claims,
)
from drug_effects_for_conditions.medical import (
    DIAGNOSIS_COLUMNS,
    MEDICAL_COLUMNS,
    MedicalSchemaError,
    clean_medical,
    dedupe_medical_data,
    scan_medical,
)
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
    PharmacySchemaError,
    clean_pharmacy,
    dedupe_ndc_pharm_data,
    scan_pharmacy,
)
from drug_effects_for_conditions.reference import (
    ReferenceFileError,
    load_diagnosis_codes,
    load_ndc_list,
    match_ndc,
)

# Running from a source tree without an install (PYTHONPATH=src) has no package metadata.
try:
    __version__ = version("drug-effects-for-conditions")
except PackageNotFoundError:
    __version__ = "0+unknown"

__all__ = [
    "DIAGNOSIS_COLUMNS",
    "MEDICAL_COLUMNS",
    "NDC_LENGTH",
    "PHARMACY_COLUMNS",
    "ClaimsSchemaError",
    "CleanResult",
    "CleanStats",
    "MedicalSchemaError",
    "PharmacySchemaError",
    "ReferenceFileError",
    "StrPath",
    "__version__",
    "clean_medical",
    "clean_pharmacy",
    "dedupe_medical_data",
    "dedupe_ndc_pharm_data",
    "is_valid_ndc",
    "load_diagnosis_codes",
    "load_ndc_list",
    "match_ndc",
    "ndc_is_valid_expr",
    "normalize_ndc",
    "normalize_ndc_expr",
    "scan_claims",
    "scan_medical",
    "scan_pharmacy",
]
