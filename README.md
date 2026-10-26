# drug-effects-for-conditions

Healthcare claims arrive as two separate record types: medical claims (visits, procedures,
diagnostics) and pharmacy claims (prescription drugs and therapies). The longer-term goal of this
project is to find patients who developed a condition `Y` after taking a drug `X`.

This package cleans both kinds of claims and then finds that cohort (see
[Finding the cohort](#finding-the-cohort)). For pharmacy claims
it removes rows that are exact duplicates, rejects rows whose National Drug Code (NDC) is invalid,
and normalizes the rest to the 11-digit format. For medical claims it strips the padding from the
diagnosis codes and removes exact duplicate rows. It is a small Python 3.15 package built on
[polars](https://pola.rs), with a command-line tool and a library API.

## Data

The `data/` directory holds four synthetic sample files. See [data/README.md](data/README.md) for
formats.

| File | Contents | Columns |
| --- | --- | --- |
| `pharmacy_data_sample.csv` | Pharmacy claims | `Patient_ID`, `Date_Submitted`, `NDC`, `Amount_Billed`, `Amount_Paid` |
| `medical_data_sample.csv` | Medical claims | `Patient_ID`, `Diagnosis_Code_1` to `Diagnosis_Code_4`, `Service_Date`, `Amount_Paid`, `Amount_Billed` |
| `ndc.csv` | NDCs for drug X | none (no header, one code per line) |
| `diagnosis_code.csv` | Diagnosis codes for condition Y | none (no header, one code per line) |

NDC rules, as implemented, applied in this order:

- Validate: with every dash removed, the NDC must be 1 to 11 ASCII digits (`0`-`9` only, no
  spaces, letters or other scripts' digits) and not all zeros. A missing or empty NDC is invalid.
  Invalid rows are not cleaned; they go to a rejects file with a `reason` column.
- Remove every dash.
- Left-pad with `0` to 11 digits.

A cleaned NDC is meant to match the format in `ndc.csv`.

Blank lines (every field empty or whitespace), including a trailing one, produce no output row and
are not counted as duplicates. An empty field is one value however it was written: `,,` and `,"",`
compare equal when de-duplicating, and both are written as `,,`.

A duplicate is a row identical to another row in every column. Rows are compared after NDC
normalization, so two rows whose NDCs differ only by dashes or leading zeros collapse into one if
every other column matches. The first occurrence is kept and row order is preserved.

Medical claims follow the same rules for blank lines, empty fields, duplicates and order, with
one difference: instead of NDC normalization, the four `Diagnosis_Code_*` columns are stripped of
leading and trailing whitespace before comparing, and a code that is only whitespace (the sample
pads unused slots with 8 spaces) is written as an empty field. `Patient_ID`, `Service_Date` and
both amounts are never altered. Rows that share patient, date and codes but differ in an amount
are separate claim lines and are kept. No rows are rejected.

## Quickstart

Install [uv](https://docs.astral.sh/uv/), then from a clone of this repository:

```
uv sync
uv run drug-effects-for-conditions dedupe-pharmacy data/pharmacy_data_sample.csv output/cleaned_pharm_data.csv
uv run drug-effects-for-conditions dedupe-medical data/medical_data_sample.csv output/cleaned_medical_data.csv
```

The `cohort` command, which needs no cleaned files, is described in [Finding the
cohort](#finding-the-cohort).

Each command prints its summary to stderr. For `dedupe-medical`:

```
read 30000 rows, wrote 28285 rows (1715 duplicates removed, 0 blank) to output/cleaned_medical_data.csv
```

`dedupe-medical` takes only the [partitioning options](#large-inputs---buckets-and---keep-order).
The rest of this section describes `dedupe-pharmacy`, apart
from [Errors and atomic writes](#errors-and-atomic-writes), which applies to all three commands.

The `dedupe-pharmacy` summary is:

```
read 32932 rows, wrote 30094 rows (2835 duplicates removed, 0 blank, 3 rejected) to output/cleaned_pharm_data.csv
wrote 3 rejected rows to output/cleaned_pharm_data.rejects.csv
```

The second line appears only when rows were rejected. The rejects file is always written, as a
header-only file when nothing was rejected. It has the input columns plus `reason`, and the first
lines on the sample data are:

```
Patient_ID,Date_Submitted,NDC,Amount_Billed,Amount_Paid,reason
1488,10/30/2015,0    -    -  ,77.95,0,NDC has characters other than digits and dashes
```

Options:

- `--rejects PATH` writes the rejects file to `PATH`. The default is the OUTPUT stem plus
  `.rejects.csv` next to OUTPUT (`output/cleaned_pharm_data.rejects.csv` above).
- `--strict` exits with code 3 if any row was rejected. The files are still written.
- `--buckets N` and `--keep-order`: see [Large inputs](#large-inputs---buckets-and---keep-order).

Missing parent directories of the output and rejects paths are created. Exit codes (`dedupe-medical` and `cohort` use 0, 1 and 2 only):

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Any error, printed as one `error:` line (see below) |
| 2 | Usage error (for example, no subcommand) |
| 3 | `--strict` and at least one row was rejected |

### Large inputs: `--buckets` and `--keep-order`

Both dedupe commands accept two options:

```
uv run drug-effects-for-conditions dedupe-pharmacy big.csv out.csv --buckets 64
uv run drug-effects-for-conditions dedupe-medical big.csv out.csv --buckets 64 --keep-order
```

By default one `unique` pass remembers every distinct row, so peak memory grows with the number
of distinct rows and the run cannot spill to disk. With `--buckets N` each row is hashed to one of
N buckets in a scratch directory (`.NAME.PID.tmp.PID.stage`, beside OUTPUT and always removed),
each bucket is de-duplicated alone, and the results are concatenated. Equal rows share a bucket, so
the output has exactly the rows of the default run. N must be at least 1.

- Without `--keep-order` the output rows are in bucket order (first occurrences within a bucket),
  not input order. The set of rows is the same.
- `--keep-order` sorts the survivors back into input order. That sort holds every distinct row in
  memory, so it gives up most of the memory saving; use it only when the order matters. Without
  `--buckets` it does nothing, because the default pass already keeps input order.

Use it when the default run is near the machine's memory. Measured on this repository's pharmacy
sample repeated to 20,022,656 rows (0.88 GB), 64 buckets, one run per configuration on a laptop
(see [Benchmark](#benchmark)); the 40M-row run needed 7.9 GB for the single pass, above the 4 GB
limit set for the table:

| Configuration | Wall (s) | Peak RSS (MB) |
| --- | ---: | ---: |
| single pass | 16.4 | 4,114 |
| `--buckets 64` | 22.8 | 4,146 |
| `--buckets 64 --keep-order` | 18.7 | 4,377 |
| partition phase alone | 11.5 | 3,182 |
| per-bucket dedupe phase alone | 11.2 | 459 |
| one `unique` over the staged Parquet | 2.8 | 2,320 |

The per-bucket phase peaks at 11% of the single pass (the target was under 25%). End to end, at
this size, the peak is not lower: reading the CSV and cleaning the rows (partition phase) costs
about as much memory as the single pass, and that part does not shrink with N. At 40M rows the
partitioned run peaked at 6.3 GB against 7.9 GB. The mode pays off when the distinct rows, not
the read, dominate memory. It is also slower on small inputs. Row counts come from the same run
as the write; OUTPUT is never read back.

### Benchmark

```
uv run python benchmarks/dedupe_benchmark.py --rows 20000000
```

It builds the input in a temp directory (the sample repeated with a distinct `Patient_ID` suffix),
runs each configuration in a subprocess, prints the table above and deletes its files. It is not
part of CI.

### Errors and atomic writes

Every command reports a failure as a single `error: ...` line on stderr, exits 1, writes nothing to
stdout and never prints a traceback. The causes are:

- a missing input file, or a wrong header (for the pharmacy, medical or reference schema);
- a claims file that is empty, is not valid UTF-8, has a stray quote, or has a row with too many or
  too few fields (the message names the file and the line, for example
  `error: short.csv: line 3 has 3 fields, fewer than the 5 columns of the header`);
- OUTPUT or the rejects path being an existing directory, or the same file as INPUT, including a
  hard link or symlink to it;
- anything the operating system refuses: an unreadable input, an OUTPUT in a directory that is not
  writable, an OUTPUT whose parent is a regular file, a full disk.

A row with fewer fields than the header is an error, not a row of nulls, but a row that ends in
empty fields (`1,d,5,,`) is fine and keeps its empty values. Blank lines are skipped.

Output files are written atomically. Each is first written to a hidden temp file beside it
(`.NAME.PID.tmp`) and renamed onto its final name only after every file of the command has been
written, so a failed run leaves an existing OUTPUT, and any rejects file, exactly as it was and
leaves no temp files behind. For `dedupe-pharmacy` the clean and rejects files are both written
before either is renamed. The input is never opened for writing, so an OUTPUT that is a link to
INPUT cannot truncate it. The rename replaces a symlink or hard link at OUTPUT with a regular file
and does not write through it.

`python -m drug_effects_for_conditions` is equivalent to the console script.

To use a built wheel instead of a clone, build it with `uv build` and run:

```
uvx --from dist/drug_effects_for_conditions-0.1.0-py3-none-any.whl drug-effects-for-conditions --version
pip install dist/drug_effects_for_conditions-0.1.0-py3-none-any.whl
```

Each GitHub Release includes the wheel and sdist as downloads.

## Finding the cohort

`cohort` finds the patients who developed condition `Y` after taking drug `X`. It cleans both
claims files in memory (no intermediate files), writes one row per qualifying patient to OUTPUT
and prints the counts to stderr. stdout stays empty.

```
uv run drug-effects-for-conditions cohort --pharmacy data/pharmacy_data_sample.csv --medical data/medical_data_sample.csv --ndc data/ndc.csv --codes data/diagnosis_code.csv output/cohort.csv
```

```
drug X patients:         759
condition Y patients:   3828
both:                    538
excluded by washout:       0
qualifying:              456
no follow-up:             30
wrote 456 rows to output/cohort.csv
```

`output/cohort.csv` has the columns `Patient_ID`, `first_x`, `first_y_after` and `days_to_y`,
sorted by `Patient_ID`. Dates are written as ISO `yyyy-mm-dd`:

```
Patient_ID,first_x,first_y_after,days_to_y
1000,2016-02-26,2016-05-04,68
1013,2016-02-04,2016-05-04,90
1029,2016-01-28,2016-04-22,85
```

### Definition

- **Drug X patient**: has a cleaned pharmacy fill whose NDC is in the `--ndc` list. The index date
  is their first such fill (`Date_Submitted`).
- **Condition Y claim**: a cleaned medical claim with a `--codes` code in any of the four
  `Diagnosis_Code_*` columns, dated by `Service_Date`. Several codes on one claim, or duplicate
  claims, are one event.
- **Qualifying patient**: a drug X patient with a Y claim after the index date. Each choice is a
  parameter:

| CLI flag | Library argument | Default | Effect |
| --- | --- | --- | --- |
| `--include-prevalent` | `incident=False` | incident | By default a patient with any Y claim on or before the index date (strictly before with `--same-day`) is excluded. The flag drops that rule: any Y after X counts. |
| `--same-day` | `same_day_counts=True` | off | Y must be strictly after the index date (`>`). With the flag, a Y on the index date counts (`>=`). |
| `--washout-days N` | `washout_days=N` | 0 | Keep only patients whose first X is at least N days after the earliest `Date_Submitted` in the pharmacy data. |

`first_y_after` is the earliest qualifying Y claim, and `days_to_y` is `first_y_after - first_x`.
Counts on the sample:

| Options | Qualifying |
| --- | --- |
| defaults | 456 |
| `--include-prevalent` | 478 |
| `--same-day` | 464 |
| `--include-prevalent --same-day` | 487 |
| `--washout-days 90` | 55 |

Each side is reduced to one row per patient (first X date, distinct Y dates) before the two are
joined, so fill rows are never joined to claim rows. Because only first dates matter, the
patient set is the same whether or not the inputs were de-duplicated; the CLI de-duplicates anyway.

Dates must be `m/d/yyyy` (a leading zero is fine, a two-digit year is not). They are parsed strictly:
an unparseable date in any row of either claims file, drug X or not, is an error and never a missing
value. A row with an empty date is ignored.

The summary lines mean:

- `drug X patients`: patients with any drug X fill. `condition Y patients`: patients with any Y
  claim. `both`: patients with each, in any order.
- `excluded by washout`: drug X patients dropped by `--washout-days`.
- `qualifying`: rows written.
- `no follow-up`: patients still eligible after the washout whose first X fill is after the last
  `Service_Date` in the medical data, so no Y could have been observed for them.

`--washout-days 90` on the sample prints `excluded by washout: 632` and `qualifying: 55`, out of 127
patients whose first X is on or after 2016-01-27.

### Data coverage caveats

The two sample files cover different periods: medical claims run from 2013-07-09 to 2016-05-19 and
pharmacy claims from 2015-10-29 to 2016-11-24. Pharmacy coverage starts late, so a patient's first
X fill in the data is often not their first ever. 424 of the 759 first fills are in the first 31
days of pharmacy coverage and 632 are before 2016-01-27. Without a washout, those patients count as
new users, and Y events in the months after them look like effects of a drug they may have taken
for years. `--washout-days` reduces this by dropping patients whose first fill is too close to the
start of pharmacy coverage. Medical coverage stops in May 2016, six months before the pharmacy data
ends, so late first fills (30 on the sample) have no follow-up.

Errors print one `error:` line and exit 1: a missing file, a wrong header, a malformed claims file,
an invalid reference file, an unparseable date, a negative `--washout-days`, OUTPUT being a
directory or the same file as any input, or a file system failure. OUTPUT is written atomically (see
[Errors and atomic writes](#errors-and-atomic-writes)).

```
error: input file not found: data/missing.csv
error: bad_ndc.csv: 1 invalid NDCs (expected ^[0-9]{11}$), for example '1234567890'
```

### Reference files

`ndc.csv` and `diagnosis_code.csv` are headerless. `load_ndc_list` and `load_diagnosis_codes`
read them as strings (so leading zeros survive), strip whitespace and keep distinct values in file
order. CRLF line endings and a missing final newline are fine. Each returns a LazyFrame with one
column, `NDC` or `Diagnosis_Code`. They raise `ReferenceFileError` (a `ValueError`, naming the
file and a sample of the bad values) when an NDC is not exactly 11 digits, when a code is not 3 to 7
characters of `0-9` and `A-Z`, or when the file is empty or has blank lines. A missing file raises
`FileNotFoundError`. The file is read once to validate it, which is fine for files this small.
`data/ndc.csv` gives 404 NDCs (166 starting with `0`) and `data/diagnosis_code.csv` gives 146 codes.

## Library use

```python
from drug_effects_for_conditions import (
    clean_medical,
    clean_pharmacy,
    dedupe_medical_data,
    dedupe_ndc_pharm_data,
    scan_medical,
    scan_pharmacy,
)

# Whole pipeline: read, clean, write the clean and rejects files. Returns row counts.
stats = dedupe_ndc_pharm_data("data/pharmacy_data_sample.csv", "output/cleaned_pharm_data.csv")
print(stats.rows_in, stats.blank_rows, stats.rejected, stats.rows_out, stats.duplicates_removed)

# For inputs near memory size: buckets=N de-duplicates in N hash partitions, one at a time.
# keep_order=True restores input order; without buckets it does nothing. buckets < 1 is a
# ValueError. dedupe_medical_data takes the same keywords.
stats = dedupe_ndc_pharm_data(
    "data/pharmacy_data_sample.csv", "output/cleaned_pharm_data.csv", buckets=64
)

# Or work lazily and keep the results as polars LazyFrames.
frame = scan_pharmacy("data/pharmacy_data_sample.csv")  # checks the header, reads strings
result = clean_pharmacy(frame)  # drop blanks, split off invalid NDCs, normalize, unique rows
print(result.clean.collect().height, result.rejects.collect().height)
```

Medical claims work the same way, without rejects:

```python
stats = dedupe_medical_data("data/medical_data_sample.csv", "output/cleaned_medical_data.csv")
print(stats.rows_in, stats.blank_rows, stats.rows_out, stats.duplicates_removed)

clean = clean_medical(scan_medical("data/medical_data_sample.csv"))  # a LazyFrame
print(clean.collect().height)
```

`scan_pharmacy` raises `FileNotFoundError` for a missing file and `PharmacySchemaError` if the
header does not match `PHARMACY_COLUMNS`. `scan_medical` does the same with `MedicalSchemaError`
and `MEDICAL_COLUMNS` (`DIAGNOSIS_COLUMNS` is the four diagnosis columns). Both errors subclass
`ClaimsSchemaError`, which is a `ValueError`; `scan_claims(path, columns)` is the shared reader. `dedupe_ndc_pharm_data` takes an optional third argument,
the rejects path. `clean_pharmacy` returns a frozen `CleanResult(clean, rejects)`, and
`duplicates_removed` is `rows_in - blank_rows - rejected - rows_out`.

A claims file that is empty, is not UTF-8 or has a row with the wrong number of fields raises
`ClaimsFileError` (a `ValueError` naming the file) from `scan_pharmacy` and `scan_medical`, or from
the dedupe functions for a problem polars finds while reading, such as a stray quote. The dedupe
functions write atomically, and `write_csv_atomic(frame, path)` does the same for any LazyFrame.
Other failures, such as an unwritable OUTPUT, stay `OSError`.

The cohort functions take the cleaned frames and the reference frames, and return LazyFrames
(`summarize` returns a frozen `CohortSummary`):

```python
from drug_effects_for_conditions import (
    clean_medical,
    clean_pharmacy,
    developed_after,
    load_diagnosis_codes,
    load_ndc_list,
    scan_medical,
    scan_pharmacy,
    summarize,
)

pharmacy = clean_pharmacy(scan_pharmacy("data/pharmacy_data_sample.csv")).clean
medical = clean_medical(scan_medical("data/medical_data_sample.csv"))
ndc_list = load_ndc_list("data/ndc.csv")
codes = load_diagnosis_codes("data/diagnosis_code.csv")

cohort = developed_after(
    pharmacy, medical, ndc_list, codes, incident=True, same_day_counts=False, washout_days=0
)
print(cohort.collect().height)  # 456
print(summarize(pharmacy, medical, ndc_list, codes))
```

`first_exposure(pharmacy, ndc_list)` returns `Patient_ID` and `first_x`, `condition_events(medical,
codes)` returns distinct `Patient_ID` and `event_date`, and `match_ndc(pharmacy, ndc_list)` is the
semi join of pharmacy rows on `NDC`. `CohortSummary` has `drug_x`, `condition_y`, `both`,
`washout_excluded`, `eligible`, `qualifying` and `no_followup`. A date that is not `m/d/yyyy` raises
a polars `InvalidOperationError` when the frame is collected.

`normalize_ndc` (plain strings) and `normalize_ndc_expr` (polars expression) only normalize and
never validate. Validity is separate: `is_valid_ndc` (plain strings) and `ndc_is_valid_expr`
(polars expression) agree on every input.

## Project layout

```
.
├── src/drug_effects_for_conditions/
│   ├── __init__.py     public API and __version__
│   ├── __main__.py     python -m entry point
│   ├── claims.py       shared scan, blank-row, unique and partitioned-unique helpers
│   ├── cli.py          argparse CLI
│   ├── cohort.py       drug X then condition Y cohort and its counts
│   ├── medical.py      scan, clean and de-duplicate medical CSVs
│   ├── ndc.py          NDC normalization and validation
│   ├── pharmacy.py     scan, clean and de-duplicate pharmacy CSVs
│   └── reference.py    load the NDC and diagnosis-code lists, match NDCs
├── benchmarks/         manual de-duplication benchmark (not in CI)
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
