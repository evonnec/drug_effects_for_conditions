import shutil
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest

import drug_effects_for_conditions as pkg


def test_version_matches_installed_metadata() -> None:
    assert pkg.__version__ == version("drug-effects-for-conditions")


@pytest.mark.parametrize("name", pkg.__all__)
def test_all_exports_resolve(name: str) -> None:
    assert hasattr(pkg, name)


def test_runs_from_source_without_metadata(tmp_path: Path) -> None:
    # A bare copy of the package: no dist-info or stray egg-info next to it, and -S hides
    # site-packages, so neither the installed metadata nor polars is importable.
    pkg_dir = Path(__file__).parents[1] / "src" / "drug_effects_for_conditions"
    shutil.copytree(pkg_dir, tmp_path / pkg_dir.name, ignore=shutil.ignore_patterns("__pycache__"))
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "import drug_effects_for_conditions as p; print(p.__version__);"
        "from drug_effects_for_conditions.cli import main; main(['--version'])"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code, str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "0+unknown\ndrug-effects-for-conditions 0+unknown\n"
