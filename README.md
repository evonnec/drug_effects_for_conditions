# drug-effects-for-conditions

Healthcare claims arrive as two separate record types: medical claims (visits, procedures,
diagnostics) and pharmacy claims (prescription drugs and therapies). The longer-term goal of this
project is to find patients who developed a condition `Y` after taking a drug `X`.

This package currently covers the first step: cleaning pharmacy claims. It removes rows that are
exact duplicates and normalizes National Drug Codes (NDCs) to the 11-digit format. It is a small
Python 3.15 package built on [polars](https://pola.rs), with a command-line tool and a library API.

## Data

The `data/` directory holds four synthetic sample files. See [data/README.md](data/README.md) for
formats.

| File | Contents | Columns |
| --- | --- | --- |
| `pharmacy_data_sample.csv` | Pharmacy claims | `Patient_ID`, `Date_Submitted`, `NDC`, `Amount_Billed`, `Amount_Paid` |
| `medical_data_sample.csv` | Medical claims | `Patient_ID`, `Diagnosis_Code_1` to `Diagnosis_Code_4`, `Service_Date`, `Amount_Paid`, `Amount_Billed` |
| `ndc.csv` | NDCs for drug X | none (no header, one code per line) |
| `diagnosis_code.csv` | Diagnosis codes for condition Y | none (no header, one code per line) |

NDC rules, as implemented:

- Remove every dash.
- Left-pad with `0` to 11 digits.

These are the only two issues handled. A cleaned NDC is meant to match the format in `ndc.csv`.

A duplicate is a row identical to another row in every column. Rows are compared after NDC
normalization, so two rows whose NDCs differ only by dashes or leading zeros collapse into one if
every other column matches. The first occurrence is kept and row order is preserved.

## Quickstart

Install [uv](https://docs.astral.sh/uv/), then from a clone of this repository:

```
uv sync
uv run drug-effects-for-conditions dedupe-pharmacy data/pharmacy_data_sample.csv output/cleaned_pharm_data.csv
```

The summary goes to stderr:

```
read 32932 rows, wrote 30097 rows (2835 duplicates removed) to output/cleaned_pharm_data.csv
```

Missing parent directories of the output path are created. Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Input file not found, header is not the pharmacy schema, or output is the same file as input |
| 2 | Usage error (for example, no subcommand) |

`python -m drug_effects_for_conditions` is equivalent to the console script.

To use a built wheel instead of a clone, build it with `uv build` and run:

```
uvx --from dist/drug_effects_for_conditions-0.1.0-py3-none-any.whl drug-effects-for-conditions --version
pip install dist/drug_effects_for_conditions-0.1.0-py3-none-any.whl
```

Each GitHub Release includes the wheel and sdist as downloads.

## Library use

```python
from drug_effects_for_conditions import clean_pharmacy, dedupe_ndc_pharm_data, scan_pharmacy

# Whole pipeline: read, clean, write. Returns row counts.
stats = dedupe_ndc_pharm_data("data/pharmacy_data_sample.csv", "output/cleaned_pharm_data.csv")
print(stats.rows_in, stats.rows_out, stats.duplicates_removed)

# Or work lazily and keep the result as a polars LazyFrame.
frame = scan_pharmacy("data/pharmacy_data_sample.csv")  # checks the header, reads strings
cleaned = clean_pharmacy(frame)  # normalize NDCs, then unique rows
print(cleaned.collect().height)
```

`scan_pharmacy` raises `FileNotFoundError` for a missing file and `PharmacySchemaError` if the
header does not match `PHARMACY_COLUMNS`. `normalize_ndc` (plain strings) and `normalize_ndc_expr`
(polars expression) are also exported.

## Project layout

```
.
├── src/drug_effects_for_conditions/
│   ├── __init__.py     public API and __version__
│   ├── __main__.py     python -m entry point
│   ├── cli.py          argparse CLI
│   ├── ndc.py          NDC normalization
│   └── pharmacy.py     scan, clean and de-duplicate pharmacy CSVs
├── tests/              pytest and hypothesis tests
├── data/               synthetic sample files
├── docs/               design notes
└── .github/workflows/ci.yml
```

## Development

The Python version (3.15) comes from `.python-version`; uv downloads it if needed.

```
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
uv run mypy
uv build
```

Design notes on testing, scaling and data modelling are in
[docs/design-notes.md](docs/design-notes.md).

## CI and releases

`.github/workflows/ci.yml` runs on pushes to `main`, on pull requests, on `v*` tags and on manual
dispatch. Jobs:

1. `lint`: `uv lock --check`, then `ruff check`. Runs in parallel with the next three.
2. `format`: `ruff format --check`.
3. `typecheck`: `mypy`.
4. `test`: `pytest`, with a JUnit report uploaded.
5. `build`: wheel and sdist via `uv build --no-sources`, smoke-tested in an isolated environment
   (import, `--version`, and the CLI on the sample data), uploaded as the `dist` artifact.
6. `release`: tag pushes only. Creates a GitHub Release with the wheel and sdist.
7. `publish-pypi`: tag pushes only, and only when the repository variable `PUBLISH_TO_PYPI` is
   `true`.

To cut a release:

```
uv version --bump patch   # or minor, or major
git commit -am "Release vX.Y.Z"
git tag vX.Y.Z
git push origin main vX.Y.Z
```

The tag must be `vMAJOR.MINOR.PATCH` and equal the version in `pyproject.toml`, or the release
job fails. A suffix `aN`, `bN` or `rcN` (for example `v1.2.0rc1`) marks a pre-release.

PyPI publishing is off by default. To enable it, once:

1. On PyPI, add a pending trusted publisher for GitHub Actions with owner `evonnec`, repository
   `drug_effects_for_conditions`, workflow `ci.yml` and environment `pypi`.
2. In the repository settings, create the `pypi` environment, ideally limited to `v*` tags with
   required reviewers.
3. Set the repository variable `PUBLISH_TO_PYPI` to `true`.

## License

GPL-3.0-only. See [LICENSE](LICENSE).
