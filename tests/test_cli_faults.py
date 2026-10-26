"""Fault injection and wiring tests for the `dedupe-pharmacy` CLI."""

import contextlib
import errno
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from unittest import mock

import polars as pl
import pytest
from polars.exceptions import ComputeError, PolarsError

from drug_effects_for_conditions import CleanStats, cli, dedupe_ndc_pharm_data

type CleanFailure = Callable[[int], str]
type MakeLink = Callable[[Literal["hard", "sym"], Path, Path], None]

HEADER = "Patient_ID,Date_Submitted,NDC,Amount_Billed,Amount_Paid\n"
ROW = "1,2020-01-01,0002-3227-30,10.00,5.00\n"
# `raises=` pins how each case fails today (the exception escapes `main`), so a broken fixture or
# a half-finished fix that changes the failure shows up as a real failure, not a quiet xfail.
XFAIL_9 = pytest.mark.xfail(
    strict=True,
    raises=(OSError, PolarsError),
    reason="#9: CLI prints a traceback instead of an error",
)
XFAIL_9_ATOMIC = pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="#9: output is not written atomically"
)


@pytest.fixture
def src(tmp_path: Path) -> Path:
    path = tmp_path / "in.csv"
    path.write_text(HEADER + ROW + ROW)
    return path


def run_cli(src: Path, out: Path) -> int:
    return cli.main(["dedupe-pharmacy", str(src), str(out)])


def files_in(directory: Path) -> list[str]:
    """Names in `directory`; a temp file left behind by a failed run shows up here."""
    return sorted(p.name for p in directory.iterdir())


# --- wiring -----------------------------------------------------------------------------------


def test_cli_passes_paths_to_library_and_reports_on_stderr(
    src: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Patch the name where cli.py looks it up (its own lazy-imported global), not in pharmacy.
    out = tmp_path / "out.csv"
    fake = mock.Mock(return_value=CleanStats(rows_in=7, rows_out=3, blank_rows=1, rejected=1))
    monkeypatch.setattr(cli, "dedupe_ndc_pharm_data", fake)
    assert run_cli(src, out) == 0
    fake.assert_called_once_with(src, out, tmp_path / "out.rejects.csv")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        f"read 7 rows, wrote 3 rows (2 duplicates removed, 1 blank, 1 rejected) to {out}\n"
        f"wrote 1 rejected rows to {tmp_path / 'out.rejects.csv'}\n"
    )


def test_cli_does_not_call_library_when_input_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, assert_clean_failure: CleanFailure
) -> None:
    fake = mock.Mock()
    monkeypatch.setattr(cli, "dedupe_ndc_pharm_data", fake)
    missing = tmp_path / "missing.csv"
    line = assert_clean_failure(run_cli(missing, tmp_path / "out.csv"))
    fake.assert_not_called()
    assert line == f"error: input file not found: {missing}"


def test_input_deleted_after_the_check_is_reported_as_missing(
    src: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, assert_clean_failure: CleanFailure
) -> None:
    # The CLI checks INPUT before reading it; if the file disappears in between, the library's
    # FileNotFoundError must still become a clean error.
    def delete_then_run(input_path: Path, output_path: Path, rejects_path: Path) -> CleanStats:
        input_path.unlink()
        return dedupe_ndc_pharm_data(input_path, output_path, rejects_path)

    spy = mock.Mock(side_effect=delete_then_run)
    monkeypatch.setattr(cli, "dedupe_ndc_pharm_data", spy)
    line = assert_clean_failure(run_cli(src, tmp_path / "out.csv"))
    spy.assert_called_once()
    assert line == f"error: input file not found: {src}"


def test_cli_creates_missing_output_parents(src: Path, tmp_path: Path) -> None:
    out = tmp_path / "a" / "b" / "out.csv"
    assert run_cli(src, out) == 0
    assert out.is_file()


# --- injected library failures ------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        PermissionError(errno.EACCES, "Permission denied"),
        OSError(errno.ENOSPC, "No space left on device"),
        ComputeError("disk write failed"),
    ],
    ids=["permission", "enospc", "compute"],
)
@XFAIL_9
def test_sink_csv_failure_is_reported_cleanly(
    src: Path, tmp_path: Path, exc: BaseException, assert_clean_failure: CleanFailure
) -> None:
    with mock.patch.object(pl.LazyFrame, "sink_csv", autospec=True, side_effect=exc) as sink:
        code = run_cli(src, tmp_path / "out.csv")
    sink.assert_called_once()
    assert_clean_failure(code)
    assert files_in(tmp_path) == ["in.csv"]


