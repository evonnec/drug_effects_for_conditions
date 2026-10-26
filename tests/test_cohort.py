import datetime as dt
import itertools
from collections.abc import Sequence
from pathlib import Path

import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from polars.exceptions import InvalidOperationError

from drug_effects_for_conditions import (
    CohortSummary,
    clean_medical,
    clean_pharmacy,
    condition_events,
    developed_after,
    first_exposure,
    load_diagnosis_codes,
    load_ndc_list,
    scan_medical,
    scan_pharmacy,
    summarize,
)

DATA = Path(__file__).parents[1] / "data"
X = "00000000005"
OTHER = "00000000006"
Y = "9592"
COMBOS = list(itertools.product([True, False], [False, True]))  # (incident, same_day_counts)

type Fill = tuple[str, str, str]  # patient, date, NDC
type Claim = tuple[str, str, Sequence[str]]  # patient, date, diagnosis codes


def mdy(day: dt.date) -> str:
    return f"{day.month}/{day.day}/{day.year}"


def pharmacy_frame(fills: Sequence[Fill]) -> pl.LazyFrame:
    columns = ["Patient_ID", "Date_Submitted", "NDC"]
    data = {name: [fill[i] for fill in fills] for i, name in enumerate(columns)}
    return pl.LazyFrame(data, schema=dict.fromkeys(columns, pl.String))


def medical_frame(claims: Sequence[Claim]) -> pl.LazyFrame:
    rows = [
        {
            "Patient_ID": patient,
            "Service_Date": date,
            **{f"Diagnosis_Code_{i + 1}": (*codes, None, None, None, None)[i] for i in range(4)},
        }
        for patient, date, codes in claims
    ]
    schema = dict.fromkeys(
        ["Patient_ID", "Service_Date", *(f"Diagnosis_Code_{i}" for i in range(1, 5))], pl.String
    )
    return pl.LazyFrame(rows, schema=schema)


def ndcs(*values: str) -> pl.LazyFrame:
    return pl.LazyFrame({"NDC": list(values)}, schema={"NDC": pl.String})


def codes(*values: str) -> pl.LazyFrame:
    return pl.LazyFrame({"Diagnosis_Code": list(values)}, schema={"Diagnosis_Code": pl.String})


# Four patients, all first filling X on 3/1/2016, plus one patient with Y and no X.
FILLS: list[Fill] = [
    ("P1", "3/1/2016", X),
    ("P1", "3/9/2016", X),
    ("P2", "3/1/2016", X),
    ("P3", "1/1/2016", OTHER),  # not drug X: neither the index date nor ignored for coverage
    ("P3", "3/1/2016", X),
    ("P4", "6/15/2016", X),  # after the last medical claim, and no claims at all
]
CLAIMS: list[Claim] = [
    ("P1", "1/10/2016", [Y]),  # Y before X
    ("P1", "5/1/2016", ["1", "2", Y]),
    ("P2", "3/1/2016", [Y]),  # Y on the day of the first fill
    ("P3", "4/1/2016", ["1", "2", "3", Y]),  # Y in the fourth column only
    ("P3", "4/2/2016", ["999"]),
    ("P5", "6/1/2016", [Y]),  # Y but never drug X
]


def cohort(
    *, incident: bool = True, same_day_counts: bool = False, washout_days: int = 0
) -> dict[str, tuple[str, str]]:
    frame = developed_after(
        pharmacy_frame(FILLS),
        medical_frame(CLAIMS),
        ndcs(X),
        codes(Y),
        incident=incident,
        same_day_counts=same_day_counts,
        washout_days=washout_days,
    ).collect()
    return {
        r["Patient_ID"]: (str(r["first_x"]), str(r["first_y_after"]))
        for r in frame.iter_rows(named=True)
    }


@pytest.mark.parametrize(
    ("incident", "same_day", "expected"),
    [
        (True, False, {"P3"}),  # P1 is prevalent, P2's Y is on the index date
        (True, True, {"P2", "P3"}),
        (False, False, {"P1", "P3"}),
        (False, True, {"P1", "P2", "P3"}),
    ],
)
def test_four_patient_fixture(incident: bool, same_day: bool, expected: set[str]) -> None:
    assert set(cohort(incident=incident, same_day_counts=same_day)) == expected


def test_fixture_dates_and_days() -> None:
    assert cohort(incident=False) == {
        "P1": ("2016-03-01", "2016-05-01"),  # the Y before X is not the outcome
        "P3": ("2016-03-01", "2016-04-01"),
    }
    frame = developed_after(
        pharmacy_frame(FILLS), medical_frame(CLAIMS), ndcs(X), codes(Y), incident=False
    ).collect()
    assert frame.columns == ["Patient_ID", "first_x", "first_y_after", "days_to_y"]
    assert frame["days_to_y"].to_list() == [61, 31]


