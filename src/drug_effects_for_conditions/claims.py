"""Helpers shared by the pharmacy and medical claims cleaners."""

import csv
import os
import shutil
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


@contextmanager
def stage_dir(target: StrPath) -> Iterator[Path]:
    """Yield a fresh scratch directory beside `target` (same file system) and always remove it."""
    path = Path(target)
    stage = path.with_name(f".{path.name}.{os.getpid()}.stage")
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir()
    try:
        yield stage
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def check_buckets(buckets: int | None) -> None:
    """Raise `ValueError` unless `buckets` is `None` or at least 1."""
    if buckets is not None and buckets < 1:
        raise ValueError(f"buckets must be at least 1, got {buckets}")


def stage_buckets(frame: Lazy, stage: Path, buckets: int) -> pl.LazyFrame:
    """A lazy sink of `frame` to Parquet files under `stage/in`, one directory per bucket.

    Rows gain a leading `_row` column (the input position) and go to bucket `hash(row) %
    buckets`; equal rows therefore share a bucket.
    """
    columns = frame.collect_schema().names()
    # pl.PartitionBy is unstable in polars 2.1; pyproject pins polars to match.
    return (
        frame.with_row_index("_row")
        .with_columns(_bucket=pl.struct(columns).hash(seed=0) % buckets)
        .sink_parquet(
            pl.PartitionBy(stage / "in", key="_bucket", include_key=False),
            # One open writer per bucket buffers a row group each; small groups keep that small.
            row_group_size=8192,
            mkdir=True,
            lazy=True,
        )
    )


def dedupe_buckets(stage: Path, columns: Sequence[str]) -> pl.LazyFrame:
    """Drop duplicates from each staged bucket in turn; return the survivors, `_row` first."""
    done = stage / "done"
    done.mkdir()
    # int(), not a string sort: "_bucket=10" sorts before "_bucket=2".
    bucket_dirs = sorted((stage / "in").glob("_bucket=*"), key=lambda d: int(d.name[8:]))
    for number, bucket in enumerate(bucket_dirs):
        # Sorting by `_row` makes "first" mean first in the input whatever order the sink wrote;
        # a bucket holds a fraction of the distinct rows, so the sort fits in memory.
        pl.scan_parquet(bucket, hive_partitioning=False).sort("_row").unique(
            subset=list(columns), keep="first", maintain_order=True
        ).sink_parquet(done / f"{number:06d}.parquet")
    return pl.scan_parquet(done, hive_partitioning=False)


def write_unique_csv(
    frame: Lazy,
    target: Path,
    *,
    counts: Lazy,
    side_sinks: Sequence[Lazy] = (),
    buckets: int | None = None,
    keep_order: bool = False,
) -> tuple[int, pl.DataFrame]:
    """Write the distinct rows of `frame` to the CSV `target`; return the count and `counts`.

    `counts` is a one-row frame of input-side statistics and `side_sinks` are other
    `sink_*(lazy=True)` frames, such as a rejects file. The output row count comes from the
    pipeline, not from reading `target` back.

    The unique step never shares a `collect_all` with another *sink*: polars caches a subplan that
    two sinks share, which holds the whole cleaned frame in memory (measured: +1 GB at 10M rows).
    A count query shares freely. So the unique sink runs first (with `counts` when there are no
    side sinks), then the side sinks run together with `counts`.

    `buckets=None` uses one `unique(maintain_order=True)`, which keeps every distinct row in
    memory and the first-occurrence order. With `buckets`, rows are hash-partitioned to Parquet
    files beside `target`, each bucket is de-duplicated alone, and the results are concatenated:
    memory follows the largest bucket, and the output is in bucket order unless `keep_order`,
    which sorts the survivors back into input order (that sort needs them all in memory).
    """
    check_buckets(buckets)
    first_counts = [] if side_sinks else [counts]
    rest = [*side_sinks, counts] if side_sinks else []

    if buckets is None:
        distinct = frame.unique(maintain_order=True)
        first = pl.collect_all(
            [
                distinct.sink_csv(target, lazy=True),
                *first_counts,
                distinct.select(pl.len().alias("rows_out")),
            ]
        )
        rows_out = int(first[-1].item())
        stats = pl.collect_all(rest)[-1] if rest else first[-2]
        return rows_out, stats
    columns = frame.collect_schema().names()
    with stage_dir(target) as stage:
        first = pl.collect_all([stage_buckets(frame, stage, buckets), *first_counts])
        stats = pl.collect_all(rest)[-1] if rest else first[-1]
        if not (stage / "in").is_dir():  # every row was blank or rejected: nothing was staged
            pl.LazyFrame(schema=frame.collect_schema()).sink_csv(target)
            return 0, stats
        survivors = dedupe_buckets(stage, columns)
        # Parquet footers hold the row counts, so this reads no data.
        rows_out = int(survivors.select(pl.len()).collect().item())
        if keep_order:
            survivors = survivors.sort("_row")
        survivors.drop("_row").sink_csv(target)
    return rows_out, stats
