"""Find patients who developed condition Y after first taking drug X.

All inputs are cleaned claims (`clean_pharmacy(...).clean`, `clean_medical(...)`) and reference
frames from `reference.py`. Each side is reduced to one row per patient before the two are
joined, so fills and claims are never joined row to row.
"""

from dataclasses import dataclass
from typing import Final

lazy import polars as pl

from drug_effects_for_conditions.medical import DIAGNOSIS_COLUMNS
from drug_effects_for_conditions.reference import match_ndc

__all__ = [
    "DATE_FORMAT",
    "CohortSummary",
    "condition_events",
    "developed_after",
    "first_exposure",
    "summarize",
]

DATE_FORMAT: Final = "%m/%d/%Y"  # chrono accepts unpadded months and days, as in `10/4/2016`


def _date(column: str) -> pl.Expr:
    # strict: an unparseable value raises when the frame is collected instead of becoming null.
    # chrono's %Y also takes `16` and reads it as year 16, so a value without a four-digit year
    # gets a "!" prefix, which fails the strict parse and shows up in the error message.
    value = pl.col(column)
    shaped = value.str.contains(r"^\d{1,2}/\d{1,2}/\d{4}$") | value.is_null()
    return pl.when(shaped).then(value).otherwise("!" + value).str.to_date(DATE_FORMAT, strict=True)


def first_exposure(pharmacy: pl.LazyFrame, ndc_list: pl.LazyFrame) -> pl.LazyFrame:
    """One row per patient with a drug X fill: `Patient_ID` and `first_x`, the earliest fill."""
    dated = pharmacy.with_columns(_date("Date_Submitted").alias("Date_Submitted"))
    return (
        match_ndc(dated, ndc_list)
        .group_by("Patient_ID")
        .agg(pl.col("Date_Submitted").min().alias("first_x"))
        .filter(pl.col("first_x").is_not_null())
    )


def condition_events(medical: pl.LazyFrame, codes: pl.LazyFrame) -> pl.LazyFrame:
    """Distinct `(Patient_ID, event_date)` for claims with a condition Y code in any column."""
    long = medical.select(
        "Patient_ID",
        _date("Service_Date").alias("event_date"),
        *DIAGNOSIS_COLUMNS,
    ).unpivot(
        DIAGNOSIS_COLUMNS,
        index=["Patient_ID", "event_date"],
        value_name="Diagnosis_Code",
    )
    return (
        long.join(codes, on="Diagnosis_Code", how="semi")
        .select("Patient_ID", "event_date")
        .filter(pl.col("event_date").is_not_null())
        .unique()
    )


def developed_after(
    pharmacy: pl.LazyFrame,
    medical: pl.LazyFrame,
    ndc_list: pl.LazyFrame,
    codes: pl.LazyFrame,
    *,
    incident: bool = True,
    same_day_counts: bool = False,
    washout_days: int = 0,
) -> pl.LazyFrame:
    """Patients who developed condition Y after their first drug X fill.

    `first_y_after` is the earliest Y claim after `first_x` (on or after it when
    `same_day_counts`). With `incident`, a patient is dropped if they had any Y claim before that
    date, so `first_y_after` is their first Y ever. `washout_days` keeps only patients whose
    `first_x` is at least that many days after the earliest pharmacy date, which is the point
    where look-back for prior drug use starts. Returns `Patient_ID`, `first_x`, `first_y_after`
    and `days_to_y`, one row per patient, sorted by `Patient_ID`. Rows with a missing date are
    ignored.
    """
    if washout_days < 0:
        raise ValueError(f"washout_days must not be negative, got {washout_days}")
    exposure = _washed_out(first_exposure(pharmacy, ndc_list), pharmacy, washout_days)
    after = (
        pl.col("event_date") >= pl.col("first_x")
        if same_day_counts
        else pl.col("event_date") > pl.col("first_x")
    )
    per_patient = (
        condition_events(medical, codes)
        .join(exposure, on="Patient_ID")
        .group_by("Patient_ID")
        .agg(
            pl.col("first_x").first(),
            pl.col("event_date").min().alias("first_y"),
            pl.col("event_date").filter(after).min().alias("first_y_after"),
        )
        .filter(pl.col("first_y_after").is_not_null())
    )
    if incident:
        per_patient = per_patient.filter(pl.col("first_y") == pl.col("first_y_after"))
    return (
        per_patient.with_columns(
            (pl.col("first_y_after") - pl.col("first_x")).dt.total_days().alias("days_to_y")
        )
        .select("Patient_ID", "first_x", "first_y_after", "days_to_y")
        .sort("Patient_ID")
    )


def _washed_out(exposure: pl.LazyFrame, pharmacy: pl.LazyFrame, washout_days: int) -> pl.LazyFrame:
    if washout_days == 0:
        return exposure
    start = pharmacy.select(_date("Date_Submitted").min().alias("coverage_start"))
    return (
        exposure.join(start, how="cross")
        .filter(pl.col("first_x") >= pl.col("coverage_start") + pl.duration(days=washout_days))
        .drop("coverage_start")
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class CohortSummary:
    """Patient counts for one cohort definition.

    `drug_x` and `condition_y` count every patient with a drug X fill or a condition Y claim,
    `both` those with each (in any order). `eligible` is `drug_x` less the `washout_excluded`
    patients, and `qualifying` is the cohort size. `no_followup` counts eligible patients whose
    first fill is after the last medical `Service_Date`, so no Y could have been observed.
    """

    drug_x: int
    condition_y: int
    both: int
    washout_excluded: int
    eligible: int
    qualifying: int
    no_followup: int


def summarize(
    pharmacy: pl.LazyFrame,
    medical: pl.LazyFrame,
    ndc_list: pl.LazyFrame,
    codes: pl.LazyFrame,
    *,
    incident: bool = True,
    same_day_counts: bool = False,
    washout_days: int = 0,
) -> CohortSummary:
    """Count the patients behind `developed_after` with the same arguments."""
    exposure = first_exposure(pharmacy, ndc_list)
    eligible = _washed_out(exposure, pharmacy, washout_days)
    events = condition_events(medical, codes)
    last_service = medical.select(_date("Service_Date").max().alias("last_service"))
    counts = pl.concat(
        [
            exposure.select(pl.len().alias("drug_x")),
            events.select(pl.col("Patient_ID").n_unique().alias("condition_y")),
            events.select("Patient_ID")
            .unique()
            .join(exposure, on="Patient_ID")
            .select(pl.len().alias("both")),
            eligible.select(pl.len().alias("eligible")),
            eligible.join(last_service, how="cross")
            .filter(pl.col("first_x") > pl.col("last_service"))
            .select(pl.len().alias("no_followup")),
            developed_after(
                pharmacy,
                medical,
                ndc_list,
                codes,
                incident=incident,
                same_day_counts=same_day_counts,
                washout_days=washout_days,
            ).select(pl.len().alias("qualifying")),
        ],
        how="horizontal",
    ).collect()
    row = counts.row(0, named=True)
    return CohortSummary(washout_excluded=row["drug_x"] - row["eligible"], **row)