@XFAIL_9
def test_scan_csv_failure_is_reported_cleanly(
    src: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, assert_clean_failure: CleanFailure
) -> None:
    scan = mock.Mock(side_effect=ComputeError("boom"))
    monkeypatch.setattr(pl, "scan_csv", scan)
    code = run_cli(src, tmp_path / "out.csv")
    scan.assert_called()
    assert_clean_failure(code)


@XFAIL_9
def test_scan_permission_error_is_reported_cleanly(
    src: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, assert_clean_failure: CleanFailure
) -> None:
    scan = mock.Mock(side_effect=PermissionError(errno.EACCES, "no"))
    monkeypatch.setattr(pl, "scan_csv", scan)
    code = run_cli(src, tmp_path / "out.csv")
    scan.assert_called()
    assert_clean_failure(code)


@XFAIL_9
def test_unreadable_input_file_is_reported_cleanly(
    unreadable_file: Path, tmp_path: Path, assert_clean_failure: CleanFailure
) -> None:
    assert_clean_failure(run_cli(unreadable_file, tmp_path / "out.csv"))


@XFAIL_9
def test_stats_rescan_failure_is_reported_cleanly(
    src: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, assert_clean_failure: CleanFailure
) -> None:
    # Fail every scan that is not of INPUT. Today that is the re-read of OUTPUT that counts rows;
    # an atomic-write fix may re-read a temp file instead, and must handle that failure the same
    # way. Matching only `== out` would never fire for such a fix and block it.
    real_scan = pl.scan_csv
    injected: list[object] = []

    def fail_unless_input(source: Any, *args: Any, **kwargs: Any) -> pl.LazyFrame:
        if not isinstance(source, str | os.PathLike) or Path(source).resolve() != src.resolve():
            injected.append(source)
            raise ComputeError("cannot re-read output")
        return real_scan(Path(source), *args, **kwargs)

    monkeypatch.setattr(pl, "scan_csv", fail_unless_input)
    code = run_cli(src, tmp_path / "out.csv")
    if not injected:
        pytest.skip("the pipeline no longer re-reads anything but INPUT")
    assert_clean_failure(code)


# --- bad OUTPUT locations ----------------------------------------------------------------------


@XFAIL_9
def test_directory_as_output(src: Path, tmp_path: Path, assert_clean_failure: CleanFailure) -> None:
    out = tmp_path / "outdir"
    out.mkdir()
    assert_clean_failure(run_cli(src, out))
    assert out.is_dir()
    assert files_in(out) == []


