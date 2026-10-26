"""Time and peak memory of the single-pass and hash-partitioned de-duplication.

Run by hand, not in CI:

    uv run python benchmarks/dedupe_benchmark.py --rows 20000000

The input is the pharmacy sample repeated with a distinct `Patient_ID` suffix per copy, so every
row of a copy repeats within that copy only. Each configuration runs in its own subprocess, and
its peak RSS comes from `os.wait4`, which reports that one child (not the maximum so far).
"""

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import polars as pl

from drug_effects_for_conditions.claims import dedupe_buckets, stage_buckets

# The private _split_pharmacy gives the rows the partitioned path stages, without the stats.
from drug_effects_for_conditions.pharmacy import (
    _split_pharmacy,
    dedupe_ndc_pharm_data,
    scan_pharmacy,
)

SAMPLE = Path(__file__).parents[1] / "data" / "pharmacy_data_sample.csv"
BUCKETS = 64
# Worker names, in the order they are reported. "stage" leaves its files for "buckets".
CONFIGS = ("single", "partitioned", "partitioned-ordered", "stage", "buckets", "parquet-unique")


def generate(path: Path, rows: int) -> int:
    """Write at least `rows` rows (whole copies of the sample); return the row count."""
    sample = pl.read_csv(SAMPLE, infer_schema=False)
    copies = -(-rows // sample.height)
    with path.open("wb") as stream:
        for i in range(copies):
            part = sample.with_columns(pl.col("Patient_ID") + f"_{i}")
            part.write_csv(stream, include_header=i == 0)
    return copies * sample.height


def worker(config: str, input_path: Path, workdir: Path) -> None:
    out = workdir / "out.csv"
    match config:
        case "single":
            dedupe_ndc_pharm_data(input_path, out)
        case "partitioned":
            dedupe_ndc_pharm_data(input_path, out, buckets=BUCKETS)
        case "partitioned-ordered":
            dedupe_ndc_pharm_data(input_path, out, buckets=BUCKETS, keep_order=True)
        case "stage":
            normalized, _ = _split_pharmacy(scan_pharmacy(input_path))
            stage_buckets(normalized, workdir, BUCKETS).collect()
        case "buckets":
            columns = list(scan_pharmacy(input_path).collect_schema().names())
            dedupe_buckets(workdir, columns)
        case "parquet-unique":
            # The baseline for "buckets": one unique over the same staged rows.
            staged = pl.scan_parquet(workdir / "in", hive_partitioning=False)
            columns = [c for c in staged.collect_schema().names() if c != "_row"]
            staged.unique(subset=columns, keep="first", maintain_order=True).drop("_row").sink_csv(
                out
            )
        case _:
            raise SystemExit(f"unknown configuration {config!r}")


def run_config(config: str, input_path: Path, workdir: Path) -> tuple[float, float]:
    """Run one worker subprocess; return its wall seconds and peak RSS in MB."""
    command = [sys.executable, __file__, "--worker", config, str(input_path), str(workdir)]
    started = time.perf_counter()
    process = subprocess.Popen(command)
    _, status, usage = os.wait4(process.pid, 0)
    wall = time.perf_counter() - started
    if os.waitstatus_to_exitcode(status) != 0:
        raise SystemExit(f"{config} failed")
    # ru_maxrss is bytes on macOS and kilobytes elsewhere.
    scale = 1 if sys.platform == "darwin" else 1024
    return wall, usage.ru_maxrss * scale / 1e6


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rows", type=int, default=20_000_000, help="rows to generate")
    parser.add_argument("--worker", nargs=3, metavar=("CONFIG", "INPUT", "WORKDIR"))
    args = parser.parse_args()
    if args.worker:
        worker(args.worker[0], Path(args.worker[1]), Path(args.worker[2]))
        return
    with tempfile.TemporaryDirectory(prefix="dedupe-bench-") as tmp:
        input_path = Path(tmp, "input.csv")
        actual = generate(input_path, args.rows)
        size = input_path.stat().st_size / 1e9
        print(f"input: {actual:,} rows, {size:.2f} GB; {BUCKETS} buckets\n")
        print("| Configuration | Wall (s) | Peak RSS (MB) |")
        print("| --- | ---: | ---: |")
        peaks: dict[str, float] = {}
        for config in CONFIGS:
            # "stage" fills this directory; "buckets" and "parquet-unique" read what it left.
            name = "staged" if config in ("stage", "buckets", "parquet-unique") else config
            workdir = Path(tmp, name)
            workdir.mkdir(exist_ok=True)
            wall, peak = run_config(config, input_path, workdir)
            peaks[config] = peak
            print(f"| {config} | {wall:.1f} | {peak:,.0f} |", flush=True)
        print(
            f"\nbuckets phase peak / single-pass peak: {peaks['buckets'] / peaks['single']:.1%}; "
            f"/ single unique over the staged Parquet: "
            f"{peaks['buckets'] / peaks['parquet-unique']:.1%}"
        )


if __name__ == "__main__":
    main()
