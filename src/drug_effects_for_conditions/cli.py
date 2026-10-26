"""Command-line interface for drug-effects-for-conditions."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from drug_effects_for_conditions import __version__

# Lazy so `--help` and `--version` don't pay for importing polars.
lazy from drug_effects_for_conditions.pharmacy import PharmacySchemaError, dedupe_ndc_pharm_data

PROG = "drug-effects-for-conditions"


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
        description="Normalize NDCs to 11 digits and drop exact-duplicate pharmacy claim rows.",
    )
    dedupe.add_argument("input", metavar="INPUT", type=Path, help="pharmacy claims CSV to read")
    dedupe.add_argument("output", metavar="OUTPUT", type=Path, help="CSV file to write")
    return parser


def _error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def _dedupe_pharmacy(input_path: Path, output_path: Path) -> int:
    if not input_path.is_file():
        return _error(f"input file not found: {input_path}")
    # resolve() catches a not-yet-existing output; samefile() catches hard links.
    if input_path.resolve() == output_path.resolve() or (
        output_path.exists() and input_path.samefile(output_path)
    ):
        return _error("OUTPUT must be a different file from INPUT")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        stats = dedupe_ndc_pharm_data(input_path, output_path)
    except FileNotFoundError:
        return _error(f"input file not found: {input_path}")
    except PharmacySchemaError as exc:
        return _error(str(exc))
    print(
        f"read {stats.rows_in} rows, wrote {stats.rows_out} rows "
        f"({stats.duplicates_removed} duplicates removed) to {output_path}",
        file=sys.stderr,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the process exit code."""
    args = build_parser().parse_args(argv)
    match args.command:
        case "dedupe-pharmacy":
            return _dedupe_pharmacy(args.input, args.output)
        case _:  # pragma: no cover - argparse rejects unknown commands
            raise AssertionError(args.command)
