import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import polars as pl
import pytest

from drug_effects_for_conditions import cli

type CleanFailure = Callable[[int], str]

DATA = Path(__file__).parents[1] / "data"
PHARMACY = DATA / "pharmacy_data_sample.csv"
MEDICAL = DATA / "medical_data_sample.csv"
NDC = DATA / "ndc.csv"
CODES = DATA / "diagnosis_code.csv"

PHARMACY_HEADER = "Patient_ID,Date_Submitted,NDC,Amount_Billed,Amount_Paid\n"
MEDICAL_HEADER = (
    "Patient_ID,Diagnosis_Code_1,Diagnosis_Code_2,Diagnosis_Code_3,Diagnosis_Code_4,"
    "Service_Date,Amount_Paid,Amount_Billed\n"
)


def argv(output: Path, *extra: str, **overrides: Path) -> list[str]:
    paths = {"pharmacy": PHARMACY, "medical": MEDICAL, "ndc": NDC, "codes": CODES} | overrides
    flags = [part for name, path in paths.items() for part in (f"--{name}", str(path))]
    return ["cohort", *flags, *extra, str(output)]


def counts(stderr: str) -> dict[str, int]:
    pairs = (line.rsplit(":", 1) for line in stderr.splitlines() if ":" in line)
    return {label.strip(): int(value) for label, value in pairs}


def test_sample_defaults(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "nested" / "cohort.csv"
    assert cli.main(argv(out)) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert counts(captured.err) == {
        "drug X patients": 759,
        "condition Y patients": 3828,
        "both": 538,
        "excluded by washout": 0,
        "qualifying": 456,
        "no follow-up": 30,
    }
    assert captured.err.splitlines()[-1] == f"wrote 456 rows to {out}"
    frame = pl.read_csv(out, schema_overrides={"Patient_ID": pl.String})
    assert frame.columns == ["Patient_ID", "first_x", "first_y_after", "days_to_y"]
    assert frame.height == 456
    assert frame["Patient_ID"].is_sorted()


@pytest.mark.parametrize(
    ("flags", "qualifying"),
    [
        (["--include-prevalent"], 478),
        (["--same-day"], 464),
        (["--include-prevalent", "--same-day"], 487),
    ],
)
def test_sample_variants(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], flags: list[str], qualifying: int
) -> None:
    out = tmp_path / "cohort.csv"
    assert cli.main(argv(out, *flags)) == 0
    assert counts(capsys.readouterr().err)["qualifying"] == qualifying
    assert pl.read_csv(out).height == qualifying


def test_sample_washout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(argv(tmp_path / "c.csv", "--washout-days", "90")) == 0
    result = counts(capsys.readouterr().err)
    assert result["qualifying"] == 55
    assert result["excluded by washout"] == 632


def test_cohort_help_does_not_import_polars() -> None:
    code = (
        "import sys\n"
        "from drug_effects_for_conditions.cli import main\n"
        "try:\n"
        "    main(['cohort', '--help'])\n"
        "except SystemExit:\n"
        "    pass\n"
        "assert 'polars' not in sys.modules, 'polars was imported'\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "--washout-days" in result.stdout


@pytest.mark.parametrize("name", ["pharmacy", "medical", "ndc", "codes"])
def test_missing_input(tmp_path: Path, assert_clean_failure: CleanFailure, name: str) -> None:
    missing = tmp_path / "nope.csv"
    code = cli.main(argv(tmp_path / "out.csv", **{name: missing}))
    assert assert_clean_failure(code) == f"error: input file not found: {missing}"
    assert not (tmp_path / "out.csv").exists()


@pytest.mark.parametrize("name", ["pharmacy", "medical", "ndc", "codes"])
def test_output_same_as_an_input(
    tmp_path: Path, assert_clean_failure: CleanFailure, name: str
) -> None:
    src = tmp_path / "in.csv"
    src.write_bytes(
        {"pharmacy": PHARMACY, "medical": MEDICAL, "ndc": NDC, "codes": CODES}[name].read_bytes()
    )
    before = src.read_bytes()
    assert_clean_failure(cli.main(argv(src, **{name: src})))
    assert src.read_bytes() == before


def test_claims_schema_error(tmp_path: Path, assert_clean_failure: CleanFailure) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b,c\n1,2,3\n")
    line = assert_clean_failure(cli.main(argv(tmp_path / "o.csv", pharmacy=bad)))
    assert "Expected columns" in line
    line = assert_clean_failure(cli.main(argv(tmp_path / "o.csv", medical=bad)))
    assert "Expected columns" in line


@pytest.mark.parametrize("name", ["ndc", "codes"])
@pytest.mark.parametrize("content", ["", "12\n", "not a code\n"])
def test_reference_file_error(
    tmp_path: Path, assert_clean_failure: CleanFailure, name: str, content: str
) -> None:
    bad = tmp_path / "ref.csv"
    bad.write_text(content)
    line = assert_clean_failure(cli.main(argv(tmp_path / "o.csv", **{name: bad})))
    assert "ref.csv" in line


def test_unparseable_pharmacy_date(tmp_path: Path, assert_clean_failure: CleanFailure) -> None:
    pharmacy = tmp_path / "p.csv"
    pharmacy.write_text(
        PHARMACY_HEADER + "1,2016-01-05,00000000005,1,1\n1,1/5/2016,00000000006,1,1\n"
    )
    out = tmp_path / "o.csv"
    line = assert_clean_failure(cli.main(argv(out, pharmacy=pharmacy)))
    assert "m/d/yyyy" in line
    assert not out.exists()


def test_unparseable_medical_date(tmp_path: Path, assert_clean_failure: CleanFailure) -> None:
    medical = tmp_path / "m.csv"
    medical.write_text(MEDICAL_HEADER + "1,9592,,,,13/45/2016,1,1\n")
    line = assert_clean_failure(cli.main(argv(tmp_path / "o.csv", medical=medical)))
    assert "m/d/yyyy" in line


def test_negative_washout(tmp_path: Path, assert_clean_failure: CleanFailure) -> None:
    assert_clean_failure(cli.main(argv(tmp_path / "o.csv", "--washout-days", "-1")))


def test_duplicates_in_inputs_do_not_change_the_cohort(tmp_path: Path) -> None:
    def doubled(path: Path, name: str) -> Path:
        lines = path.read_text().splitlines(keepends=True)
        out = tmp_path / name
        out.write_text("".join(lines) + "\n" + "".join(lines[1:]))
        return out

    plain, dup = tmp_path / "plain.csv", tmp_path / "dup.csv"
    assert cli.main(argv(plain)) == 0
    code = cli.main(
        argv(dup, pharmacy=doubled(PHARMACY, "p.csv"), medical=doubled(MEDICAL, "m.csv"))
    )
    assert code == 0
    assert dup.read_bytes() == plain.read_bytes()
