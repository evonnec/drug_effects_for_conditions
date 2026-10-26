"""`write_csv_atomic`, `ClaimsFileError` and the error paths shared by all three commands."""

import errno
from collections.abc import Callable
from pathlib import Path
from typing import Literal
from unittest import mock

import polars as pl
import pytest

from drug_effects_for_conditions import (
    ClaimsFileError,
    ClaimsSchemaError,
    cli,
    dedupe_medical_data,
    dedupe_ndc_pharm_data,
    scan_medical,
    scan_pharmacy,
    write_csv_atomic,
)

type CleanFailure = Callable[[int], str]
type MakeLink = Callable[[Literal["hard", "sym"], Path, Path], None]

DATA = Path(__file__).parents[1] / "data"
PHARMACY_HEADER = "Patient_ID,Date_Submitted,NDC,Amount_Billed,Amount_Paid"
MEDICAL_HEADER = (
    "Patient_ID,Diagnosis_Code_1,Diagnosis_Code_2,Diagnosis_Code_3,Diagnosis_Code_4,"
    "Service_Date,Amount_Paid,Amount_Billed"
)
MEDICAL_ROW = "1,32723,,,,4/19/2016,5.39,12.32\n"


def names(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


def with_header(header: str, body: bytes) -> bytes:
    """`body` with the placeholder `HDR` line replaced by `header`."""
    return body.replace(b"HDR", header.encode())


# --- write_csv_atomic ---------------------------------------------------------------------------


def test_atomic_write_creates_a_missing_target(tmp_path: Path) -> None:
    target = tmp_path / "out.csv"
    write_csv_atomic(pl.LazyFrame({"a": ["1", "2"]}), target)
    assert target.read_text() == "a\n1\n2\n"
    assert names(tmp_path) == ["out.csv"]


def test_atomic_write_replaces_an_existing_target(tmp_path: Path) -> None:
    target = tmp_path / "out.csv"
    target.write_text("old\n")
    write_csv_atomic(pl.LazyFrame({"a": ["1"]}), target)
    assert target.read_text() == "a\n1\n"
    assert names(tmp_path) == ["out.csv"]


def test_atomic_write_failure_keeps_the_target_and_removes_the_temp(tmp_path: Path) -> None:
    target = tmp_path / "out.csv"
    target.write_bytes(b"precious\n")

    def half_write(self: pl.LazyFrame, path: Path, *args: object, **kwargs: object) -> None:
        path.write_bytes(b"half a fi")
        raise OSError(errno.ENOSPC, "No space left on device")

    with (
        mock.patch.object(pl.LazyFrame, "sink_csv", half_write),
        pytest.raises(OSError, match="No space"),
    ):
        write_csv_atomic(pl.LazyFrame({"a": ["1"]}), target)
    assert target.read_bytes() == b"precious\n"
    assert names(tmp_path) == ["out.csv"]


def test_atomic_write_into_a_missing_directory_fails_and_leaves_nothing(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        write_csv_atomic(pl.LazyFrame({"a": ["1"]}), tmp_path / "nope" / "out.csv")
    assert names(tmp_path) == []


def test_atomic_write_replaces_a_symlink_instead_of_following_it(
    tmp_path: Path, make_link: MakeLink
) -> None:
    real = tmp_path / "real.csv"
    real.write_text("keep\n")
    link = tmp_path / "link.csv"
    make_link("sym", real, link)
    write_csv_atomic(pl.LazyFrame({"a": ["1"]}), link)
    assert real.read_text() == "keep\n"
    assert link.read_text() == "a\n1\n"


# --- #20 for medical, and the clean + rejects pair ----------------------------------------------


@pytest.mark.parametrize("kind", ["hard", "sym"])
def test_medical_library_never_truncates_input_through_a_link(
    tmp_path: Path, kind: Literal["hard", "sym"], make_link: MakeLink
) -> None:
    src = tmp_path / "in.csv"
    src.write_text(MEDICAL_HEADER + "\n" + MEDICAL_ROW * 2)
    before = src.read_bytes()
    link = tmp_path / "link.csv"
    make_link(kind, src, link)
    dedupe_medical_data(src, link)
    assert src.read_bytes() == before
    assert link.read_text() == MEDICAL_HEADER + "\n" + MEDICAL_ROW


def test_pharmacy_pair_is_all_or_nothing(tmp_path: Path) -> None:
    src = tmp_path / "in.csv"
    src.write_text(PHARMACY_HEADER + "\n1,d,0002-3227-30,1,2\n1,d,x,1,2\n")
    out, rejects = tmp_path / "out.csv", tmp_path / "rejects.csv"
    out.write_text("old clean\n")
    real_sink = pl.LazyFrame.sink_csv
    calls: list[Path] = []

    def fail_second(self: pl.LazyFrame, path: Path, *args: object, **kwargs: object) -> None:
        calls.append(path)
        if len(calls) == 2:
            raise OSError(errno.ENOSPC, "No space left on device")
        real_sink(self, path)

    with (
        mock.patch.object(pl.LazyFrame, "sink_csv", fail_second),
        pytest.raises(OSError, match="No space"),
    ):
        dedupe_ndc_pharm_data(src, out, rejects)
    assert out.read_text() == "old clean\n"
    assert names(tmp_path) == ["in.csv", "out.csv"]


# --- ClaimsFileError ----------------------------------------------------------------------------

BAD_FILES = [
    pytest.param(b"", "empty", id="zero_byte"),
    pytest.param(b"\xef\xbb\xbf", "empty", id="bom_only"),
    pytest.param(b"HDR\n1,d,5,1\n", "line 2 has 4 fields, fewer than the 5 columns", id="short"),
    pytest.param(b"HDR\n1,d,5,1,2,3\n", "line 2 has 6 fields, more than the 5 columns", id="long"),
    pytest.param(b"HDR\n\xff,d,5,1,2\n", "utf-8", id="invalid_utf8"),
]


@pytest.mark.parametrize(("body", "needle"), BAD_FILES)
def test_scan_names_the_file(tmp_path: Path, body: bytes, needle: str) -> None:
    src = tmp_path / "bad.csv"
    src.write_bytes(with_header(PHARMACY_HEADER, body))
    with pytest.raises(ClaimsFileError) as info:
        scan_pharmacy(src)
    assert str(src) in str(info.value)
    assert needle in str(info.value).lower()
    assert "\n" not in str(info.value)


def test_medical_scan_names_the_file(tmp_path: Path) -> None:
    src = tmp_path / "bad.csv"
    src.write_text(MEDICAL_HEADER + "\n1,32723,,,\n")
    with pytest.raises(ClaimsFileError, match=r"bad\.csv: line 2 has 5 fields"):
        scan_medical(src)


def test_trailing_empty_fields_and_blank_lines_are_not_short_rows(tmp_path: Path) -> None:
    src = tmp_path / "ok.csv"
    src.write_text(PHARMACY_HEADER + "\n1,d,5,,\n\n   \n1,d,6,1,2\n")
    assert scan_pharmacy(src).collect().height >= 2


def test_stray_quote_surfaces_when_the_pipeline_runs(tmp_path: Path) -> None:
    # polars only sees the bad quote when it reads the rows, not during the header scan.
    src = tmp_path / "bad.csv"
    src.write_text(PHARMACY_HEADER + '\n1,d,5,a"b,2\n')
    with pytest.raises(ClaimsFileError, match=r"bad\.csv"):
        dedupe_ndc_pharm_data(src, tmp_path / "out.csv")
    assert names(tmp_path) == ["bad.csv"]


def test_a_wrong_header_is_still_a_schema_error(tmp_path: Path) -> None:
    src = tmp_path / "in.csv"
    src.write_text("a,b\n1,2\n")
    with pytest.raises(ClaimsSchemaError):
        scan_pharmacy(src)


def test_a_missing_input_is_still_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        scan_pharmacy(tmp_path / "missing.csv")


# --- the shared CLI error path ------------------------------------------------------------------


@pytest.mark.parametrize("command", ["dedupe-pharmacy", "dedupe-medical"])
def test_dedupe_commands_refuse_a_directory_as_output(
    tmp_path: Path, command: str, assert_clean_failure: CleanFailure
) -> None:
    src = tmp_path / "in.csv"
    src.write_text((PHARMACY_HEADER if command == "dedupe-pharmacy" else MEDICAL_HEADER) + "\n")
    folder = tmp_path / "folder"
    folder.mkdir()
    assert "directory" in assert_clean_failure(cli.main([command, str(src), str(folder)]))
    assert names(folder) == []


def test_pharmacy_refuses_a_directory_as_rejects_path(
    tmp_path: Path, assert_clean_failure: CleanFailure
) -> None:
    src = tmp_path / "in.csv"
    src.write_text(PHARMACY_HEADER + "\n")
    folder = tmp_path / "folder"
    folder.mkdir()
    argv = ["dedupe-pharmacy", "--rejects", str(folder), str(src), str(tmp_path / "out.csv")]
    assert "directory" in assert_clean_failure(cli.main(argv))
    assert names(tmp_path) == ["folder", "in.csv"]


@pytest.mark.parametrize(
    "body",
    [b"", b"HDR\n1,32723,,,\n", b"HDR\n\xff,1,,,,d,1,2\n"],
    ids=["empty", "short_row", "invalid_utf8"],
)
def test_medical_cli_reports_a_bad_file_and_keeps_the_output(
    tmp_path: Path, body: bytes, assert_clean_failure: CleanFailure
) -> None:
    src, out = tmp_path / "in.csv", tmp_path / "out.csv"
    src.write_bytes(with_header(MEDICAL_HEADER, body))
    out.write_bytes(b"precious\n")
    assert str(src) in assert_clean_failure(cli.main(["dedupe-medical", str(src), str(out)]))
    assert out.read_bytes() == b"precious\n"
    assert names(tmp_path) == ["in.csv", "out.csv"]


def cohort_argv(out: Path, **overrides: Path) -> list[str]:
    paths = {
        "pharmacy": DATA / "pharmacy_data_sample.csv",
        "medical": DATA / "medical_data_sample.csv",
        "ndc": DATA / "ndc.csv",
        "codes": DATA / "diagnosis_code.csv",
    } | overrides
    flags = [part for name, path in paths.items() for part in (f"--{name}", str(path))]
    return ["cohort", *flags, str(out)]


@pytest.mark.parametrize("which", ["pharmacy", "medical"])
@pytest.mark.parametrize(
    "body", [b"", b"HDR\n1,2\n", b"HDR\n\xff,d,5,1,2\n"], ids=["empty", "short", "invalid_utf8"]
)
def test_cohort_cli_reports_a_bad_claims_file(
    tmp_path: Path, which: str, body: bytes, assert_clean_failure: CleanFailure
) -> None:
    header = PHARMACY_HEADER if which == "pharmacy" else MEDICAL_HEADER
    bad, out = tmp_path / "bad.csv", tmp_path / "out.csv"
    bad.write_bytes(with_header(header, body))
    out.write_bytes(b"precious\n")
    line = assert_clean_failure(cli.main(cohort_argv(out, **{which: bad})))
    assert str(bad) in line
    assert out.read_bytes() == b"precious\n"
    assert names(tmp_path) == ["bad.csv", "out.csv"]


def test_cohort_cli_reports_a_stray_quote(
    tmp_path: Path, assert_clean_failure: CleanFailure
) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text(PHARMACY_HEADER + '\n1,1/1/2016,5,a"b,2\n')
    out = tmp_path / "out.csv"
    assert str(bad) in assert_clean_failure(cli.main(cohort_argv(out, pharmacy=bad)))
    assert names(tmp_path) == ["bad.csv"]


def test_cohort_cli_refuses_a_directory_as_output(
    tmp_path: Path, assert_clean_failure: CleanFailure
) -> None:
    out = tmp_path / "outdir"
    out.mkdir()
    assert "directory" in assert_clean_failure(cli.main(cohort_argv(out)))
    assert names(out) == []


def test_cohort_cli_failed_write_keeps_the_output(
    tmp_path: Path, assert_clean_failure: CleanFailure
) -> None:
    out = tmp_path / "out.csv"
    out.write_bytes(b"precious\n")
    error = OSError(errno.ENOSPC, "No space left on device")
    with mock.patch.object(pl.LazyFrame, "sink_csv", side_effect=error):
        assert "No space" in assert_clean_failure(cli.main(cohort_argv(out)))
    assert out.read_bytes() == b"precious\n"
    assert names(tmp_path) == ["out.csv"]


def test_cohort_cli_output_parent_is_a_file(
    tmp_path: Path, assert_clean_failure: CleanFailure
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    assert_clean_failure(cli.main(cohort_argv(blocker / "out.csv")))
    assert blocker.read_text() == "x"
