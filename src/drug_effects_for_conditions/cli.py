"""Command-line interface for drug-effects-for-conditions."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

# Lazy so `--help` and `--version` don't pay for importing polars.
lazy from polars.exceptions import InvalidOperationError

from drug_effects_for_conditions import __version__
lazy from drug_effects_for_conditions.claims import ClaimsSchemaError, CleanStats
lazy from drug_effects_for_conditions.cohort import CohortSummary, developed_after, summarize
lazy from drug_effects_for_conditions.medical import (
    clean_medical,
    dedupe_medical_data,
    scan_medical,
)
lazy from drug_effects_for_conditions.pharmacy import (
    clean_pharmacy,
    dedupe_ndc_pharm_data,
    default_rejects_path,
    scan_pharmacy,
)
lazy from drug_effects_for_conditions.reference import (
    ReferenceFileError,
    load_diagnosis_codes,
    load_ndc_list,
)

PROG = "drug-effects-for-conditions"
EXIT_REJECTED = 3  # --strict and at least one row rejected; 2 is argparse's usage error


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(prog=PROG, description=__doc__)
    parser.add_argument(
        "--version",
        action="version",
        version=f"{PROG} {__version__}",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    dedupe = sub.add_parser(
        "dedupe-pharmacy",
        help="normalize NDCs and de-duplicate a pharmacy claims CSV",
        description=(
            "Normalize NDCs to 11 digits and drop exact-duplicate pharmacy claim rows. "
            "Rows with an invalid NDC are written to a rejects file instead."
        ),
    )
    dedupe.add_argument("input", metavar="INPUT", type=Path, help="pharmacy claims CSV to read")
    dedupe.add_argument("output", metavar="OUTPUT", type=Path, help="CSV file to write")
    dedupe.add_argument(
        "--rejects",
        metavar="PATH",
        type=Path,
        help="CSV file for rows with an invalid NDC (default: OUTPUT stem + .rejects.csv)",
    )
    dedupe.add_argument(
        "--strict",
        action="store_true",
        help=f"exit with code {EXIT_REJECTED} if any row was rejected",
    )

    medical = sub.add_parser(
        "dedupe-medical",
        help="de-duplicate a medical claims CSV",
        description=(
            "Strip the padding from diagnosis codes and drop exact-duplicate medical claim rows. "
            "Rows that differ in an amount are separate claim lines and are kept."
        ),
    )
    medical.add_argument("input", metavar="INPUT", type=Path, help="medical claims CSV to read")
    medical.add_argument("output", metavar="OUTPUT", type=Path, help="CSV file to write")

    cohort = sub.add_parser(
        "cohort",
        help="find patients who developed condition Y after taking drug X",
        description=(
            "Clean both claims files in memory, then write one row per patient whose first drug X "
            "fill was followed by a condition Y claim. Counts go to stderr."
        ),
    )
    cohort.add_argument("--pharmacy", metavar="P", required=True, type=Path, help="pharmacy CSV")
    cohort.add_argument("--medical", metavar="M", required=True, type=Path, help="medical CSV")
    cohort.add_argument("--ndc", metavar="N", required=True, type=Path, help="drug X NDC list")
    cohort.add_argument(
        "--codes", metavar="C", required=True, type=Path, help="condition Y diagnosis code list"
    )
    cohort.add_argument(
        "--include-prevalent",
        action="store_true",
        help="also count patients who had a condition Y claim before drug X",
    )
    cohort.add_argument(
        "--same-day",
        action="store_true",
        help="count a condition Y claim on the day of the first drug X fill",
    )
    cohort.add_argument(
        "--washout-days",
        metavar="N",
        type=int,
        default=0,
        help="keep patients whose first fill is at least N days after the first pharmacy date",
    )
    cohort.add_argument("output", metavar="OUTPUT", type=Path, help="CSV file to write")
    return parser


def _error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def _same_file(a: Path, b: Path) -> bool:
    # resolve() catches a not-yet-existing path; samefile() catches hard links.
    return a.resolve() == b.resolve() or (a.exists() and b.exists() and a.samefile(b))


def _check_paths(input_path: Path, output_path: Path) -> str | None:
    """The error message for unusable INPUT/OUTPUT paths, or None."""
    if not input_path.is_file():
        return f"input file not found: {input_path}"
    if _same_file(input_path, output_path):
        return "OUTPUT must be a different file from INPUT"
    return None


def _summary(stats: CleanStats, output_path: Path, *, rejects: bool) -> str:
    detail = f"{stats.duplicates_removed} duplicates removed, {stats.blank_rows} blank"
    if rejects:
        detail += f", {stats.rejected} rejected"
    return f"read {stats.rows_in} rows, wrote {stats.rows_out} rows ({detail}) to {output_path}"


def _dedupe_pharmacy(
    input_path: Path, output_path: Path, rejects_path: Path | None, *, strict: bool
) -> int:
    if message := _check_paths(input_path, output_path):
        return _error(message)
    rejects_path = rejects_path or default_rejects_path(output_path)
    for other in (input_path, output_path):
        if _same_file(rejects_path, other):
            return _error(f"rejects file must be different from {other}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rejects_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        stats = dedupe_ndc_pharm_data(input_path, output_path, rejects_path)
    except FileNotFoundError:
        return _error(f"input file not found: {input_path}")
    except ClaimsSchemaError as exc:
        return _error(str(exc))
    print(_summary(stats, output_path, rejects=True), file=sys.stderr)
    if stats.rejected:
        print(f"wrote {stats.rejected} rejected rows to {rejects_path}", file=sys.stderr)
        if strict:
            return EXIT_REJECTED
    return 0


def _dedupe_medical(input_path: Path, output_path: Path) -> int:
    if message := _check_paths(input_path, output_path):
        return _error(message)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        stats = dedupe_medical_data(input_path, output_path)
    except FileNotFoundError:
        return _error(f"input file not found: {input_path}")
    except ClaimsSchemaError as exc:
        return _error(str(exc))
    print(_summary(stats, output_path, rejects=False), file=sys.stderr)
    return 0


def _cohort_summary(summary: CohortSummary, output_path: Path) -> str:
    lines = [
        ("drug X patients", summary.drug_x),
        ("condition Y patients", summary.condition_y),
        ("both", summary.both),
        ("excluded by washout", summary.washout_excluded),
        ("qualifying", summary.qualifying),
        ("no follow-up", summary.no_followup),
    ]
    width = max(len(label) for label, _ in lines) + 1
    body = "\n".join(f"{label + ':':<{width}} {count:>6}" for label, count in lines)
    return f"{body}\nwrote {summary.qualifying} rows to {output_path}"


def _cohort(args: argparse.Namespace) -> int:
    for path in (args.pharmacy, args.medical, args.ndc, args.codes):
        if message := _check_paths(path, args.output):
            return _error(message)
    if args.washout_days < 0:
        return _error("--washout-days must not be negative")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        pharmacy = clean_pharmacy(scan_pharmacy(args.pharmacy)).clean
        medical = clean_medical(scan_medical(args.medical))
        ndc_list = load_ndc_list(args.ndc)
        codes = load_diagnosis_codes(args.codes)
        incident = not args.include_prevalent
        # Summarizing first parses every date, so a bad date fails before OUTPUT is written.
        summary = summarize(
            pharmacy,
            medical,
            ndc_list,
            codes,
            incident=incident,
            same_day_counts=args.same_day,
            washout_days=args.washout_days,
        )
        developed_after(
            pharmacy,
            medical,
            ndc_list,
            codes,
            incident=incident,
            same_day_counts=args.same_day,
            washout_days=args.washout_days,
        ).sink_csv(args.output)
    except FileNotFoundError as exc:
        return _error(str(exc))
    except (ClaimsSchemaError, ReferenceFileError) as exc:
        return _error(str(exc))
    except InvalidOperationError as exc:
        return _error(f"cannot parse a date as m/d/yyyy: {str(exc).splitlines()[0]}")
    print(_cohort_summary(summary, args.output), file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    args = build_parser().parse_args(argv)
    match args.command:
        case "dedupe-pharmacy":
            return _dedupe_pharmacy(args.input, args.output, args.rejects, strict=args.strict)
        case "dedupe-medical":
            return _dedupe_medical(args.input, args.output)
        case "cohort":
            return _cohort(args)
        case _:  # pragma: no cover - argparse rejects unknown commands
            raise AssertionError(args.command)
