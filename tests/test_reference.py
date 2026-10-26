from collections.abc import Callable
from pathlib import Path

import polars as pl
import pytest

from drug_effects_for_conditions import (
    ReferenceFileError,
    clean_medical,
    clean_pharmacy,
    load_diagnosis_codes,
    load_ndc_list,
    match_ndc,
    scan_medical,
    scan_pharmacy,
)

type Loader = Callable[[Path], pl.LazyFrame]

DATA = Path(__file__).parents[1] / "data"
NDC_FILE = DATA / "ndc.csv"
CODES_FILE = DATA / "diagnosis_code.csv"


def write(tmp_path: Path, content: bytes, name: str = "ref.csv") -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def test_ndc_sample() -> None:
    frame = load_ndc_list(NDC_FILE).collect()
    assert frame.columns == ["NDC"]
    assert frame.height == 404
    assert frame["NDC"].str.contains(r"^\d{11}$").all()
    assert "10370010250" in frame["NDC"].to_list()
    assert frame["NDC"].str.starts_with("0").sum() == 166


def test_diagnosis_sample() -> None:
    frame = load_diagnosis_codes(CODES_FILE).collect()
    assert frame.columns == ["Diagnosis_Code"]
    assert frame.height == 146
    assert not frame["Diagnosis_Code"].str.contains(r"\s").any()
    assert {"9592", "V571"} <= set(frame["Diagnosis_Code"])


def test_headerless_crlf_without_final_newline_keeps_first_line(tmp_path: Path) -> None:
    path = write(tmp_path, b"00002322730\r\n00093005801\r\n00000000005")
    assert load_ndc_list(path).collect()["NDC"].to_list() == [
        "00002322730",
        "00093005801",
        "00000000005",
    ]


def test_ndc_distinct_in_file_order(tmp_path: Path) -> None:
    path = write(tmp_path, b"00093005801\n00002322730\n00093005801\n 00002322730 \n")
    assert load_ndc_list(path).collect()["NDC"].to_list() == ["00093005801", "00002322730"]


def test_codes_are_stripped_and_distinct(tmp_path: Path) -> None:
    path = write(tmp_path, b"V571   \r\n9592\r\nV571\r\nE8190")
    assert load_diagnosis_codes(path).collect()["Diagnosis_Code"].to_list() == [
        "V571",
        "9592",
        "E8190",
    ]


@pytest.mark.parametrize(
    "content",
    [b"0002322730a\n", b"0002322730\n", b"000232273012\n", b"12345678901\n123\n", b"1234567890-\n"],
    ids=["letter", "ten-digits", "twelve-digits", "one-bad-line", "dash"],
)
def test_bad_ndc_raises_with_file_name_and_value(tmp_path: Path, content: bytes) -> None:
    path = write(tmp_path, content, "drug_x.csv")
    with pytest.raises(ReferenceFileError, match=r"drug_x\.csv") as info:
        load_ndc_list(path)
    bad = next(v for v in content.decode().split() if len(v) != 11 or not v.isdigit())
    assert repr(bad) in str(info.value)


@pytest.mark.parametrize("content", [b"12\n", b"V57.1\n", b"abc\n", b"12345678\n", b"9592 1\n"])
def test_bad_code_raises(tmp_path: Path, content: bytes) -> None:
    with pytest.raises(ReferenceFileError, match=r"ref\.csv"):
        load_diagnosis_codes(write(tmp_path, content))


@pytest.mark.parametrize("content", [b"", b"\n", b"   \n"], ids=["empty", "newline", "spaces"])
@pytest.mark.parametrize("loader", [load_ndc_list, load_diagnosis_codes])
def test_empty_file_raises(tmp_path: Path, content: bytes, loader: Loader) -> None:
    with pytest.raises(ReferenceFileError):
        loader(write(tmp_path, content))


def test_blank_line_between_values_is_missing_value(tmp_path: Path) -> None:
    with pytest.raises(ReferenceFileError, match="missing"):
        load_ndc_list(write(tmp_path, b"00002322730\n\n00093005801\n"))


def test_error_sample_is_capped(tmp_path: Path) -> None:
    path = write(tmp_path, "".join(f"{i}\n" for i in range(50)).encode())
    with pytest.raises(ReferenceFileError) as info:
        load_ndc_list(path)
    assert "50 invalid" in str(info.value)
    assert "'5'" not in str(info.value)


@pytest.mark.parametrize("loader", [load_ndc_list, load_diagnosis_codes])
def test_missing_file(tmp_path: Path, loader: Loader) -> None:
    with pytest.raises(FileNotFoundError):
        loader(tmp_path / "nope.csv")


def test_reference_error_is_value_error() -> None:
    assert issubclass(ReferenceFileError, ValueError)


def test_match_ndc_on_sample() -> None:
    pharmacy = clean_pharmacy(scan_pharmacy(DATA / "pharmacy_data_sample.csv")).clean
    matched = match_ndc(pharmacy, load_ndc_list(NDC_FILE)).collect()
    assert matched.height == 10_231
    assert matched["Patient_ID"].n_unique() == 759
    assert matched.columns == pharmacy.collect_schema().names()


def test_medical_codes_match_3828_patients() -> None:
    medical = clean_medical(scan_medical(DATA / "medical_data_sample.csv"))
    codes = load_diagnosis_codes(CODES_FILE)
    long = medical.unpivot(
        [f"Diagnosis_Code_{i}" for i in range(1, 5)],
        index="Patient_ID",
        value_name="Diagnosis_Code",
    )
    patients = long.join(codes, on="Diagnosis_Code", how="semi").select(pl.col("Patient_ID"))
    assert patients.collect()["Patient_ID"].n_unique() == 3_828
