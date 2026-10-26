import csv
import io
from collections.abc import Callable
from pathlib import Path

import pytest

from drug_effects_for_conditions import CleanStats, dedupe_ndc_pharm_data

HEADER = "Patient_ID,Date_Submitted,NDC,Amount_Billed,Amount_Paid"


@pytest.fixture
def write_csv(tmp_path: Path) -> Callable[..., Path]:
    """Write a small CSV (header plus given rows) and return its path."""

    def _write(rows: list[str], *, header: str | None = HEADER, name: str = "in.csv") -> Path:
        lines = ([header] if header is not None else []) + rows
        path = tmp_path / name
        path.write_text("".join(f"{line}\n" for line in lines))
        return path

    return _write


type RunDedupe = Callable[[bytes], tuple[CleanStats, bytes]]
type ParseCsv = Callable[[bytes], list[list[str]]]


@pytest.fixture
def run_dedupe(tmp_path: Path) -> RunDedupe:
    """Write raw bytes as the input file, run the library pipeline, return stats and output."""

    def _run(data: bytes) -> tuple[CleanStats, bytes]:
        src = tmp_path / "raw_in.csv"
        out = tmp_path / "raw_out.csv"
        src.write_bytes(data)
        stats = dedupe_ndc_pharm_data(src, out)
        return stats, out.read_bytes()

    return _run


@pytest.fixture
def parse_csv() -> ParseCsv:
    """Parse CSV bytes into rows of strings (header included) with the stdlib reader."""

    def _parse(data: bytes) -> list[list[str]]:
        return list(csv.reader(io.StringIO(data.decode("utf-8"), newline="")))

    return _parse
