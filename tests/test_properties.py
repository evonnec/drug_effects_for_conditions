"""Property tests over generated whole pharmacy CSV files."""

import csv
import io
import tempfile
from dataclasses import dataclass
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from drug_effects_for_conditions import CleanStats, dedupe_ndc_pharm_data, normalize_ndc

HEADER = ["Patient_ID", "Date_Submitted", "NDC", "Amount_Billed", "Amount_Paid"]
NDC_INDEX = HEADER.index("NDC")

SETTINGS = settings(max_examples=40, deadline=None)

type Row = list[str]

_ID_ALPHABET = st.characters(
    codec="utf-8", exclude_categories=["Cs", "Cc", "Zl", "Zp"], exclude_characters="\x85"
)
patient_ids = st.one_of(
    st.sampled_from(["P1", "P2", "007", "Zoë", "患者", "Doe, Jane", 'say "hi"']),
    st.text(_ID_ALPHABET | st.sampled_from([",", '"', "\n"]), min_size=1, max_size=6),
)
dates = st.sampled_from(["2020-01-01", "2020-01-02", "2021-12-31"])
amounts = st.sampled_from(["", "0", "10.00", "10.0", "7.50", "1,000.00"])


def ndc_forms(ndc11: str) -> list[str]:
    """Raw spellings that all normalize to `ndc11`."""
    dashed = f"{ndc11[:5]}-{ndc11[5:9]}-{ndc11[9:]}"
    stripped = ndc11.lstrip("0") or "0"
    return [ndc11, dashed, stripped, dashed.lstrip("0"), f"{stripped[:-2]}-{stripped[-2:]}"]


@st.composite
def ndc11_values(draw: st.DrawFn) -> str:
    pool = ["00002322730", "00093005801", "00000000005", "12345678901"]
    random_digits = st.integers(1, 11).flatmap(lambda n: st.integers(0, 10**n - 1))
    return draw(st.one_of(st.sampled_from(pool), random_digits.map(lambda i: f"{i:011d}")))


@st.composite
def base_rows(draw: st.DrawFn) -> Row:
    ndc = draw(ndc11_values())
    return [
        draw(patient_ids),
        draw(dates),
        draw(st.sampled_from(ndc_forms(ndc))),
        draw(amounts),
        draw(amounts),
    ]


@st.composite
def pharmacy_rows(draw: st.DrawFn) -> list[Row]:
    """Messy rows with deliberately injected duplicates and NDC-respelled duplicates."""
    rows = draw(st.lists(base_rows(), max_size=12))
    for _ in range(draw(st.integers(0, 6)) if rows else 0):
        source = list(draw(st.sampled_from(rows)))
        if draw(st.booleans()):
            source[NDC_INDEX] = draw(st.sampled_from(ndc_forms(normalize_ndc(source[NDC_INDEX]))))
        rows.insert(draw(st.integers(0, len(rows))), source)
    return rows


@dataclass(frozen=True)
class GeneratedCsv:
    rows: list[Row]
    data: bytes


@st.composite
def pharmacy_csvs(draw: st.DrawFn) -> GeneratedCsv:
    rows = draw(pharmacy_rows())
    terminator = draw(st.sampled_from(["\n", "\r\n"]))
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator=terminator)
    writer.writerow(HEADER)
    writer.writerows(rows)
    return GeneratedCsv(rows=rows, data=buffer.getvalue().encode("utf-8"))


def run(data: bytes) -> tuple[CleanStats, bytes]:
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp, "in.csv"), Path(tmp, "out.csv")
        src.write_bytes(data)
        stats = dedupe_ndc_pharm_data(src, out)
        return stats, out.read_bytes()


def parse(data: bytes) -> list[Row]:
    return list(csv.reader(io.StringIO(data.decode("utf-8"), newline="")))


def normalized(row: Row) -> Row:
    fixed = list(row)
    fixed[NDC_INDEX] = normalize_ndc(row[NDC_INDEX])
    return fixed


def first_occurrences(rows: list[Row]) -> list[Row]:
    return [list(t) for t in dict.fromkeys(tuple(normalized(r)) for r in rows)]


@SETTINGS
@given(pharmacy_csvs())
def test_every_output_row_is_a_normalized_input_row(case: GeneratedCsv) -> None:
    _, output = run(case.data)
    expected = {tuple(normalized(r)) for r in case.rows}
    assert all(tuple(row) in expected for row in parse(output)[1:])


@SETTINGS
@given(pharmacy_csvs())
def test_output_has_no_duplicate_rows(case: GeneratedCsv) -> None:
    _, output = run(case.data)
    body = [tuple(row) for row in parse(output)[1:]]
    assert len(body) == len(set(body))


@SETTINGS
@given(pharmacy_csvs())
def test_stats_balance(case: GeneratedCsv) -> None:
    stats, output = run(case.data)
    assert stats.rows_in == len(case.rows)
    assert stats.rows_out == len(parse(output)) - 1
    assert stats.rows_in == stats.rows_out + stats.duplicates_removed


@SETTINGS
@given(pharmacy_csvs())
def test_output_keeps_first_occurrence_order(case: GeneratedCsv) -> None:
    _, output = run(case.data)
    assert parse(output)[1:] == first_occurrences(case.rows)


@SETTINGS
@given(pharmacy_csvs())
def test_cleaning_is_idempotent(case: GeneratedCsv) -> None:
    _, once = run(case.data)
    stats, twice = run(once)
    assert twice == once
    assert stats.duplicates_removed == 0


@SETTINGS
@given(pharmacy_csvs())
def test_non_ndc_columns_round_trip(case: GeneratedCsv) -> None:
    _, output = run(case.data)
    kept = [i for i in range(len(HEADER)) if i != NDC_INDEX]
    got = {tuple(row[i].encode("utf-8") for i in kept) for row in parse(output)[1:]}
    want = {tuple(row[i].encode("utf-8") for i in kept) for row in case.rows}
    assert got == want


@SETTINGS
@given(pharmacy_csvs())
def test_output_always_has_the_header(case: GeneratedCsv) -> None:
    _, output = run(case.data)
    assert output.startswith((",".join(HEADER) + "\n").encode())
