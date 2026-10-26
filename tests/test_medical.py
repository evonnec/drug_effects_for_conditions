import csv
from collections.abc import Callable
from pathlib import Path

import pytest

from drug_effects_for_conditions import (
    DIAGNOSIS_COLUMNS,
    MEDICAL_COLUMNS,
    ClaimsSchemaError,
    CleanStats,
    MedicalSchemaError,
    PharmacySchemaError,
    cli,
    dedupe_medical_data,
    scan_medical,
)

type WriteCsv = Callable[..., Path]

HEADER = ",".join(MEDICAL_COLUMNS)
PAD = " " * 8


def read_rows(path: Path) -> list[list[str]]:
    with path.open(newline="") as f:
        return list(csv.reader(f))


def dedupe(write_csv: WriteCsv, rows: list[str], tmp_path: Path) -> tuple[CleanStats, Path]:
    out = tmp_path / "out.csv"
    return dedupe_medical_data(write_csv(rows, header=HEADER), out), out


def test_columns() -> None:
    assert MEDICAL_COLUMNS[1:5] == DIAGNOSIS_COLUMNS
    assert all(c.startswith("Diagnosis_Code_") for c in DIAGNOSIS_COLUMNS)


def test_exact_duplicates_collapse(write_csv: WriteCsv, tmp_path: Path) -> None:
    row = f"1,32723,{PAD},{PAD},{PAD},4/19/2016,5.39,12.32"
    stats, out = dedupe(write_csv, [row, row, row], tmp_path)
    assert read_rows(out)[1:] == [["1", "32723", "", "", "", "4/19/2016", "5.39", "12.32"]]
    assert stats == CleanStats(rows_in=3, rows_out=1)
    assert stats.duplicates_removed == 2


def test_rows_differing_only_in_code_padding_collapse(write_csv: WriteCsv, tmp_path: Path) -> None:
    rows = [
        f"1,V5481   ,{PAD},{PAD},{PAD},4/19/2016,5,6",
        "1,V5481,,,,4/19/2016,5,6",
        '1, V5481 ,"","",  ,4/19/2016,5,6',
    ]
    stats, out = dedupe(write_csv, rows, tmp_path)
    assert read_rows(out)[1:] == [["1", "V5481", "", "", "", "4/19/2016", "5", "6"]]
    assert stats.duplicates_removed == 2


@pytest.mark.parametrize("other", ["1,1,,,,d,9,6", "1,1,,,,d,5,9"])
def test_rows_differing_in_an_amount_are_both_kept(
    write_csv: WriteCsv, tmp_path: Path, other: str
) -> None:
    stats, out = dedupe(write_csv, ["1,1,,,,d,5,6", other], tmp_path)
    assert len(read_rows(out)) == 3
    assert stats.duplicates_removed == 0


def test_blank_lines_produce_no_row(write_csv: WriteCsv, tmp_path: Path) -> None:
    rows = ["", "1,1,,,,d,5,6", "", f" ,{PAD},,,,  ,,", ""]
    stats, out = dedupe(write_csv, rows, tmp_path)
    assert len(read_rows(out)) == 2
    assert (stats.rows_in, stats.blank_rows) == (5, 4)
    assert stats.duplicates_removed == 0


def test_bare_and_quoted_empty_fields_deduplicate(write_csv: WriteCsv, tmp_path: Path) -> None:
    stats, out = dedupe(write_csv, ["1,1,,,,d,,6", '1,1,"","","",d,"",6'], tmp_path)
    assert read_rows(out)[1:] == [["1", "1", "", "", "", "d", "", "6"]]
    assert stats.duplicates_removed == 1


def test_first_occurrence_order_and_header(write_csv: WriteCsv, tmp_path: Path) -> None:
    rows = ["3,1,,,,d,1,1", "1,1,,,,d,1,1", "3,1,,,,d,1,1", "2,1,,,,d,1,1"]
    _, out = dedupe(write_csv, rows, tmp_path)
    got = read_rows(out)
    assert got[0] == list(MEDICAL_COLUMNS)
    assert [r[0] for r in got[1:]] == ["3", "1", "2"]