def test_patient_without_claims_is_excluded_and_has_no_followup() -> None:
    assert "P4" not in cohort(incident=False, same_day_counts=True)
    summary = summarize(pharmacy_frame(FILLS), medical_frame(CLAIMS), ndcs(X), codes(Y))
    assert summary == CohortSummary(
        drug_x=4,
        condition_y=4,
        both=3,
        washout_excluded=0,
        eligible=4,
        qualifying=1,
        no_followup=1,
    )


def test_washout_counts_from_earliest_pharmacy_date_of_any_drug() -> None:
    # Coverage starts 1/1/2016 (P3's other drug), so 60 days later is 3/1/2016 and 61 is not.
    assert set(cohort(incident=False, washout_days=60)) == {"P1", "P3"}
    assert cohort(incident=False, washout_days=61) == {}
    summary = summarize(
        pharmacy_frame(FILLS), medical_frame(CLAIMS), ndcs(X), codes(Y), washout_days=61
    )
    assert (summary.eligible, summary.washout_excluded, summary.qualifying) == (1, 3, 0)
    assert summary.no_followup == 1


def test_negative_washout_is_rejected() -> None:
    with pytest.raises(ValueError, match="negative"):
        developed_after(
            pharmacy_frame(FILLS), medical_frame(CLAIMS), ndcs(X), codes(Y), washout_days=-1
        )


def test_first_exposure_and_events_are_one_row_per_key() -> None:
    exposure = first_exposure(pharmacy_frame(FILLS), ndcs(X)).sort("Patient_ID").collect()
    assert exposure["Patient_ID"].to_list() == ["P1", "P2", "P3", "P4"]
    assert exposure["first_x"].to_list() == [
        dt.date(2016, 3, 1),
        dt.date(2016, 3, 1),
        dt.date(2016, 3, 1),
        dt.date(2016, 6, 15),
    ]
    events = condition_events(medical_frame(CLAIMS + CLAIMS), codes(Y)).collect()
    assert events.height == events.unique().height == 5


def test_claim_with_y_in_several_columns_is_one_event() -> None:
    events = condition_events(medical_frame([("P1", "1/1/2016", [Y, Y, Y])]), codes(Y)).collect()
    assert events.height == 1


@pytest.mark.parametrize("bad", ["2016-03-01", "13/1/2016", "3/1/16", "", "x", "3/1/2016 "])
def test_unparseable_pharmacy_date_raises(bad: str) -> None:
    # The bad row is not drug X: every date is checked, not only the ones that match.
    fills = [*FILLS, ("P9", bad, OTHER)]
    with pytest.raises(InvalidOperationError):
        developed_after(pharmacy_frame(fills), medical_frame(CLAIMS), ndcs(X), codes(Y)).collect()


@pytest.mark.parametrize("bad", ["2016-03-01", "2/30/2016", "Mar 1"])
def test_unparseable_medical_date_raises(bad: str) -> None:
    claims = [*CLAIMS, ("P9", bad, ["999"])]
    with pytest.raises(InvalidOperationError):
        developed_after(pharmacy_frame(FILLS), medical_frame(claims), ndcs(X), codes(Y)).collect()


def test_unpadded_and_padded_dates_agree() -> None:
    fills = [("P1", "03/01/2016", X)]
    claims = [("P1", "3/2/2016", [Y])]
    result = developed_after(pharmacy_frame(fills), medical_frame(claims), ndcs(X), codes(Y))
    assert result.collect()["days_to_y"].to_list() == [1]


# Sample data


@pytest.fixture(scope="module")
def sample() -> tuple[pl.LazyFrame, pl.LazyFrame, pl.LazyFrame, pl.LazyFrame]:
    pharmacy = clean_pharmacy(scan_pharmacy(DATA / "pharmacy_data_sample.csv")).clean.collect()
    medical = clean_medical(scan_medical(DATA / "medical_data_sample.csv")).collect()
    return (
        pharmacy.lazy(),
        medical.lazy(),
        load_ndc_list(DATA / "ndc.csv"),
        load_diagnosis_codes(DATA / "diagnosis_code.csv"),
    )


@pytest.mark.parametrize(
    ("incident", "same_day", "expected"),
    [(True, False, 456), (False, False, 478), (True, True, 464), (False, True, 487)],
)
def test_sample_counts(
    sample: tuple[pl.LazyFrame, ...], incident: bool, same_day: bool, expected: int
) -> None:
    frame = developed_after(*sample, incident=incident, same_day_counts=same_day).collect()
    assert frame.height == expected
    assert frame["Patient_ID"].is_unique().all()
    assert frame["Patient_ID"].is_sorted()
    ordered = (
        frame["first_y_after"] >= frame["first_x"]
        if same_day
        else frame["first_y_after"] > frame["first_x"]
    )
    assert ordered.all()
    assert (frame["days_to_y"] == (frame["first_y_after"] - frame["first_x"]).dt.total_days()).all()


