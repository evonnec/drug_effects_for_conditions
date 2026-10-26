import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pytest

from drug_effects_for_conditions import cli
from drug_effects_for_conditions.pharmacy import PharmacySchemaError

type MakeLink = Callable[[Literal["hard", "sym"], Path, Path], None]

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
    assert (
        captured.err
        == f"read 3 rows, wrote 2 rows (1 duplicates removed, 0 blank, 0 rejected) to {out}\n"
    )
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
    def boom(input_path: object, output_path: object, rejects_path: object) -> None:
        raise PharmacySchemaError("bad header")

    monkeypatch.setattr(cli, "dedupe_ndc_pharm_data", boom)
    src = write_csv(tmp_path / "in.csv")
    assert cli.main(["dedupe-pharmacy", str(src), str(tmp_path / "o.csv")]) == 1
    assert capsys.readouterr().err == "error: bad header\n"


def test_same_input_and_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = write_csv(tmp_path / "in.csv")
    before = src.read_bytes()
    # pathlib drops "." segments, so `tmp_path / "." / "in.csv"` is not an alias; ".." is kept.
    (tmp_path / "sub").mkdir()
    alias = tmp_path / "sub" / ".." / "in.csv"
    assert str(alias) != str(src)
    assert cli.main(["dedupe-pharmacy", str(src), str(alias)]) == 1
    assert capsys.readouterr().err.startswith("error: ")
    assert src.read_bytes() == before


def test_hard_link_to_input_is_rejected(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], make_link: MakeLink
) -> None:
    src = write_csv(tmp_path / "in.csv")
    before = src.read_bytes()
    link = tmp_path / "link.csv"
    make_link("hard", src, link)
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


def test_python_dash_m_propagates_the_exit_code(tmp_path: Path) -> None:
    # `--help` exits through argparse's own SystemExit, so it cannot tell whether __main__.py
    # passes main()'s return value on. A failing run can.
    missing = tmp_path / "nope.csv"
    argv = ["dedupe-pharmacy", str(missing), str(tmp_path / "o.csv")]
    result = subprocess.run(
        [sys.executable, "-m", "drug_effects_for_conditions", *argv],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert result.stderr == f"error: input file not found: {missing}\n"


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


def write_with_reject(path: Path) -> Path:
    path.write_text(HEADER + ROWS + "3,2020-01-03,ABC,1.00,2.00\n")
    return path


def test_rejects_default_path_and_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src, out = write_with_reject(tmp_path / "in.csv"), tmp_path / "out.csv"
    assert cli.main(["dedupe-pharmacy", str(src), str(out)]) == 0
    rejects = tmp_path / "out.rejects.csv"
    assert capsys.readouterr().err == (
        f"read 3 rows, wrote 2 rows (0 duplicates removed, 0 blank, 1 rejected) to {out}\n"
        f"wrote 1 rejected rows to {rejects}\n"
    )
    assert rejects.read_text().splitlines() == [
        "Patient_ID,Date_Submitted,NDC,Amount_Billed,Amount_Paid,reason",
        "3,2020-01-03,ABC,1.00,2.00,NDC has characters other than digits and dashes",
    ]


def test_rejects_file_is_written_with_header_when_nothing_is_rejected(tmp_path: Path) -> None:
    src, out = write_csv(tmp_path / "in.csv"), tmp_path / "out.csv"
    assert cli.main(["dedupe-pharmacy", str(src), str(out)]) == 0
    header = "Patient_ID,Date_Submitted,NDC,Amount_Billed,Amount_Paid,reason"
    assert (tmp_path / "out.rejects.csv").read_text() == header + "\n"


@pytest.mark.parametrize(("extra", "code"), [([], 0), (["--strict"], 3)])
def test_strict_exit_code(tmp_path: Path, extra: list[str], code: int) -> None:
    src, out = write_with_reject(tmp_path / "in.csv"), tmp_path / "out.csv"
    assert cli.main(["dedupe-pharmacy", *extra, str(src), str(out)]) == code
    assert out.is_file()  # --strict changes the exit code, not what is written


def test_custom_rejects_path_parent_is_created(tmp_path: Path) -> None:
    src, out = write_with_reject(tmp_path / "in.csv"), tmp_path / "out.csv"
    rejects = tmp_path / "new" / "dir" / "bad.csv"
    assert cli.main(["dedupe-pharmacy", "--rejects", str(rejects), str(src), str(out)]) == 0
    assert rejects.is_file()


def test_strict_succeeds_without_rejects(tmp_path: Path) -> None:
    src, out = write_csv(tmp_path / "in.csv"), tmp_path / "out.csv"
    assert cli.main(["dedupe-pharmacy", "--strict", str(src), str(out)]) == 0


@pytest.mark.parametrize("target", ["input", "output"])
def test_rejects_path_must_differ_from_input_and_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], target: str
) -> None:
    src, out = write_with_reject(tmp_path / "in.csv"), tmp_path / "out.csv"
    before = src.read_text()
    rejects = src if target == "input" else out
    assert cli.main(["dedupe-pharmacy", "--rejects", str(rejects), str(src), str(out)]) == 1
    assert capsys.readouterr().err.startswith("error: rejects file must be different from")
    assert src.read_text() == before
