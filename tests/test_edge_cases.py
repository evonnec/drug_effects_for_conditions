"""Hand-written inputs with explicit expected results for the dedupe pipeline."""

import csv
import io
from collections.abc import Callable
from pathlib import Path

import pytest
from polars.exceptions import PolarsError

from drug_effects_for_conditions import (
    MEDICAL_COLUMNS,
    PHARMACY_COLUMNS,
    CleanStats,
    cli,
    dedupe_medical_data,
)
from drug_effects_for_conditions.pharmacy import clean_pharmacy, scan_pharmacy

type RunDedupe = Callable[[bytes], tuple[CleanStats, bytes]]
type ParseCsv = Callable[[bytes], list[list[str]]]
type CleanFailure = Callable[[int], str]

HDR = b"Patient_ID,Date_Submitted,NDC,Amount_Billed,Amount_Paid"
ROW = b"1,2020-01-01,00002322730,1.00,2.00\n"


def _case(
    name: str, data: bytes, expected: bytes, mark: pytest.MarkDecorator | None = None
) -> object:
    return pytest.param(data, expected, id=name, marks=[mark] if mark else [])


def _ndc_row(ndc: bytes) -> bytes:
    return HDR + b"\n1,2020-01-01," + ndc + b",1.00,2.00\n"


def _bad_ndc(name: str, ndc: bytes) -> object:
    """#7: a row whose NDC is invalid is removed from the clean output."""
    return _case(name, _ndc_row(ndc), HDR + b"\n")


