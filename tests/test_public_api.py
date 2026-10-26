from importlib.metadata import version

import pytest

import drug_effects_for_conditions as pkg


def test_version_matches_installed_metadata() -> None:
    assert pkg.__version__ == version("drug-effects-for-conditions")


@pytest.mark.parametrize("name", pkg.__all__)
def test_all_exports_resolve(name: str) -> None:
    assert hasattr(pkg, name)
