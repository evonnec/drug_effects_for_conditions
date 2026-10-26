import os
import subprocess
import sys
from pathlib import Path

import pytest

from drug_effects_for_conditions import cli
from drug_effects_for_conditions.pharmacy import PharmacySchemaError

HEADER = "Patient_ID,Date_Submitted,NDC,Amount_Billed,Amount_Paid\n"
ROWS = "1,2020-01-01,0002-3227-30,10.00,5.00\n2,2020-01-02,00093-0058-01,7.50,3.00\n"


def write_csv(path: Path, *, duplicate: bool = True, header: str = HEADER) -> Path:
    first = ROWS.splitlines(keepends=True)[0]
    path.write_text(header + ROWS + (first if duplicate else ""))
    return path


def test_dedupe_success(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = write_csv(tmp_path / "in.csv")
    out = tmp_path / "out.csv"
    assert cli.main(["dedupe-pharmacy", str(src), str(out)]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"read 3 rows, wrote 2 rows (1 duplicates removed) to {out}\n"
    assert out.is_file()


def test_missing_input(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "nope.csv"
    assert cli.main(["dedupe-pharmacy", str(missing), str(tmp_path / "o.csv")]) == 1
    assert capsys.readouterr().err == f"error: input file not found: {missing}\n"


def test_schema_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = write_csv(tmp_path / "in.csv", header="a,b,c\n")
    assert cli.main(["dedupe-pharmacy", str(src), str(tmp_path / "o.csv")]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("error: ")
    assert "Traceback" not in captured.err


def test_schema_error_message_is_passed_through(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(input_path: object, output_path: object) -> None:
        raise PharmacySchemaError("bad header")

    monkeypatch.setattr(cli, "dedupe_ndc_pharm_data", boom)
    src = write_csv(tmp_path / "in.csv")
    assert cli.main(["dedupe-pharmacy", str(src), str(tmp_path / "o.csv")]) == 1
    assert capsys.readouterr().err == "error: bad header\n"


def test_same_input_and_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = write_csv(tmp_path / "in.csv")
    before = src.read_bytes()
    alias = tmp_path / "." / "in.csv"
    assert cli.main(["dedupe-pharmacy", str(src), str(alias)]) == 1
    assert capsys.readouterr().err.startswith("error: ")
    assert src.read_bytes() == before


def test_hard_link_to_input_is_rejected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = write_csv(tmp_path / "in.csv")
    before = src.read_bytes()
    link = tmp_path / "link.csv"
    os.link(src, link)
    assert cli.main(["dedupe-pharmacy", str(src), str(link)]) == 1
    assert capsys.readouterr().err.startswith("error: ")
    assert src.read_bytes() == before


def test_nested_output_dir_created(tmp_path: Path) -> None:
    src = write_csv(tmp_path / "in.csv")
    out = tmp_path / "a" / "b" / "out.csv"
    assert cli.main(["dedupe-pharmacy", str(src), str(out)]) == 0
    assert out.is_file()


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("drug-effects-for-conditions ")


def test_no_subcommand_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_python_dash_m_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "drug_effects_for_conditions", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "dedupe-pharmacy" in result.stdout


def test_help_and_version_do_not_import_polars() -> None:
    code = (
        "import sys\n"
        "from drug_effects_for_conditions.cli import main\n"
        "for argv in (['--help'], ['--version']):\n"
        "    try:\n"
        "        main(argv)\n"
        "    except SystemExit:\n"
        "        pass\n"
        "assert 'polars' not in sys.modules, 'polars was imported'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