def _good_ndc(name: str, ndc: bytes, normalized: bytes) -> object:
    return _case(name, _ndc_row(ndc), _ndc_row(normalized))


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
        "every_field_quoted",
        HDR + b'\n"1","2020-01-01","0002-3227-30","1.00","2.00"\n',
        HDR + b"\n" + ROW,
    ),
    _case(
        "unicode_patient_id",
        HDR + "\né中,d,5,1,2\n".encode(),
        HDR + "\né中,d,00000000005,1,2\n".encode(),
    ),
    # Empty fields stay bare: they are not rewritten as "" (which would also change how #19 must
    # be fixed: both spellings have to collapse to the bare form).
    _case("bare_empty_amount_stays_bare", HDR + b"\n1,d,5,,2\n", HDR + b"\n1,d,00000000005,,2\n"),
    _case("header_only", HDR + b"\n", HDR + b"\n"),
    _case("header_only_no_newline", HDR, HDR + b"\n"),
    _case("no_final_newline", HDR + b"\n" + ROW.rstrip(b"\n"), HDR + b"\n" + ROW),
    _case(
        "trailing_blank_line",
        HDR + b"\n" + ROW + b"\n",
        HDR + b"\n" + ROW,
    ),
    _case(
        "blank_lines_between_rows",
        HDR + b"\n" + ROW + b"\n\n" + ROW.replace(b"1,", b"2,", 1),
        HDR + b"\n" + ROW + ROW.replace(b"1,", b"2,", 1),
    ),
    # #7 boundaries that must stay valid: 1 and 11 digits once dashes are removed.
    _good_ndc("one_digit_ndc_is_valid", b"7", b"00000000007"),
    _good_ndc("eleven_digit_ndc_is_valid", b"12345678901", b"12345678901"),
    _good_ndc("eleven_digits_with_dashes_is_valid", b"12345-6789-01", b"12345678901"),
    _good_ndc("leading_dash_is_valid", b"-5", b"00000000005"),
    _bad_ndc("bare_empty_ndc", b""),
    _bad_ndc("quoted_empty_ndc", b'""'),
    _bad_ndc("whitespace_ndc", b"   "),
    _bad_ndc("letters_ndc", b"ABC"),
    _bad_ndc("twelve_digit_ndc", b"123456789012"),
    _bad_ndc("twelve_digits_with_dashes_ndc", b"12345-6789-012"),
    _bad_ndc("dashes_and_spaces_ndc", b"0    -    -  "),
    _bad_ndc("dashes_only_ndc", b"--"),
    # "Not all zeros": every spelling of zero is invalid, however it is padded or dashed.
    _bad_ndc("all_zero_ndc", b"00000000000"),
    _bad_ndc("single_zero_ndc", b"0"),
    _bad_ndc("all_zero_dashed_ndc", b"00000-0000-00"),
    # "1-11 digits" means ASCII 0-9 with nothing else: no padding, no other scripts' digits.
    _bad_ndc("padded_digits_ndc", b" 2322730"),
    _bad_ndc("non_ascii_digits_ndc", "\u0661\u0662\u0663".encode()),
    _bad_ndc("fullwidth_digits_ndc", "\uff12\uff13\uff12\uff12\uff17\uff13\uff10".encode()),
    _case(
        "valid_row_survives_next_to_invalid",
        HDR + b"\n" + ROW + b"1,2020-01-01,ABC,1.00,2.00\n",
        HDR + b"\n" + ROW,
    ),
    _case(
        "empty_and_quoted_empty_amounts_are_duplicates",
        HDR + b'\n1,d,5,,2\n1,d,5,"",2\n',
        HDR + b"\n1,d,00000000005,,2\n",
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


# --- #7: the planned rejects API -----------------------------------------------------------------
# These pin the parts of #7 the output-bytes cases cannot see. Without them, a "fix" that silently
# drops invalid rows (no rejects file, no counts) would turn every #7 case above green.

INVALID_ROWS = HDR + b"\n" + ROW + b"2,d,ABC,1,2\n3,d,,1,2\n" + ROW


def test_blank_lines_are_counted_separately_not_as_duplicates(run_dedupe: RunDedupe) -> None:
    # One blank line between the two rows and one trailing blank line.
    stats, _ = run_dedupe(HDR + b"\n" + ROW + b"\n" + ROW.replace(b"1,", b"2,", 1) + b"\n")
    assert stats.duplicates_removed == 0
    assert stats.rows_out == 2
    assert stats.blank_rows == 2


def test_stats_count_rejected_rows(run_dedupe: RunDedupe) -> None:
    stats, _ = run_dedupe(INVALID_ROWS)
    assert (stats.rejected, stats.rows_out, stats.duplicates_removed) == (2, 1, 1)


def test_clean_pharmacy_returns_clean_and_rejects(tmp_path: Path) -> None:
    src = tmp_path / "in.csv"
    src.write_bytes(INVALID_ROWS)
    result = clean_pharmacy(scan_pharmacy(src))
    clean, rejects = result.clean.collect(), result.rejects.collect()
    assert clean["Patient_ID"].to_list() == ["1"]
    assert rejects["Patient_ID"].to_list() == ["2", "3"]


def _rejects_rows(path: Path) -> list[list[str]]:
    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8"), newline="")))
    assert tuple(rows[0][: len(PHARMACY_COLUMNS)]) == PHARMACY_COLUMNS
    return rows[1:]


def test_cli_writes_rejects_next_to_output_by_default(tmp_path: Path) -> None:
    src, out = tmp_path / "in.csv", tmp_path / "clean.csv"
    src.write_bytes(INVALID_ROWS)
    assert cli.main(["dedupe-pharmacy", str(src), str(out)]) == 0
    assert out.read_bytes() == HDR + b"\n" + ROW
    rejects = tmp_path / "clean.rejects.csv"
    assert rejects.is_file()
    assert [row[0] for row in _rejects_rows(rejects)] == ["2", "3"]


def test_cli_rejects_option_sets_the_path(tmp_path: Path) -> None:
    src, out, rejects = tmp_path / "in.csv", tmp_path / "clean.csv", tmp_path / "r.csv"
    src.write_bytes(INVALID_ROWS)
    assert cli.main(["dedupe-pharmacy", "--rejects", str(rejects), str(src), str(out)]) == 0
    assert [row[0] for row in _rejects_rows(rejects)] == ["2", "3"]
    assert not (tmp_path / "clean.rejects.csv").exists()


# --- #9: malformed input -------------------------------------------------------------------------

# Inputs the CLI must refuse with one "error: ..." line and exit code 1 (never a traceback).
REJECTED_INPUTS = [
    pytest.param(b"", id="zero_byte_file"),
    pytest.param(b"\xef\xbb\xbf", id="bom_only"),
    pytest.param(HDR + b"\n1,d,5,1,2,3\n", id="long_row"),
    pytest.param(HDR + b"\n" + ROW * 3 + b"1,d,5,1,2,3\n", id="long_row_after_good_rows"),
    pytest.param(HDR + b"\n1,d,5,1\n", id="short_row"),
    pytest.param(HDR + b"\n\xff,d,5,1,2\n", id="invalid_utf8"),
    pytest.param(HDR + b'\n1,d,5,a"b,2\n', id="stray_quote"),
    pytest.param(b"patient_id" + HDR[10:] + b"\n" + ROW, id="header_lowercase"),
    pytest.param(
        b"Patient_ID,NDC,Date_Submitted,Amount_Billed,Amount_Paid\n1,5,d,1,2\n", id="header_order"
    ),
    pytest.param(HDR + b",Extra\n" + ROW.rstrip(b"\n") + b",x\n", id="header_extra_column"),
    pytest.param(HDR[:-9] + b"Billed\n" + ROW, id="header_misspelled_column"),
    pytest.param(
        HDR.replace(b"Amount_Paid", b"Amount_Billed") + b"\n" + ROW, id="header_duplicate_name"
    ),
    pytest.param(b"Patient_ID,Date_Submitted,NDC,Amount_Billed\n1,d,5,1\n", id="header_missing"),
]


@pytest.mark.parametrize("data", REJECTED_INPUTS)
def test_cli_rejects_with_clean_error(
    tmp_path: Path, data: bytes, assert_clean_failure: CleanFailure
) -> None:
    src, out = tmp_path / "bad.csv", tmp_path / "out.csv"
    src.write_bytes(data)
    assert_clean_failure(cli.main(["dedupe-pharmacy", str(src), str(out)]))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bad.csv"], "a failed run left files"


def test_short_row_is_an_error_in_the_library(run_dedupe: RunDedupe) -> None:
    # Checks for an error rather than the absence of one null-filled spelling: the old version of
    # this test also passed if the short row was padded with "" instead of nulls.
    with pytest.raises((ValueError, PolarsError)):
        run_dedupe(HDR + b"\n1,d,5\n")


# --- #8: medical claims ------------------------------------------------------------------------

MHDR = ",".join(MEDICAL_COLUMNS).encode()
MROW = b"1,32723,,,,4/19/2016,5.39,12.32\n"
PAD8 = b" " * 8

MEDICAL_CASES = [
    _case(
        "padded_blank_codes_become_bare",
        MHDR + b"\n1,32723," + b",".join([PAD8] * 3) + b",d,1,2\n",
        MHDR + b"\n1,32723,,,,d,1,2\n",
    ),
    _case(
        "v_code_padding_is_stripped",
        MHDR + b"\n1,V5481   ,,,,d,1,2\n",
        MHDR + b"\n1,V5481,,,,d,1,2\n",
    ),
    _case(
        "padded_and_bare_rows_collapse",
        MHDR + b"\n1,1," + PAD8 + b",,,d,1,2\n1,1,,,,d,1,2\n",
        MHDR + b"\n1,1,,,,d,1,2\n",
    ),
    _case(
        "crlf_and_trailing_blank_line",
        MHDR + b"\r\n" + MROW.replace(b"\n", b"\r\n") * 2 + b"\r\n",
        MHDR + b"\n" + MROW,
    ),
    _case(
        "different_amounts_are_kept",
        MHDR + b"\n1,1,,,,d,1,2\n1,1,,,,d,1,3\n",
        MHDR + b"\n1,1,,,,d,1,2\n1,1,,,,d,1,3\n",
    ),
    _case(
        "amount_text_round_trips",
        MHDR + b"\n007,1,,,,01/02/2016,54.90,0060\n",
        MHDR + b"\n007,1,,,,01/02/2016,54.90,0060\n",
    ),
    _case(
        "code_internal_space_is_kept", MHDR + b"\n1,A B,,,,d,1,2\n", MHDR + b"\n1,A B,,,,d,1,2\n"
    ),
    _case("header_only", MHDR + b"\n", MHDR + b"\n"),
]


@pytest.mark.parametrize(("data", "expected"), MEDICAL_CASES)
def test_medical_output_bytes(tmp_path: Path, data: bytes, expected: bytes) -> None:
    src, out = tmp_path / "in.csv", tmp_path / "out.csv"
    src.write_bytes(data)
    dedupe_medical_data(src, out)
    assert out.read_bytes() == expected