def test_sample_washout_90(sample: tuple[pl.LazyFrame, ...]) -> None:
    assert developed_after(*sample, washout_days=90).collect().height == 55
    exposure = first_exposure(sample[0], sample[2]).collect()
    assert (exposure["first_x"] >= dt.date(2016, 1, 27)).sum() == 127
    summary = summarize(*sample, washout_days=90)
    assert (summary.eligible, summary.qualifying) == (127, 55)


def test_sample_summary(sample: tuple[pl.LazyFrame, ...]) -> None:
    assert summarize(*sample) == CohortSummary(
        drug_x=759,
        condition_y=3828,
        both=538,
        washout_excluded=0,
        eligible=759,
        qualifying=456,
        no_followup=30,
    )


@pytest.mark.parametrize(("incident", "same_day"), COMBOS)
def test_patient_set_is_the_same_without_deduplication(
    sample: tuple[pl.LazyFrame, ...], incident: bool, same_day: bool
) -> None:
    pharmacy, medical, ndc_list, diagnosis = sample
    doubled = (pl.concat([pharmacy, pharmacy]), pl.concat([medical, medical]))
    clean = developed_after(
        pharmacy, medical, ndc_list, diagnosis, incident=incident, same_day_counts=same_day
    ).collect()
    dupes = developed_after(
        *doubled, ndc_list, diagnosis, incident=incident, same_day_counts=same_day
    ).collect()
    assert dupes.equals(clean)


# Property test against a plain-Python implementation of the definition

BASE = dt.date(2016, 1, 1)
offsets = st.integers(0, 25)  # a narrow range makes same-day and before/after ties common


@st.composite
def histories(draw: st.DrawFn) -> dict[str, tuple[list[tuple[int, bool]], list[tuple[int, bool]]]]:
    """Per patient: fills as (day offset, is drug X) and claims as (day offset, has Y)."""
    patients = {}
    for i in range(draw(st.integers(0, 6))):
        fills = draw(st.lists(st.tuples(offsets, st.booleans()), max_size=4))
        claims = draw(st.lists(st.tuples(offsets, st.booleans()), max_size=5))
        patients[f"p{i}"] = (fills, claims)
    return patients


def reference(
    history: dict[str, tuple[list[tuple[int, bool]], list[tuple[int, bool]]]],
    *,
    incident: bool,
    same_day_counts: bool,
    washout_days: int,
) -> dict[str, tuple[dt.date, dt.date]]:
    all_fills = [BASE + dt.timedelta(d) for fills, _ in history.values() for d, _ in fills]
    expected = {}
    for patient, (fills, claims) in sorted(history.items()):
        x_days = [BASE + dt.timedelta(d) for d, is_x in fills if is_x]
        y_days = sorted(BASE + dt.timedelta(d) for d, has_y in claims if has_y)
        if not x_days:
            continue
        first_x = min(x_days)
        if first_x < min(all_fills) + dt.timedelta(washout_days):
            continue
        after = [y for y in y_days if (y >= first_x if same_day_counts else y > first_x)]
        before = [y for y in y_days if (y < first_x if same_day_counts else y <= first_x)]
        if after and not (incident and before):
            expected[patient] = (first_x, after[0])
    return expected


@settings(max_examples=150)
@given(history=histories(), washout=st.integers(0, 12))
@pytest.mark.parametrize(("incident", "same_day"), COMBOS)
def test_cohort_matches_reference_implementation(
    history: dict[str, tuple[list[tuple[int, bool]], list[tuple[int, bool]]]],
    washout: int,
    incident: bool,
    same_day: bool,
) -> None:
    fills = [
        (p, mdy(BASE + dt.timedelta(d)), X if is_x else OTHER)
        for p, (fs, _) in history.items()
        for d, is_x in fs
    ]
    claims = [
        (p, mdy(BASE + dt.timedelta(d)), [Y] if has_y else ["999"])
        for p, (_, cs) in history.items()
        for d, has_y in cs
    ]
    got = developed_after(
        pharmacy_frame(fills),
        medical_frame(claims),
        ndcs(X),
        codes(Y),
        incident=incident,
        same_day_counts=same_day,
        washout_days=washout,
    ).collect()
    expected = reference(history, incident=incident, same_day_counts=same_day, washout_days=washout)
    assert {
        r["Patient_ID"]: (r["first_x"], r["first_y_after"]) for r in got.iter_rows(named=True)
    } == expected
    assert got["Patient_ID"].to_list() == sorted(expected)
    assert got["days_to_y"].to_list() == [(y - x).days for x, y in expected.values()]
