import csv
import io
import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Literal

import pytest
from hypothesis import settings

from drug_effects_for_conditions import CleanStats, dedupe_ndc_pharm_data

HEADER = "Patient_ID,Date_Submitted,NDC,Amount_Billed,Amount_Paid"

# Every example runs polars on a real file, so wall-clock deadlines only produce flaky failures
# on slow CI machines. CI also gets a fixed example sequence, so a red run reproduces exactly.
settings.register_profile("dev", deadline=None)
settings.register_profile("ci", deadline=None, derandomize=True, database=None, print_blob=True)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci" if os.environ.get("CI") else "dev"))


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
type MakeLink = Callable[[Literal["hard", "sym"], Path, Path], None]
type CleanFailure = Callable[[int], str]


@pytest.fixture
def assert_clean_failure(capsys: pytest.CaptureFixture[str]) -> CleanFailure:
    """Check the #9 contract for a failed CLI run and return the error line.

    Exit code 1 alone proves little: an uncaught exception also exits 1. The run must print
    exactly one line, `error: <message>`, on stderr, nothing on stdout, and no traceback.
    """

    def _check(code: int) -> str:
        captured = capsys.readouterr()
        assert code == 1
        assert captured.out == ""
        assert "Traceback" not in captured.err
        lines = captured.err.splitlines()
        assert len(lines) == 1, f"expected one stderr line, got {captured.err!r}"
        assert captured.err.endswith("\n")
        assert lines[0].startswith("error: ")
        assert lines[0].removeprefix("error: ").strip(), "empty error message"
        return lines[0]

    return _check


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


@pytest.fixture
def make_link() -> MakeLink:
    """Create a hard or symbolic link, skipping the test where the platform refuses."""

    def _link(kind: Literal["hard", "sym"], target: Path, link: Path) -> None:
        try:
            if kind == "hard":
                os.link(target, link)
            else:
                link.symlink_to(target)
        except (OSError, NotImplementedError) as exc:  # Windows without privilege, FAT, ...
            pytest.skip(f"cannot create a {kind} link here: {exc}")

    return _link


def _write_denied(directory: Path) -> bool:
    probe = directory / ".probe"
    try:
        probe.touch()
    except PermissionError:
        return True
    probe.unlink()
    return False


@pytest.fixture
def read_only_dir(tmp_path: Path) -> Iterator[Path]:
    """A directory the test process cannot create files in, or skip.

    chmod is not enough on its own: root, Windows and some CI file systems ignore it, so the
    fixture checks that a write really fails before handing the directory over.
    """
    if sys.platform == "win32":
        pytest.skip("POSIX permission bits are not enforced on Windows")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        if not _write_denied(locked):
            pytest.skip("directory permissions are not enforced for this user")
        yield locked
    finally:
        locked.chmod(0o700)


@pytest.fixture
def unreadable_file(tmp_path: Path) -> Iterator[Path]:
    """A valid pharmacy CSV the test process cannot read, or skip."""
    if sys.platform == "win32":
        pytest.skip("POSIX permission bits are not enforced on Windows")
    path = tmp_path / "unreadable.csv"
    path.write_text(f"{HEADER}\n1,d,5,1,2\n")
    path.chmod(0o000)
    try:
        try:
            path.read_bytes()
        except PermissionError:
            yield path
        else:
            pytest.skip("file permissions are not enforced for this user")
    finally:
        path.chmod(0o600)
