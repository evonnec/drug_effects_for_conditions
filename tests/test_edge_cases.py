"""Hand-written inputs with explicit expected results for the dedupe pipeline."""

from collections.abc import Callable
from pathlib import Path

import pytest

from drug_effects_for_conditions import CleanStats, cli

type RunDedupe = Callable[[bytes], tuple[CleanStats, bytes]]
type ParseCsv = Callable[[bytes], list[list[str]]]

HDR = b"Patient_ID,Date_Submitted,NDC,Amount_Billed,Amount_Paid"
ROW = b"1,2020-01-01,00002322730,1.00,2.00\n"

XFAIL_7_BLANK = pytest.mark.xfail(strict=True, reason="#7: blank lines become ,,,, rows")
XFAIL_7_NDC = pytest.mark.xfail(strict=True, reason="#7: invalid NDC passes through, not rejected")


def _case(
    name: str, data: bytes, expected: bytes, mark: pytest.MarkDecorator | None = None
) -> object:
    return pytest.param(data, expected, id=name, marks=[mark] if mark else [])


def _bad_ndc(ndc: bytes) -> bytes:
    return HDR + b"\n1,2020-01-01," + ndc + b",1.00,2.00\n"


OUTPUT_CASES = [
    _case("lf", HDR + b"\n" + b"1,2020-01-01,0002-3227-30,1.00,2.00\n" * 2, HDR + b"\n" + ROW),
    _case(
        "crlf", HDR + b"\r\n" + b"1,2020-01-01,0002-3227-30,1.00,2.00\r\n" * 2, HDR + b"\n" + ROW
    ),
    _case(
        "crlf_no_final_newline",
        HDR + b"\r\n1,2020-01-01,0002-3227-30,1.00,2.00",
        HDR + b"\n" + ROW,
    ),
    _case(
        "utf8_bom_before_header",
        b"\xef\xbb\xbf" + HDR + b"\n1,2020-01-01,2322730,1.00,2.00\n",
        HDR + b"\n" + ROW,
    ),
    _case(
        "quoted_commas_and_quotes",
        HDR + b'\n"Doe, J",2020-01-01,2322730,"1""2",2.00\n',
        HDR + b'\n"Doe, J",2020-01-01,00002322730,"1""2",2.00\n',
    ),
    _case(
        "quoted_embedded_newline",
        HDR + b'\n"a\nb",2020-01-01,2322730,1.00,2.00\n"a\nb",2020-01-01,2322730,1.00,2.00\n',
        HDR + b'\n"a\nb",2020-01-01,00002322730,1.00,2.00\n',
    ),
    _case(
        "quoted_embedded_crlf",
        HDR + b'\r\n"a\r\nb",2020-01-01,2322730,1.00,2.00\r\n',
        HDR + b'\n"a\r\nb",2020-01-01,00002322730,1.00,2.00\n',
    ),
    _case(
        "unicode_patient_id",
        HDR + "\né中,d,5,1,2\n".encode(),
        HDR + "\né中,d,00000000005,1,2\n".encode(),
    ),
    _case("header_only", HDR + b"\n", HDR + b"\n"),
    _case("header_only_no_newline", HDR, HDR + b"\n"),
    _case("no_final_newline", HDR + b"\n" + ROW.rstrip(b"\n"), HDR + b"\n" + ROW),
    _case(
        "trailing_blank_line",
        HDR + b"\n" + ROW + b"\n",
        HDR + b"\n" + ROW,
        XFAIL_7_BLANK,
    ),
    _case(
        "blank_lines_between_rows",
        HDR + b"\n" + ROW + b"\n\n" + ROW.replace(b"1,", b"2,", 1),
        HDR + b"\n" + ROW + ROW.replace(b"1,", b"2,", 1),
        XFAIL_7_BLANK,
    ),
    _case("bare_empty_ndc", _bad_ndc(b""), HDR + b"\n", XFAIL_7_NDC),
    _case("quoted_empty_ndc", _bad_ndc(b'""'), HDR + b"\n", XFAIL_7_NDC),
    _case("whitespace_ndc", _bad_ndc(b"   "), HDR + b"\n", XFAIL_7_NDC),
    _case("letters_ndc", _bad_ndc(b"ABC"), HDR + b"\n", XFAIL_7_NDC),
    _case("twelve_digit_ndc", _bad_ndc(b"123456789012"), HDR + b"\n", XFAIL_7_NDC),
    _case("dashes_and_spaces_ndc", _bad_ndc(b"0    -    -  "), HDR + b"\n", XFAIL_7_NDC),
    _case(
        "valid_row_survives_next_to_invalid",
        HDR + b"\n" + ROW + b"1,2020-01-01,ABC,1.00,2.00\n",
        HDR + b"\n" + ROW,
        XFAIL_7_NDC,
    ),
    _case(
        "empty_and_quoted_empty_amounts_are_duplicates",
        HDR + b'\n1,d,5,,2\n1,d,5,"",2\n',
        HDR + b"\n1,d,00000000005,,2\n",
        pytest.mark.xfail(strict=True, reason='#19: bare empty and "" fields not deduped'),
    ),
]