@XFAIL_9
def test_file_as_output_parent(
    src: Path, tmp_path: Path, assert_clean_failure: CleanFailure
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    assert_clean_failure(run_cli(src, blocker / "out.csv"))
    assert blocker.read_text() == "x"


@XFAIL_9
def test_read_only_output_directory(
    src: Path, read_only_dir: Path, assert_clean_failure: CleanFailure
) -> None:
    assert_clean_failure(run_cli(src, read_only_dir / "out.csv"))
    assert files_in(read_only_dir) == []


def test_symlink_output_pointing_at_input_is_refused(
    src: Path, tmp_path: Path, make_link: MakeLink, assert_clean_failure: CleanFailure
) -> None:
    before = src.read_bytes()
    link = tmp_path / "link.csv"
    make_link("sym", src, link)
    assert_clean_failure(run_cli(src, link))
    assert src.read_bytes() == before


# --- failed runs must not destroy an existing OUTPUT ---------------------------------------------


def _write_partial(target: Any) -> None:
    if isinstance(target, str | os.PathLike):
        Path(target).write_bytes(b"half a fi")
        return
    try:  # an open file object, binary or text
        target.write(b"half a fi")
    except TypeError:
        target.write("half a fi")


@pytest.fixture
def partial_sink(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Make `sink_csv` write half a file and then fail; return the targets it was given."""
    targets: list[object] = []

    def fake(self: pl.LazyFrame, path: object, *args: Any, **kwargs: Any) -> None:
        targets.append(path)
        _write_partial(path)
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(pl.LazyFrame, "sink_csv", fake)
    return targets


@XFAIL_9_ATOMIC
def test_existing_output_survives_failed_sink(
    src: Path, tmp_path: Path, partial_sink: list[object]
) -> None:
    out = tmp_path / "out.csv"
    out.write_bytes(b"precious\n")
    with contextlib.suppress(OSError):
        run_cli(src, out)
    assert partial_sink, "sink_csv was never called, so nothing was tested"
    assert out.read_bytes() == b"precious\n"
    assert files_in(tmp_path) == ["in.csv", "out.csv"]


def test_existing_output_survives_malformed_input(src: Path, tmp_path: Path) -> None:
    # Passes for any fix that leaves OUTPUT alone, including one that validates before writing.
    # Only the failed-sink tests need a real temp-file-and-replace.
    out = tmp_path / "out.csv"
    out.write_bytes(b"precious\n")
    src.write_text(HEADER + ROW * 3 + "1,2020-01-01,5,1.00,2.00,extra\n")
    with contextlib.suppress(PolarsError):
        run_cli(src, out)
    assert out.read_bytes() == b"precious\n"
    assert files_in(tmp_path) == ["in.csv", "out.csv"]


@XFAIL_9_ATOMIC
def test_failed_run_leaves_no_partial_output(
    src: Path, tmp_path: Path, partial_sink: list[object]
) -> None:
    out = tmp_path / "out.csv"
    with contextlib.suppress(OSError):
        run_cli(src, out)
    assert partial_sink, "sink_csv was never called, so nothing was tested"
    assert files_in(tmp_path) == ["in.csv"]


# --- a real process -----------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True, raises=AssertionError, reason="#9: CLI prints a traceback instead of an error"
)
def test_real_process_prints_one_error_line_and_no_traceback(tmp_path: Path) -> None:
    src = tmp_path / "empty.csv"
    src.write_bytes(b"")
    argv = ["dedupe-pharmacy", str(src), str(tmp_path / "out.csv")]
    result = subprocess.run(
        [sys.executable, "-m", "drug_effects_for_conditions", *argv],
        capture_output=True,
        text=True,
        check=False,
    )
    # An uncaught exception also exits with 1, so the exit code alone proves nothing here.
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("error: ")
    assert result.stderr.count("\n") == 1


# --- defects not tracked by an issue yet -------------------------------------------------------


@pytest.mark.parametrize("kind", ["hard", "sym"])
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="#20: library truncates INPUT when OUTPUT is a link to it",
)
def test_library_never_truncates_input_through_a_link(
    tmp_path: Path, kind: Literal["hard", "sym"], make_link: MakeLink
) -> None:
    # polars refuses an identical path, but not another name for the same file. The CLI checks
    # this; the public library function does not, and destroys the input before it fails.
    src = tmp_path / "in.csv"
    src.write_text(HEADER + ROW + ROW)
    before = src.read_bytes()
    link = tmp_path / "link.csv"
    make_link(kind, src, link)
    with contextlib.suppress(Exception):  # refusing is fine, destroying the input is not
        dedupe_ndc_pharm_data(src, link)
    assert src.read_bytes() == before


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="#21: OUTPUT-side FileNotFoundError is reported as a missing INPUT",
)
def test_output_side_file_not_found_does_not_blame_the_input(
    src: Path, tmp_path: Path, make_link: MakeLink, capsys: pytest.CaptureFixture[str]
) -> None:
    # OUTPUT is a dangling symlink into a directory that does not exist. Whether the run then
    # fails or replaces the link, it must not claim that INPUT (which exists) is missing.
    out = tmp_path / "out.csv"
    make_link("sym", tmp_path / "nowhere" / "out.csv", out)
    run_cli(src, out)
    assert "input file not found" not in capsys.readouterr().err


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="#21: a failed run still creates OUTPUT's parent directories",
)
def test_failed_run_creates_no_directories(
    tmp_path: Path, assert_clean_failure: CleanFailure
) -> None:
    src = tmp_path / "in.csv"
    src.write_text("a,b\n1,2\n")  # rejected by the header check, before anything is written
    assert_clean_failure(run_cli(src, tmp_path / "new" / "dir" / "out.csv"))
    assert files_in(tmp_path) == ["in.csv"]
