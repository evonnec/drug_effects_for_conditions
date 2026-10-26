"""Hash-partitioned de-duplication (`buckets`, `keep_order`) and stats from a single run."""

import errno
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest import mock

import polars as pl
import pytest
from polars.exceptions import ComputeError

from drug_effects_for_conditions import (
    ClaimsFileError,
    CleanStats,
    cli,
    dedupe_medical_data,
    dedupe_ndc_pharm_data,
)

DATA = Path(__file__).parents[1] / "data"
PHARMACY = DATA / "pharmacy_data_sample.csv"
MEDICAL = DATA / "medical_data_sample.csv"
HEADER = "Patient_ID,Date_Submitted,NDC,Amount_Billed,Amount_Paid\n"

type Dedupe = Callable[..., CleanStats]


def pharmacy(src: Path, out: Path, **kwargs: Any) -> CleanStats:
    return dedupe_ndc_pharm_data(src, out, **kwargs)


def medical(src: Path, out: Path, **kwargs: Any) -> CleanStats:
    return dedupe_medical_data(src, out, **kwargs)


def sorted_lines(path: Path) -> list[str]:
    header, *rows = path.read_text().splitlines()
    return [header, *sorted(rows)]


def names(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


@pytest.fixture
def mixed_order(tmp_path: Path) -> Path:
    """The pharmacy sample twice, the second copy reversed: first occurrences interleave."""
    lines = PHARMACY.read_text().splitlines()[1:]
    path = tmp_path / "mixed.csv"
    path.write_text("\n".join([HEADER.strip(), *lines, *reversed(lines)]) + "\n")
    return path


# --- equivalence with the single pass ---------------------------------------------------------


@pytest.mark.parametrize("buckets", [1, 8, 64])
def test_pharmacy_partitioned_matches_single_pass(tmp_path: Path, buckets: int) -> None:
    single, part = tmp_path / "single.csv", tmp_path / "part.csv"
    expected = pharmacy(PHARMACY, single)
    stats = pharmacy(PHARMACY, part, buckets=buckets)
    assert stats == expected
    assert sorted_lines(part) == sorted_lines(single)
    assert (tmp_path / "part.rejects.csv").read_bytes() == (
        tmp_path / "single.rejects.csv"
    ).read_bytes()

    ordered = tmp_path / "ordered.csv"
    assert pharmacy(PHARMACY, ordered, buckets=buckets, keep_order=True) == expected
    assert ordered.read_bytes() == single.read_bytes()


@pytest.mark.parametrize("buckets", [1, 8, 64])
def test_medical_partitioned_matches_single_pass(tmp_path: Path, buckets: int) -> None:
    single, part = tmp_path / "single.csv", tmp_path / "part.csv"
    expected = medical(MEDICAL, single)
    stats = medical(MEDICAL, part, buckets=buckets)
    assert stats == expected
    assert sorted_lines(part) == sorted_lines(single)

    ordered = tmp_path / "ordered.csv"
    assert medical(MEDICAL, ordered, buckets=buckets, keep_order=True) == expected
    assert ordered.read_bytes() == single.read_bytes()


def test_sample_stats_are_unchanged(tmp_path: Path) -> None:
    for kwargs in ({}, {"buckets": 64}, {"buckets": 7, "keep_order": True}):
        stats = pharmacy(PHARMACY, tmp_path / "p.csv", **kwargs)
        assert stats == CleanStats(rows_in=32932, rows_out=30094, blank_rows=0, rejected=3)
        assert stats.duplicates_removed == 2835
        stats = medical(MEDICAL, tmp_path / "m.csv", **kwargs)
        assert stats == CleanStats(rows_in=30000, rows_out=28285, blank_rows=0)
        assert stats.duplicates_removed == 1715


def test_keep_order_keeps_first_occurrences_when_buckets_hold_interleaved_rows(
    tmp_path: Path, mixed_order: Path
) -> None:
    single, ordered = tmp_path / "single.csv", tmp_path / "ordered.csv"
    expected = pharmacy(mixed_order, single)
    # Few buckets, so each holds many rows from both halves of the file.
    assert pharmacy(mixed_order, ordered, buckets=3, keep_order=True) == expected
    assert ordered.read_bytes() == single.read_bytes()


def test_keep_order_without_buckets_changes_nothing(tmp_path: Path) -> None:
    plain, flagged = tmp_path / "plain.csv", tmp_path / "flagged.csv"
    assert pharmacy(PHARMACY, flagged, keep_order=True) == pharmacy(PHARMACY, plain)
    assert flagged.read_bytes() == plain.read_bytes()
    assert not [n for n in names(tmp_path) if n.endswith(".stage")]


def test_partitioned_run_uses_polars_partition_by(tmp_path: Path) -> None:
    # pl.PartitionBy is unstable, and pyproject pins polars because of it: this fails first if a
    # newer polars changes it.
    with mock.patch.object(pl, "PartitionBy", wraps=pl.PartitionBy) as partition_by:
        pharmacy(PHARMACY, tmp_path / "out.csv", buckets=4)
    partition_by.assert_called_once()
    assert partition_by.call_args.kwargs["key"] == "_bucket"
    assert partition_by.call_args.kwargs["include_key"] is False


@pytest.mark.parametrize("dedupe", [pharmacy, medical], ids=["pharmacy", "medical"])
def test_nothing_survives_the_filters(tmp_path: Path, dedupe: Dedupe) -> None:
    src = tmp_path / "in.csv"
    if dedupe is pharmacy:
        src.write_text(HEADER + "1,d,bad,1,2\n,,,,\n")
    else:
        src.write_text(
            "Patient_ID,Diagnosis_Code_1,Diagnosis_Code_2,Diagnosis_Code_3,Diagnosis_Code_4,"
            "Service_Date,Amount_Paid,Amount_Billed\n,,,,,,,\n"
        )
    single, part = tmp_path / "single.csv", tmp_path / "part.csv"
    expected = dedupe(src, single)
    assert dedupe(src, part, buckets=4) == expected
    assert part.read_bytes() == single.read_bytes()
    assert expected.rows_out == 0
    assert not [n for n in names(tmp_path) if n.endswith(".stage")]


# --- scratch directory and failures -----------------------------------------------------------


def test_stage_directory_is_removed_after_success(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    pharmacy(PHARMACY, out_dir / "o.csv", buckets=8)
    assert names(out_dir) == ["o.csv", "o.rejects.csv"]


def test_stage_directory_is_beside_output_while_running(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    seen: list[str] = []
    real = pl.LazyFrame.sink_parquet

    def spy(self: pl.LazyFrame, path: Any, *args: Any, **kwargs: Any) -> Any:
        if not seen:
            seen.extend(n for n in names(out_dir) if n.endswith(".stage"))
        return real(self, path, *args, **kwargs)

    with mock.patch.object(pl.LazyFrame, "sink_parquet", spy):
        medical(MEDICAL, out_dir / "o.csv", buckets=4)
    # Named after the output's temp file, so it is hidden and unique to this process.
    assert len(seen) == 1
    assert seen[0].startswith(".") and seen[0].endswith(f".{os.getpid()}.stage")
    assert names(out_dir) == ["o.csv"]


@pytest.mark.parametrize("dedupe", [pharmacy, medical], ids=["pharmacy", "medical"])
@pytest.mark.parametrize("fail_on", ["stage", "bucket"])
def test_failed_partitioned_run_leaves_output_and_directory_untouched(
    tmp_path: Path, dedupe: Dedupe, fail_on: str
) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    out = out_dir / "o.csv"
    out.write_bytes(b"precious\n")
    rejects = out_dir / "o.rejects.csv"
    rejects.write_bytes(b"also precious\n")
    src = PHARMACY if dedupe is pharmacy else MEDICAL
    real = pl.LazyFrame.sink_parquet
    calls = 0

    def failing(self: pl.LazyFrame, path: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        is_bucket_sink = isinstance(path, Path) and path.suffix == ".parquet"
        if (fail_on == "stage" and not is_bucket_sink) or (
            fail_on == "bucket" and is_bucket_sink and calls > 2
        ):
            # Mid-way: the stage directory exists and holds data.
            assert any(n.endswith(".stage") for n in names(out_dir)) or fail_on == "stage"
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, path, *args, **kwargs)

    with (
        mock.patch.object(pl.LazyFrame, "sink_parquet", failing),
        pytest.raises(OSError, match="No space left"),
    ):
        dedupe(src, out, buckets=8)
    assert out.read_bytes() == b"precious\n"
    assert rejects.read_bytes() == b"also precious\n"
    assert names(out_dir) == ["o.csv", "o.rejects.csv"]


def test_polars_error_in_the_partitioned_path_becomes_claims_file_error(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    boom = mock.Mock(side_effect=ComputeError("bucket exploded\nsecond line"))
    with (
        mock.patch.object(pl.LazyFrame, "sink_parquet", boom),
        pytest.raises(ClaimsFileError, match="bucket exploded") as excinfo,
    ):
        medical(MEDICAL, out_dir / "o.csv", buckets=8)
    assert "second line" not in str(excinfo.value)
    assert names(out_dir) == []


def test_failure_while_writing_the_csv_removes_stage_and_temp(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    real = pl.LazyFrame.sink_csv

    def failing(self: pl.LazyFrame, path: Any, *args: Any, **kwargs: Any) -> Any:
        # The final write of the partitioned path is the only non-lazy sink_csv.
        if not kwargs.get("lazy"):
            Path(path).write_bytes(b"half a fi")
            raise OSError(errno.EIO, "I/O error")
        return real(self, path, *args, **kwargs)

    with mock.patch.object(pl.LazyFrame, "sink_csv", failing), pytest.raises(OSError):
        pharmacy(PHARMACY, out_dir / "o.csv", buckets=8)
    assert names(out_dir) == []


# --- argument checks --------------------------------------------------------------------------


@pytest.mark.parametrize("buckets", [0, -1, -64])
@pytest.mark.parametrize("dedupe", [pharmacy, medical], ids=["pharmacy", "medical"])
def test_buckets_below_one_is_a_value_error(tmp_path: Path, dedupe: Dedupe, buckets: int) -> None:
    src = PHARMACY if dedupe is pharmacy else MEDICAL
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    with pytest.raises(ValueError, match="buckets must be at least 1"):
        dedupe(src, out_dir / "o.csv", buckets=buckets)
    assert names(out_dir) == []


# --- stats come from the one run --------------------------------------------------------------


@pytest.mark.parametrize("buckets", [None, 8], ids=["single-pass", "partitioned"])
@pytest.mark.parametrize("dedupe", [pharmacy, medical], ids=["pharmacy", "medical"])
def test_inputs_are_scanned_once_and_outputs_never_read_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dedupe: Dedupe, buckets: int | None
) -> None:
    src = PHARMACY if dedupe is pharmacy else MEDICAL
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    scanned: list[Path] = []
    real = pl.scan_csv

    def spy(source: Any, *args: Any, **kwargs: Any) -> pl.LazyFrame:
        scanned.append(Path(source))
        return real(source, *args, **kwargs)

    monkeypatch.setattr(pl, "scan_csv", spy)
    stats = dedupe(src, out_dir / "o.csv", buckets=buckets)
    assert stats.rows_out > 0
    # scan_claims reads the header with one scan and builds the pipeline with another. Counting
    # would add a scan of INPUT, and counting rows_out a scan of OUTPUT or its temp file.
    assert scanned == [src, src]
    assert names(out_dir) == (["o.csv", "o.rejects.csv"] if dedupe is pharmacy else ["o.csv"])


# --- CLI --------------------------------------------------------------------------------------


@pytest.fixture
def src(tmp_path: Path) -> Path:
    path = tmp_path / "in.csv"
    path.write_text(HEADER + "1,d,0002-3227-30,1,2\n1,d,0002-3227-30,1,2\n")
    return path


def test_cli_passes_partition_flags_to_the_pharmacy_library(src: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    fake = mock.Mock(return_value=CleanStats(rows_in=2, rows_out=1))
    with mock.patch.object(cli, "dedupe_ndc_pharm_data", fake):
        assert (
            cli.main(["dedupe-pharmacy", str(src), str(out), "--buckets", "8", "--keep-order"]) == 0
        )
    fake.assert_called_once_with(src, out, tmp_path / "out.rejects.csv", buckets=8, keep_order=True)


def test_cli_passes_partition_flags_to_the_medical_library(src: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    fake = mock.Mock(return_value=CleanStats(rows_in=2, rows_out=1))
    with mock.patch.object(cli, "dedupe_medical_data", fake):
        assert cli.main(["dedupe-medical", str(src), str(out), "--buckets", "2"]) == 0
        fake.assert_called_once_with(src, out, buckets=2)
        fake.reset_mock()
        assert cli.main(["dedupe-medical", str(src), str(out)]) == 0
        fake.assert_called_once_with(src, out)


@pytest.mark.parametrize("command", ["dedupe-pharmacy", "dedupe-medical"])
@pytest.mark.parametrize("value", ["0", "-5"])
def test_cli_rejects_buckets_below_one(
    src: Path,
    tmp_path: Path,
    command: str,
    value: str,
    assert_clean_failure: Callable[[int], str],
) -> None:
    out = tmp_path / "out.csv"
    with mock.patch.object(cli, "dedupe_ndc_pharm_data") as library:
        code = cli.main([command, str(src), str(out), f"--buckets={value}"])
    line = assert_clean_failure(code)
    assert line == "error: --buckets must be at least 1"
    library.assert_not_called()
    assert not out.exists()


@pytest.mark.parametrize("command", ["dedupe-pharmacy", "dedupe-medical"])
def test_cli_buckets_must_be_an_integer(
    src: Path, tmp_path: Path, command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main([command, str(src), str(tmp_path / "o.csv"), "--buckets", "many"])
    assert excinfo.value.code == 2
    assert "invalid int value" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["dedupe-pharmacy", "dedupe-medical"])
def test_cli_help_lists_the_flags(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main([command, "--help"])
    out = capsys.readouterr().out
    assert "--buckets N" in out
    assert "--keep-order" in out


def test_cli_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plain, part = tmp_path / "plain.csv", tmp_path / "part.csv"
    assert cli.main(["dedupe-pharmacy", str(PHARMACY), str(plain)]) == 0
    plain_summary = capsys.readouterr().err
    assert (
        cli.main(["dedupe-pharmacy", str(PHARMACY), str(part), "--buckets", "16", "--keep-order"])
        == 0
    )
    assert capsys.readouterr().err == plain_summary.replace(str(plain), str(part)).replace(
        "plain.rejects", "part.rejects"
    )
    assert part.read_bytes() == plain.read_bytes()

    assert cli.main(["dedupe-medical", str(MEDICAL), str(plain)]) == 0
    capsys.readouterr()
    assert cli.main(["dedupe-medical", str(MEDICAL), str(part), "--buckets", "16"]) == 0
    assert capsys.readouterr().err.startswith("read 30000 rows, wrote 28285 rows (1715 dup")
    assert sorted_lines(part) == sorted_lines(plain)
    assert not [n for n in names(tmp_path) if n.startswith(".")]
