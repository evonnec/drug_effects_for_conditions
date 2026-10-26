"""Property tests over generated whole pharmacy CSV files."""

import csv
import io
import tempfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from hypothesis import example, given, settings
from hypothesis import strategies as st

from drug_effects_for_conditions import (
    MEDICAL_COLUMNS,
    CleanStats,
    dedupe_medical_data,
    dedupe_ndc_pharm_data,
    normalize_ndc,
)

HEADER = ["Patient_ID", "Date_Submitted", "NDC", "Amount_Billed", "Amount_Paid"]
NDC_INDEX = HEADER.index("NDC")

# Deadlines come from the profile in conftest.py.
SETTINGS = settings(max_examples=75)

type Row = list[str]

_ID_ALPHABET = st.characters(
    codec="utf-8", exclude_categories=["Cs", "Cc", "Zl", "Zp"], exclude_characters="\x85"
)
# Values a "helpful" reader option would rewrite: null markers (null_values), a leading '#'
# (comment_prefix), padding (whitespace stripping), leading zeros (schema inference) and a field
# far longer than any read buffer.
_TRICKY_TEXT = ["NA", "null", "#7", " 7 ", "007", "x" * 5_000]
patient_ids = st.one_of(
    st.sampled_from(["P1", "P2", "Zoë", "患者", "Doe, Jane", 'say "hi"', "a\r\nb", *_TRICKY_TEXT]),
    # CSV-special pieces, including line breaks that must survive inside quotes.
    st.lists(
        _ID_ALPHABET | st.sampled_from([",", '"', "\n", "\r", "\r\n"]), min_size=1, max_size=6
    ).map("".join),
)
dates = st.sampled_from(["2020-01-01", "2020-01-02", "2021-12-31", "1/2/2016", "01/02/2016"])
amounts = st.sampled_from(["", "0", "10.00", "10.0", "007.50", "1,000.00", "NA", "#N/A"])


def ndc_forms(ndc11: str) -> list[str]:
    """Raw spellings that all normalize to `ndc11`."""
    dashed = f"{ndc11[:5]}-{ndc11[5:9]}-{ndc11[9:]}"
    stripped = ndc11.lstrip("0")
    return [ndc11, dashed, stripped, dashed.lstrip("0"), f"{stripped[:-2]}-{stripped[-2:]}"]


@st.composite
def ndc11_values(draw: st.DrawFn) -> str:
    # Never all zeros: #7 makes those invalid, and these properties are about valid NDCs only.
    pool = ["00002322730", "00093005801", "00000000005", "12345678901"]
    random_digits = st.integers(1, 11).flatmap(lambda n: st.integers(1, 10**n - 1))
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
    """Messy rows with injected copies of earlier rows.

    A copy is exact, has its NDC respelled (still a duplicate), or differs in one other column
    (a near-duplicate that must survive). Independent rows almost never collide, so without
    near-duplicates a dedupe that ignores a column would go unnoticed.
    """
    rows = draw(st.lists(base_rows(), max_size=12))
    for _ in range(draw(st.integers(0, 6)) if rows else 0):
        source = list(draw(st.sampled_from(rows)))
        match draw(st.sampled_from(["exact", "respelled", "near"])):
            case "respelled":
                ndc11 = normalize_ndc(source[NDC_INDEX])
                source[NDC_INDEX] = draw(st.sampled_from(ndc_forms(ndc11)))
            case "near":
                column = draw(st.sampled_from([i for i in range(len(HEADER)) if i != NDC_INDEX]))
                source[column] += draw(st.sampled_from(["0", " ", "x"]))
        rows.insert(draw(st.integers(0, len(rows))), source)
    return rows


@dataclass(frozen=True)
class GeneratedCsv:
    rows: list[Row]
    data: bytes


def encode(rows: list[Row], terminator: str = "\n", quote_all: list[bool] | None = None) -> bytes:
    """CSV bytes for `rows`; `quote_all[i]` quotes every field of row i, empty ones included."""
    flags = quote_all if quote_all is not None else [False] * len(rows)
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator=terminator).writerow(HEADER)
    for row, flag in zip(rows, flags, strict=True):
        writer = csv.writer(
            buffer,
            lineterminator=terminator,
            quoting=csv.QUOTE_ALL if flag else csv.QUOTE_MINIMAL,
        )
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8")


def make_case(
    rows: list[Row], terminator: str = "\n", quote_all: list[bool] | None = None
) -> GeneratedCsv:
    return GeneratedCsv(rows=rows, data=encode(rows, terminator, quote_all))


@st.composite
def pharmacy_csvs(draw: st.DrawFn) -> GeneratedCsv:
    """A whole file. Quoting is chosen per file: some exporters quote every field."""
    rows = draw(pharmacy_rows())
    terminator = draw(st.sampled_from(["\n", "\r\n"]))
    return make_case(rows, terminator, [draw(st.booleans())] * len(rows))


@st.composite
def mixed_quoting_csvs(draw: st.DrawFn) -> GeneratedCsv:
    """Like `pharmacy_csvs`, but each row picks its own quoting style."""
    rows = draw(pharmacy_rows())
    flags = draw(st.lists(st.booleans(), min_size=len(rows), max_size=len(rows)))
    return make_case(rows, "\n", flags)


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


_ROW = ["P1", "2020-01-01", "0002-3227-30", "10.00", ""]
_EXAMPLES = [
    make_case([]),
    make_case([_ROW]),
    # Every row is the same claim, spelled four ways.
    make_case([[*_ROW[:2], ndc, *_ROW[3:]] for ndc in ndc_forms("00002322730")[:4]]),
    make_case([["a\r\nb", *_ROW[1:]], ["a\nb", *_ROW[1:]], ["a\r\nb", *_ROW[1:]]], "\r\n"),
    # Same claim except for one column each: nothing may collapse.
    make_case([_ROW, [*_ROW[:4], "0"], [*_ROW[:3], "10.0", ""], ["P2", *_ROW[1:]]]),
]


