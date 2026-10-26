import csv
from collections.abc import Callable
from pathlib import Path

import pytest

from drug_effects_for_conditions.pharmacy import (
    PHARMACY_COLUMNS,
    CleanStats,
    PharmacySchemaError,
    dedupe_ndc_pharm_data,
    scan_pharmacy,
)

type WriteCsv = Callable[..., Path]


def read_rows(path: Path) -> list[list[str]]:
    with path.open(newline="") as f:
        return list(csv.reader(f))


def test_exact_duplicates_collapse(write_csv: WriteCsv, tmp_path: Path) -> None:
    src = write_csv(["1,1/1/2016,00591-0845-10,5,4", "1,1/1/2016,00591-0845-10,5,4"])
    out = tmp_path / "out.csv"
    dedupe_ndc_pharm_data(src, out)
    assert read_rows(out)[1:] == [["1", "1/1/2016", "00591084510", "5", "4"]]


def test_ndc_formatting_variants_collapse(write_csv: WriteCsv, tmp_path: Path) -> None:
    src = write_csv(["1,1/1/2016,591-0845-10,5,4", "1,1/1/2016,00591084510,5,4"])
    out = tmp_path / "out.csv"
    stats = dedupe_ndc_pharm_data(src, out)
    assert len(read_rows(out)) == 2
    assert stats.duplicates_removed == 1


def test_first_occurrence_order_and_header(write_csv: WriteCsv, tmp_path: Path) -> None:
    src = write_csv(["3,d,1,1,1", "1,d,2,1,1", "3,d,1,1,1", "2,d,3,1,1"])
    out = tmp_path / "out.csv"
    dedupe_ndc_pharm_data(src, out)
    rows = read_rows(out)
    assert rows[0] == list(PHARMACY_COLUMNS)
    assert [r[0] for r in rows[1:]] == ["3", "1", "2"]


def test_first_occurrence_order_at_scale(tmp_path: Path) -> None:
    # Small inputs keep their order even without maintain_order=True; this size does not.
    n = 300_000
    ids = [(i * 7_919) % n for i in range(n)]  # a fixed permutation of 0..n-1
    rows = [f"{i},1/1/2016,1-2,1,1" for i in ids]
    src = tmp_path / "in.csv"
    src.write_text("\n".join([",".join(PHARMACY_COLUMNS), *rows, *rows[::3]]) + "\n")
    out = tmp_path / "out.csv"
    stats = dedupe_ndc_pharm_data(src, out)
    assert stats.rows_out == n
    assert [r[0] for r in read_rows(out)[1:]] == [str(i) for i in ids]


def test_values_round_trip(write_csv: WriteCsv, tmp_path: Path) -> None:
    src = write_csv(["007,1/2/2016,1-2,54.90,0"])
    out = tmp_path / "out.csv"
    dedupe_ndc_pharm_data(src, out)
    assert read_rows(out)[1] == ["007", "1/2/2016", "00000000012", "54.90", "0"]


@pytest.mark.parametrize(
    "header",
    [
        "Patient_ID,Date_Submitted,NDC,Amount_Billed",
        "A,B,C,D,E",
        "NDC,Patient_ID,Date_Submitted,Amount_Billed,Amount_Paid",
    ],
)
def test_wrong_header(write_csv: WriteCsv, header: str) -> None:
    src = write_csv(["1,2,3,4,5"], header=header)
    with pytest.raises(PharmacySchemaError, match="Expected columns"):
        scan_pharmacy(src)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        scan_pharmacy(tmp_path / "nope.csv")


def test_stats(write_csv: WriteCsv, tmp_path: Path) -> None:
    src = write_csv(["1,d,1,1,1", "1,d,1,1,1", "2,d,2,1,1"])
    stats = dedupe_ndc_pharm_data(src, tmp_path / "out.csv")
    assert stats == CleanStats(rows_in=3, rows_out=2, blank_rows=0, rejected=0)
    assert stats.duplicates_removed == 1


def test_header_only(write_csv: WriteCsv, tmp_path: Path) -> None:
    src = write_csv([])
    out = tmp_path / "out.csv"
    stats = dedupe_ndc_pharm_data(src, out)
    assert stats == CleanStats(rows_in=0, rows_out=0)
    assert read_rows(out) == [list(PHARMACY_COLUMNS)]


SAMPLE = Path(__file__).parents[1] / "data" / "pharmacy_data_sample.csv"
# Output of the original stdlib-csv implementation on the sample (no header, set order).
LEGACY = Path(__file__).parent / "fixtures" / "legacy_cleaned_pharm_data.csv"


def test_sample_parity(tmp_path: Path) -> None:
    # The legacy output minus patient 1488's three invalid-NDC claims (see the test below).
    if not SAMPLE.is_file():
        pytest.skip("sample data missing")
    out = tmp_path / "out.csv"
    stats = dedupe_ndc_pharm_data(SAMPLE, out)
    new_rows = {tuple(r) for r in read_rows(out)[1:]}
    old_rows = {tuple(r) for r in read_rows(LEGACY) if not (r[0] == "1488" and r[2].strip() == "0")}
    assert new_rows == old_rows
    assert stats.rows_out == len(new_rows) == 30_094


def test_sample_after_rejecting_invalid_ndcs(tmp_path: Path) -> None:
    if not SAMPLE.is_file():
        pytest.skip("sample data missing")
    out = tmp_path / "out.csv"
    stats = dedupe_ndc_pharm_data(SAMPLE, out)
    assert (stats.rows_in, stats.rejected, stats.rows_out, stats.duplicates_removed) == (
        32_932,
        3,
        30_094,
        2_835,
    )
    # Exactly the legacy rows minus patient 1488's three `0    -    -  ` claims, nothing else.
    invalid = {r for r in map(tuple, read_rows(LEGACY)) if r[0] == "1488" and r[2].strip() == "0"}
    assert len(invalid) == 3
    expected = {tuple(r) for r in read_rows(LEGACY)} - invalid
    assert {tuple(r) for r in read_rows(out)[1:]} == expected
