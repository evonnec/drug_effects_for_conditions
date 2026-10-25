from collections.abc import Callable
from pathlib import Path

import pytest

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
