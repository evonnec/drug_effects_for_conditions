"""Helpers shared by the pharmacy and medical claims cleaners."""

import csv
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

lazy import polars as pl

type StrPath = str | os.PathLike[str]
# An alias, not `pl.LazyFrame` inline: dataclasses resolve annotations and would import polars.
type Lazy = pl.LazyFrame


class ClaimsSchemaError(ValueError):
    """The CSV header does not match the expected columns."""


class ClaimsFileError(ValueError):
    """A claims file is empty, is not UTF-8 or has a malformed row."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CleanStats:
    rows_in: int
    rows_out: int
    blank_rows: int = 0
    rejected: int = 0

    @property
    def duplicates_removed(self) -> int:
        return self.rows_in - self.blank_rows - self.rejected - self.rows_out


def _first_line(exc: BaseException) -> str:
    # polars errors run to several lines; the CLI prints exactly one.
    lines = (line.strip() for line in str(exc).splitlines())
    return next((line for line in lines if line), type(exc).__name__)


@contextmanager
def file_errors(*paths: StrPath) -> Iterator[None]:
    """Re-raise polars and decoding errors as `ClaimsFileError` naming `paths`.

    polars is lazy, so a bad row usually surfaces when the pipeline runs, not when it is
    scanned. `OSError` passes through untouched: it already names the file and the cause.
    """
    try:
        yield
    except ClaimsFileError:
        raise
    except (pl.exceptions.PolarsError, UnicodeDecodeError, csv.Error) as exc:
        names = " or ".join(str(p) for p in paths)
        raise ClaimsFileError(f"{names}: {_first_line(exc)}") from exc


def _check_field_counts(path: Path, width: int) -> None:
    """Raise `ClaimsFileError` for a row without `width` fields (or invalid UTF-8).

    polars null-fills a short row, so it can't tell `1,d,5` from `1,d,5,,`; counting the fields
    with the csv module can.
    """
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        for row in reader:
            blank_line = len(row) <= 1 and not any(field.strip() for field in row)
            if len(row) == width or blank_line:
                continue
            kind = "fewer" if len(row) < width else "more"
            raise ClaimsFileError(
                f"{path}: line {reader.line_num} has {len(row)} fields, {kind} than the "
                f"{width} columns of the header"
            )


def _scan(path: Path, **kwargs: Any) -> pl.LazyFrame:
    return pl.scan_csv(
        path, raise_if_empty=True, truncate_ragged_lines=False, encoding="utf8", **kwargs
    )


def scan_claims(
    path: StrPath,
    columns: Sequence[str],
    *,
    error: type[ClaimsSchemaError] = ClaimsSchemaError,
) -> pl.LazyFrame:
    """Lazily scan a CSV whose header must be `columns`, reading every column as a string.

    A wrong header raises `error`, so each cleaner can raise its own subclass. A file that is
    empty, is not UTF-8 or has a row with the wrong number of fields raises `ClaimsFileError`.
    """
    csv_path = Path(path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"input file not found: {csv_path}")
    with file_errors(csv_path):
        header = _scan(csv_path, infer_schema=False).collect_schema().names()
        if tuple(header) != tuple(columns):
            raise error(f"Expected columns {list(columns)}, found {header}")
        _check_field_counts(csv_path, len(columns))
        return _scan(csv_path, schema=dict.fromkeys(columns, pl.String))


def empty_to_null(frame: pl.LazyFrame) -> pl.LazyFrame:
    # polars reads a bare empty field as null but a quoted one ("") as "": make them one value.
    return frame.with_columns(pl.all().replace("", None))


def is_blank_expr() -> pl.Expr:
    return pl.all_horizontal(pl.all().str.strip_chars().fill_null("") == "")


def count_rows(path: StrPath) -> int:
    """Data rows in a CSV written by this package."""
    return int(pl.scan_csv(Path(path), infer_schema=False).select(pl.len()).collect().item())


@contextmanager
def staged_outputs(*targets: StrPath) -> Iterator[list[Path]]:
    """Yield a temp path beside each target; on success `os.replace` them onto the targets.

    The temps are always removed. A target is only touched once every write has succeeded, so a
    failed run leaves an existing file as it was. `os.replace` swaps the directory entry, so a
    target that is a link to the input can never truncate the input.
    """
    paths = [Path(t) for t in targets]
    temps = [p.with_name(f".{p.name}.{os.getpid()}.tmp") for p in paths]
    try:
        yield temps
        for temp, target in zip(temps, paths, strict=True):
            temp.replace(target)
    finally:
        for temp in temps:
            temp.unlink(missing_ok=True)


def write_csv_atomic(frame: pl.LazyFrame, path: StrPath) -> None:
    """Sink `frame` to `path` through a temp file, so `path` is never left half-written."""
    with staged_outputs(path) as (temp,):
        frame.sink_csv(temp)
