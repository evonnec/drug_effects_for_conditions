"""Fault injection and wiring tests for the `dedupe-pharmacy` CLI."""

import contextlib
import errno
import os
from pathlib import Path
from typing import Any
from unittest import mock

import polars as pl
import pytest
from polars.exceptions import ComputeError

from drug_effects_for_conditions import CleanStats, cli

HEADER = "Patient_ID,Date_Submitted,NDC,Amount_Billed,Amount_Paid\n"
ROW = "1,2020-01-01,0002-3227-30,10.00,5.00\n"
XFAIL_9 = pytest.mark.xfail(strict=True, reason="#9: CLI prints a traceback instead of an error")
XFAIL_9_ATOMIC = pytest.mark.xfail(strict=True, reason="#9: output is not written atomically")


@pytest.fixture
def src(tmp_path: Path) -> Path:
    path = tmp_path / "in.csv"
    path.write_text(HEADER + ROW + ROW)
    return path


def run_cli(src: Path, out: Path) -> int:
    return cli.main(["dedupe-pharmacy", str(src), str(out)])


def assert_clean_failure(code: int, capsys: pytest.CaptureFixture[str]) -> None:
    captured = capsys.readouterr()
    assert code == 1
    assert captured.err.startswith("error: ")
    assert "Traceback" not in captured.err
    assert captured.out == ""


# --- wiring -----------------------------------------------------------------------------------


def test_cli_passes_paths_to_library_and_reports_on_stderr(
    src: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out.csv"
    fake = mock.Mock(return_value=CleanStats(rows_in=5, rows_out=3))
    monkeypatch.setattr(cli, "dedupe_ndc_pharm_data", fake)
    assert run_cli(src, out) == 0
    fake.assert_called_once_with(src, out)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"read 5 rows, wrote 3 rows (2 duplicates removed) to {out}\n"


def test_cli_does_not_call_library_when_input_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = mock.Mock()
    monkeypatch.setattr(cli, "dedupe_ndc_pharm_data", fake)
    assert run_cli(tmp_path / "missing.csv", tmp_path / "out.csv") == 1
    fake.assert_not_called()
    assert_clean_failure(1, capsys)


def test_cli_creates_missing_output_parents(src: Path, tmp_path: Path) -> None:
    out = tmp_path / "a" / "b" / "out.csv"
    assert run_cli(src, out) == 0
    assert out.is_file()


# --- injected library failures ------------------------------------------------------------------


def _sink_raising(exc: BaseException) -> Any:
    def fake(self: pl.LazyFrame, *args: Any, **kwargs: Any) -> None:
        raise exc

    return fake


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
    src: Path,
    tmp_path: Path,
    exc: BaseException,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pl.LazyFrame, "sink_csv", _sink_raising(exc))
    assert_clean_failure(run_cli(src, tmp_path / "out.csv"), capsys)


@XFAIL_9
def test_scan_csv_failure_is_reported_cleanly(
    src: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pl, "scan_csv", mock.Mock(side_effect=ComputeError("boom")))
    assert_clean_failure(run_cli(src, tmp_path / "out.csv"), capsys)


@XFAIL_9
def test_unreadable_input_is_reported_cleanly(
    src: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pl, "scan_csv", mock.Mock(side_effect=PermissionError(errno.EACCES, "no")))
    assert_clean_failure(run_cli(src, tmp_path / "out.csv"), capsys)


@XFAIL_9
def test_stats_rescan_failure_is_reported_cleanly(
    src: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out.csv"
    real_scan = pl.scan_csv

    def fail_on_output(source: Any, *args: Any, **kwargs: Any) -> pl.LazyFrame:
        if Path(source) == out:
            raise ComputeError("cannot re-read output")
        return real_scan(source, *args, **kwargs)

    monkeypatch.setattr(pl, "scan_csv", fail_on_output)
    assert_clean_failure(run_cli(src, out), capsys)


# --- bad OUTPUT locations ----------------------------------------------------------------------


@XFAIL_9
def test_directory_as_output(src: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "outdir"
    out.mkdir()
    assert_clean_failure(run_cli(src, out), capsys)


@XFAIL_9
def test_file_as_output_parent(
    src: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    assert_clean_failure(run_cli(src, blocker / "out.csv"), capsys)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
@XFAIL_9
def test_read_only_output_directory(
    src: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        code = run_cli(src, locked / "out.csv")
    finally:
        locked.chmod(0o700)
    assert_clean_failure(code, capsys)


def test_symlink_output_pointing_at_input_is_refused(
    src: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = src.read_bytes()
    link = tmp_path / "link.csv"
    link.symlink_to(src)
    assert_clean_failure(run_cli(src, link), capsys)
    assert src.read_bytes() == before


# --- failed runs must not destroy an existing OUTPUT ---------------------------------------------


@XFAIL_9_ATOMIC
def test_existing_output_survives_failed_sink(
    src: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out.csv"
    out.write_text("precious\n")

    def partial_write(self: pl.LazyFrame, path: Path, *args: Any, **kwargs: Any) -> None:
        Path(path).write_text("half a fi")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(pl.LazyFrame, "sink_csv", partial_write)
    with contextlib.suppress(OSError):
        run_cli(src, out)
    assert out.read_text() == "precious\n"


@XFAIL_9_ATOMIC
def test_existing_output_survives_malformed_input(src: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    out.write_text("precious\n")
    src.write_text(HEADER + "1,2020-01-01,5,1.00,2.00,extra\n")
    with contextlib.suppress(ComputeError):
        run_cli(src, out)
    assert out.read_text() == "precious\n"


@XFAIL_9_ATOMIC
def test_failed_run_leaves_no_partial_output(
    src: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out.csv"

    def partial_write(self: pl.LazyFrame, path: Path, *args: Any, **kwargs: Any) -> None:
        Path(path).write_text("half a fi")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(pl.LazyFrame, "sink_csv", partial_write)
    with contextlib.suppress(OSError):
        run_cli(src, out)
    assert not out.exists()