@pytest.mark.parametrize(("data", "expected"), OUTPUT_CASES)
def test_output_bytes(run_dedupe: RunDedupe, data: bytes, expected: bytes) -> None:
    _, output = run_dedupe(data)
    assert output == expected


def test_stats_count_input_and_output_rows(run_dedupe: RunDedupe) -> None:
    stats, _ = run_dedupe(HDR + b"\r\n" + b"1,d,0002-3227-30,1,2\r\n1,d,00002322730,1,2\r\n")
    assert (stats.rows_in, stats.rows_out, stats.duplicates_removed) == (2, 1, 1)


def test_stats_for_header_only_input(run_dedupe: RunDedupe) -> None:
    stats, _ = run_dedupe(HDR + b"\n")
    assert (stats.rows_in, stats.rows_out) == (0, 0)


def test_output_has_exactly_the_five_header_columns(
    run_dedupe: RunDedupe, parse_csv: ParseCsv
) -> None:
    _, output = run_dedupe(b"\xef\xbb\xbf" + HDR + b"\n" + ROW)
    assert parse_csv(output)[0] == HDR.decode().split(",")


XFAIL_9 = pytest.mark.xfail(strict=True, reason="#9: CLI prints a traceback instead of an error")
XFAIL_9_SHORT = pytest.mark.xfail(strict=True, reason="#9: short rows are silently null-filled")

# Inputs the CLI must refuse with "error: ..." and exit code 1 (never a traceback).
REJECTED_INPUTS = [
    pytest.param(b"", id="zero_byte_file", marks=XFAIL_9),
    pytest.param(b"\xef\xbb\xbf", id="bom_only", marks=XFAIL_9),
    pytest.param(HDR + b"\n1,d,5,1,2,3\n", id="long_row", marks=XFAIL_9),
    pytest.param(HDR + b"\n1,d,5,1\n", id="short_row", marks=XFAIL_9_SHORT),
    pytest.param(HDR + b"\n\xff,d,5,1,2\n", id="invalid_utf8", marks=XFAIL_9),
    pytest.param(HDR + b'\n1,d,5,a"b,2\n', id="stray_quote", marks=XFAIL_9),
    pytest.param(b"patient_id" + HDR[10:] + b"\n" + ROW, id="header_lowercase"),
    pytest.param(
        b"Patient_ID,NDC,Date_Submitted,Amount_Billed,Amount_Paid\n1,5,d,1,2\n", id="header_order"
    ),
    pytest.param(HDR + b",Extra\n" + ROW.rstrip(b"\n") + b",x\n", id="header_extra_column"),
    pytest.param(HDR[:-9] + b"Billed\n" + ROW, id="header_duplicate_name"),
    pytest.param(b"Patient_ID,Date_Submitted,NDC,Amount_Billed\n1,d,5,1\n", id="header_missing"),
]


@pytest.mark.parametrize("data", REJECTED_INPUTS)
def test_cli_rejects_with_clean_error(
    tmp_path: Path,
    data: bytes,
    capsys: pytest.CaptureFixture[str],
) -> None:
    src = tmp_path / "bad.csv"
    src.write_bytes(data)
    assert cli.main(["dedupe-pharmacy", str(src), str(tmp_path / "out.csv")]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("error: ")
    assert "Traceback" not in captured.err
    assert captured.out == ""


@XFAIL_9_SHORT
def test_short_row_is_not_silently_null_filled(run_dedupe: RunDedupe) -> None:
    _, output = run_dedupe(HDR + b"\n1,d,5\n")
    assert b"1,d,00000000005,,\n" not in output