def test_non_diagnosis_columns_round_trip(write_csv: WriteCsv, tmp_path: Path) -> None:
    _, out = dedupe(write_csv, ["007,1,,,,01/02/2016,54.90,0060"], tmp_path)
    assert read_rows(out)[1] == ["007", "1", "", "", "", "01/02/2016", "54.90", "0060"]


def test_header_only(write_csv: WriteCsv, tmp_path: Path) -> None:
    stats, out = dedupe(write_csv, [], tmp_path)
    assert stats == CleanStats(rows_in=0, rows_out=0)
    assert read_rows(out) == [list(MEDICAL_COLUMNS)]


@pytest.mark.parametrize(
    "header",
    [
        "Patient_ID,Diagnosis_Code_1",
        "Patient_ID,Diagnosis_Code_1,Diagnosis_Code_2,Diagnosis_Code_3,Diagnosis_Code_4,"
        "Service_Date,Amount_Billed,Amount_Paid",
        "Patient_ID,Date_Submitted,NDC,Amount_Billed,Amount_Paid",
    ],
)
def test_wrong_header(write_csv: WriteCsv, header: str) -> None:
    src = write_csv(["1,2,3,4,5,6,7,8"], header=header)
    with pytest.raises(MedicalSchemaError, match="Expected columns") as info:
        scan_medical(src)
    assert "Diagnosis_Code_4" in str(info.value)
    assert isinstance(info.value, ClaimsSchemaError)
    assert not isinstance(info.value, PharmacySchemaError)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        scan_medical(tmp_path / "nope.csv")


# --- CLI ---------------------------------------------------------------------------------------


def test_cli_success(
    write_csv: WriteCsv, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = write_csv(["1,1,,,,d,5,6", "1,1,,,,d,5,6", "2,1,,,,d,5,6"], header=HEADER)
    out = tmp_path / "new" / "out.csv"
    assert cli.main(["dedupe-medical", str(src), str(out)]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"read 3 rows, wrote 2 rows (1 duplicates removed, 0 blank) to {out}\n"
    assert out.is_file()


def test_cli_wrong_header_is_one_error_line(
    write_csv: WriteCsv, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = write_csv(["1,2,3"], header="a,b,c")
    assert cli.main(["dedupe-medical", str(src), str(tmp_path / "o.csv")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: Expected columns ['Patient_ID', 'Diagnosis_Code_1'")
    assert err.count("\n") == 1
    assert "Traceback" not in err


def test_cli_missing_input(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "nope.csv"
    assert cli.main(["dedupe-medical", str(missing), str(tmp_path / "o.csv")]) == 1
    assert capsys.readouterr().err == f"error: input file not found: {missing}\n"


def test_cli_output_same_as_input(
    write_csv: WriteCsv, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = write_csv(["1,1,,,,d,5,6"], header=HEADER)
    before = src.read_bytes()
    assert cli.main(["dedupe-medical", str(src), str(src)]) == 1
    assert capsys.readouterr().err == "error: OUTPUT must be a different file from INPUT\n"
    assert src.read_bytes() == before


SAMPLE = Path(__file__).parents[1] / "data" / "medical_data_sample.csv"


def test_sample(tmp_path: Path) -> None:
    if not SAMPLE.is_file():
        pytest.skip("sample data missing")
    out = tmp_path / "out.csv"
    stats = dedupe_medical_data(SAMPLE, out)
    assert (stats.rows_in, stats.blank_rows, stats.rows_out, stats.duplicates_removed) == (
        30_000,
        0,
        28_285,
        1_715,
    )
    rows = read_rows(out)
    assert rows[0] == list(MEDICAL_COLUMNS)
    diagnosis = [i for i, name in enumerate(MEDICAL_COLUMNS) if name in DIAGNOSIS_COLUMNS]
    assert all(r[i] == r[i].strip() for r in rows[1:] for i in diagnosis)
    assert [sum(r[i] == "" for r in rows[1:]) for i in diagnosis] == [0, 12_140, 15_841, 18_860]