def with_examples[**P](test: Callable[P, None]) -> Callable[P, None]:
    """Always run the hand-picked edge cases, whatever the random seed does."""
    for case in _EXAMPLES:
        test = example(case)(test)
    return test


@SETTINGS
@with_examples
@given(pharmacy_csvs())
def test_output_is_exactly_the_first_occurrences_in_order(case: GeneratedCsv) -> None:
    # The full oracle: values, multiplicity and order. The tests below are weaker on purpose, so
    # that when this one fails, they tell you which part broke.
    _, output = run(case.data)
    assert parse(output)[1:] == first_occurrences(case.rows)


@SETTINGS
@given(pharmacy_csvs())
def test_output_rows_are_the_distinct_normalized_inputs(case: GeneratedCsv) -> None:
    # Order-insensitive, but each distinct normalized input row must appear exactly once.
    _, output = run(case.data)
    got = Counter(tuple(row) for row in parse(output)[1:])
    assert got == Counter({tuple(normalized(r)): 1 for r in case.rows})


@SETTINGS
@given(pharmacy_csvs())
def test_output_has_no_duplicate_rows(case: GeneratedCsv) -> None:
    _, output = run(case.data)
    body = [tuple(row) for row in parse(output)[1:]]
    assert len(body) == len(set(body))


@SETTINGS
@with_examples
@given(pharmacy_csvs())
def test_stats_balance(case: GeneratedCsv) -> None:
    stats, output = run(case.data)
    assert stats.rows_in == len(case.rows)
    assert stats.rows_out == len(parse(output)) - 1 == len(first_occurrences(case.rows))
    assert stats.duplicates_removed == stats.rows_in - stats.rows_out


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


@SETTINGS
@example(make_case([_ROW, _ROW], quote_all=[False, True]))
@given(mixed_quoting_csvs())
def test_quoting_style_does_not_affect_dedupe(case: GeneratedCsv) -> None:
    # The explicit example (bare vs quoted empty field) pins #19 on every run.
    _, output = run(case.data)
    assert parse(output)[1:] == first_occurrences(case.rows)


# --- #8: medical claims ------------------------------------------------------------------------

MEDICAL_HEADER = list(MEDICAL_COLUMNS)
DIAG = range(1, 5)

# Each slot is one code; padding variants of the same code must compare equal after cleaning.
_CODES = ["32723", "V5481", "E8497", "7", "A B", ""]
_PADS = ["", " ", " " * 8, "\t"]
diag_cells = st.builds(
    lambda code, left, right: left + code + right,
    st.sampled_from(_CODES),
    st.sampled_from(["", " "]),
    st.sampled_from(_PADS),
)


@st.composite
def medical_row(draw: st.DrawFn) -> Row:
    return [
        draw(patient_ids),
        *(draw(diag_cells) for _ in DIAG),
        draw(dates),
        draw(amounts),
        draw(amounts),
    ]


@st.composite
def medical_rows(draw: st.DrawFn) -> list[Row]:
    """Rows with injected exact, re-padded and near copies of earlier rows."""
    rows = draw(st.lists(medical_row(), max_size=12))
    for _ in range(draw(st.integers(0, 6)) if rows else 0):
        source = list(draw(st.sampled_from(rows)))
        match draw(st.sampled_from(["exact", "repadded", "near"])):
            case "repadded":
                column = draw(st.sampled_from(list(DIAG)))
                source[column] = draw(st.sampled_from(_PADS)) + source[column].strip()
            case "near":
                column = draw(st.sampled_from([0, 5, 6, 7]))
                source[column] += draw(st.sampled_from(["0", "x"]))
        rows.insert(draw(st.integers(0, len(rows))), source)
    return rows


def medical_encode(rows: list[Row], quote_all: list[bool]) -> bytes:
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\n").writerow(MEDICAL_HEADER)
    for row, flag in zip(rows, quote_all, strict=True):
        writer = csv.writer(
            buffer, lineterminator="\n", quoting=csv.QUOTE_ALL if flag else csv.QUOTE_MINIMAL
        )
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8")


def run_medical(data: bytes) -> tuple[CleanStats, bytes]:
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp, "in.csv"), Path(tmp, "out.csv")
        src.write_bytes(data)
        stats = dedupe_medical_data(src, out)
        return stats, out.read_bytes()


def stripped(row: Row) -> Row:
    return [c.strip() if i in DIAG else c for i, c in enumerate(row)]


@SETTINGS
@example(([["P", "1", "", "", "", "d", "1", "2"]] * 2, [False, True]))
@given(
    medical_rows().flatmap(
        lambda rows: st.tuples(
            st.just(rows), st.lists(st.booleans(), min_size=len(rows), max_size=len(rows))
        )
    )
)
def test_medical_dedupe(case: tuple[list[Row], list[bool]]) -> None:
    rows, quote_all = case
    stats, output = run_medical(medical_encode(rows, quote_all))
    got = parse(output)
    want = [list(r) for r in dict.fromkeys(tuple(stripped(r)) for r in rows)]
    assert got[0] == MEDICAL_HEADER
    assert got[1:] == want  # no duplicates, first occurrences, input order, other columns intact
    assert all(c == c.strip() for r in got[1:] for c in r[1:5])
    assert stats.rows_in == len(rows)
    assert stats.rows_out == len(want)
    assert stats.duplicates_removed == len(rows) - len(want)
