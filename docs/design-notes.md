# Design notes

Engineering notes on running this kind of pipeline at scale: how to test data quality, how to
scale the de-duplication, how to reconcile records from several systems, and how join cardinality
behaves. The first two sections refer to the pharmacy cleaning in
`src/drug_effects_for_conditions/`.

## Testing data quality at scale

Unit tests show that the cleaning code does what it claims. They say nothing about the data that
arrives tomorrow. A usable plan needs both, in layers, with each check placed where it can run
cheaply and fail loudly.

### Code tests (run in CI)

Test each function against small inputs.

- De-duplication returns no repeated rows, keeps the first occurrence, and keeps row order.
- NDC normalization removes dashes, pads short codes with leading zeros, and is idempotent.
  Property-based tests (hypothesis) fit well: for any string, the output has at least 11
  characters, no dash, and normalizing twice changes nothing.
- The writer reproduces the input except for the cleaned field.
- Fixed fixtures catch regressions; a sample-parity test pins the row counts on the committed
  sample data (32,932 rows in, 30,097 out).

### Layer 1: schema and contract checks

Run on every batch before any transformation.

- Column names, order and count match the contract (the pharmacy loader raises
  `PharmacySchemaError` on a header mismatch).
- Types parse: dates as `m/d/yyyy`, amounts as non-negative decimals, IDs as non-empty.
- Nullability is decided per column and written down. For example, `Patient_ID` and `NDC` are
  never null; the unused `Diagnosis_Code_2` to `Diagnosis_Code_4` slots may be blank.
- Units and currency are stated once. If amounts can arrive in more than one currency, normalize
  at ingestion and record the original.

A failed contract check rejects the whole batch. There is no point cleaning a file whose columns
shifted.

### Layer 2: field-level rules, using NDC as the example

For each field, state the rule, what happens to a violation, and what is measured.

1. **Format.** After normalization an NDC must match `^\d{11}$`. Anything else (letters, wrong
   length, empty) fails.
2. **Reference match.** Check the normalized NDC against the reference list (`ndc.csv` for drug
   X, or the full NDC directory in production). A well-formed code that is not in the list is not
   necessarily wrong, since the list only covers drug X, so this check feeds a metric rather than
   a reject. For the full directory, an unknown code is a warning.
3. **Rejects path.** Rows that fail the format rule are not dropped silently. Write them to a
   rejects table with the batch ID, source file, row number, raw value and failed rule. Clean rows
   continue downstream.
4. **Metrics per batch.** Rows in, rows out, rows rejected, rejection rate by rule, share of NDCs
   that needed dash removal, share that needed padding, and share not in the reference list.
5. **Alert thresholds.** Start from a baseline of recent batches. Page when the reject rate
   exceeds a fixed ceiling (for example 1%) or moves more than a few standard deviations from the
   trailing average. Warn when the padded share or the unknown-code share shifts sharply: a sudden
   jump usually means an upstream format change rather than bad luck.

The same pattern repeats for other fields: diagnosis codes (trim the blank padding, match against
the code list), dates (parse, range check, not in the future), amounts (non-negative, paid not
greater than billed unless the business says otherwise).

### Layer 3: dataset-level checks

These catch problems no single row shows.

- **Row counts in versus out.** `rows_out = rows_in - duplicates_removed - rejects`. The
  identity must hold exactly. A violation means rows were lost.
- **Duplicate rate.** Track it per source and per batch. The sample file sits near 8.6%
  (2,835 of 32,932). A rate near zero or a large jump both deserve a look.
- **Null rates** per column against the contract and against history.
- **Distribution and volume.** Rows per day, distinct patients, distinct NDCs, amount totals.
  Compare with the same period in prior weeks.
- **Referential checks.** Every `Patient_ID` in the pharmacy file should exist in the member
  list, if there is one.
- **Idempotence.** Running the cleaner on its own output changes nothing.

### Where each check runs

| Check | Where | On failure |
| --- | --- | --- |
| Function behaviour, properties | Unit tests in CI | Block the merge |
| Schema and contract | Pipeline assertion at ingestion | Reject the batch |
| Field rules | Pipeline step, with a rejects table | Divert rows, continue |
| Counts, duplicate and null rates | Pipeline assertion at the end of the run | Fail the run if the identity breaks |
| Trends, thresholds, drift | Monitoring on the metrics tables | Alert an owner |

Start with the identity checks and the NDC format rule, since they are cheap and catch the most.
Add the rest as the data shows what actually goes wrong, and let business priorities order them.

## Scaling the de-duplication

### What the current version does

`dedupe_ndc_pharm_data` is built on polars:

- `scan_pharmacy` uses `pl.scan_csv`, which is lazy: nothing is read until the plan runs.
- NDC normalization is a vectorized string expression (`str.replace_all` then `str.pad_start`),
  executed in native code across whole columns, with no Python loop over rows.
- `clean_pharmacy` applies `unique(maintain_order=True)` to the lazy frame.
- The result goes out through `sink_csv`, which streams to disk instead of materializing a frame
  for writing.

Type annotations do not make any of this faster. They are not used at runtime, and the speed
comes from the vectorized, native execution and lazy planning. The annotations are checked by
mypy, which helps correctness, not performance.

The limit is `unique`. To know whether a row has been seen, it must remember every distinct row,
so memory grows with the number of distinct rows. That is fine for tens of millions of rows on a
typical machine and fails when the distinct rows exceed memory. Row counts for the summary come
from the same run as the write (`pl.collect_all` over lazy sinks and count queries that share one
plan), so neither the input nor the output is read again to count.

### Implemented: `buckets` and `keep_order`

`dedupe_ndc_pharm_data` and `dedupe_medical_data` (and the `--buckets N` / `--keep-order` CLI
options) implement the design below with polars, in `claims.write_unique_csv`, shared by both
cleaners:

1. Stage: the rows after the blank filter, NDC normalization (pharmacy) or code stripping
   (medical) get a `_row` index and a bucket `hash(whole row) % N`, and are written with
   `sink_parquet(pl.PartitionBy(..., key="_bucket"), row_group_size=8192)` to a scratch directory
   beside OUTPUT.
2. Dedupe each bucket on its own: sort by `_row`, `unique(keep="first")`, write Parquet. "First"
   is therefore the first in the input whatever order the sink wrote.
3. Combine: scan the per-bucket files and write the CSV. With `keep_order`, sort by `_row` first.
   The output row count is read from the Parquet footers.

A hash collision only puts different rows in one bucket; `unique` compares whole rows, so they are
never merged. `pl.PartitionBy` is unstable in polars 2.1, so pyproject pins `polars>=2.1.0,<2.2`
and a test exercises it. Defaults are unchanged: without `buckets` the single `unique` pass runs.

Row counts: no extra pass to count, and OUTPUT is never read back. The count of rows out comes
from the pipeline (`select(len)` on the `unique`, or the Parquet footers), and the input-side
counts run in one pass with the rejects sink (pharmacy) or the main sink (medical).

Why the unique sink runs alone. Running the main sink, the rejects sink and the
counts in one `pl.collect_all` raised peak RSS by about 0.7 GB (single pass) and 1 GB (staging) at
10M rows, because polars caches a subplan shared by two sinks and so materializes the cleaned
frame. Measured at 10M rows, staging alone 2.10 GB; with a rejects sink beside it 3.09 GB; with a
count query beside it 2.11 GB. A count query shares a plan for free; a second sink does not. So
the unique sink never shares a `collect_all` with another sink. A second cost was in the stage
itself: with 64 open Parquet writers at the default 122,880-row group, the writers buffered about
0.5 GB; `row_group_size=8192` brings staging to 1.58 GB.

Measured as above (best of two, laptop; see the README for the table): at 10M rows the previous
version's single pass peaks at 2.38 GB, this version's at 2.50 GB (within noise), and
`--buckets 64` at 1.74 GB. Phase by phase on the benchmark's 10.0M-row file: single 2,294 MB;
partitioned 1,755 MB; stage 1,625 MB; per-bucket dedupe 307 MB (13% of the single pass); one
`unique` over the staged rows 1,102 MB. The partitioned peak is the stage: reading and cleaning
the CSV costs about 1.4 GB (the mapped file plus polars' per-thread buffers for the NDC
expressions) before any de-duplication, so the end-to-end saving is the distinct-row table, not the
whole run. `keep_order` costs memory for the global sort, as the notes below predict. Wall time
includes about 0.35 s per million rows for the field-count validation pass (`csv` module);
replacing its loop with `set(map(len, reader))` saved only 6%, so it was left as is.

### Design for data larger than memory

The design the implementation follows (the sketch below is a simplification: it is not a
streaming pass in polars, and the sort for order is optional):

Idea: two identical rows have the same hash, so they land in the same bucket. Split the data into
buckets small enough to fit in memory, dedupe each bucket on its own, and the union of the results
is globally de-duplicated.

```
P = number of partitions, chosen so that (distinct rows / P) fits in memory

# Pass 1: partition (streaming, one read of the input)
open P output files part_0 .. part_{P-1}
for each chunk of the input:
    normalize NDC in the chunk
    for each row in the chunk:
        key = hash(all columns of the normalized row) mod P
        append row to part_key

# Pass 2: dedupe each partition independently (parallel across partitions)
for k in 0 .. P-1, in parallel workers:
    seen = empty set
    for each row in part_k:
        if row not in seen:
            add row to seen
            write row to dedup_k

# Pass 3: combine
concatenate dedup_0 .. dedup_{P-1} into the output
```

Correctness: the hash is computed over the full normalized row, so any two duplicates go to the
same partition, and no duplicate can survive across partitions. The hash must be stable across
workers (not a per-process randomized hash).

Cost: with N rows, each pass is linear. Time is O(N) in expectation: one read and write for
partitioning, one for deduping, one for the final concatenation. Memory per worker is
O(N_distinct / P). Disk I/O is about 3 reads and 3 writes of the data. Skew matters: if one row
value is extremely common, its partition is large in rows but small in distinct rows, which is
fine for a set-based dedupe. If many distinct rows hash to one bucket, re-partition that bucket
with a second hash.

Ordering: this does not preserve input order. If first-occurrence order matters, carry the input
row number through the partitions and sort the output by it at the end (an external sort on one
integer key), or keep the minimum row number per row and sort by that.

Alternatives:

- **External sort, then drop adjacent duplicates.** Sort the normalized rows with an external
  merge sort (sorted runs that fit in memory, then a k-way merge), then stream through and skip
  any row equal to the previous one. O(N log N) time, O(memory budget) space, and the output is
  sorted. Simple and robust, slower than hashing.
- **`SELECT DISTINCT` in an engine built for it.** DuckDB spills hash aggregates to disk on a
  single machine; Spark (`dropDuplicates`) does the same partition-by-hash step across a
  cluster. The algorithm is the one above, with the engine managing the shuffle and spill. For
  data that already lives in a warehouse or a lake, this is the lowest-effort option.

A less exact option, when a small error is acceptable, is a Bloom filter in front of the set to
skip rows that are definitely new. It cannot replace the exact check, because false positives
would have to be resolved against the real data.

## Reconciling multiple sources of truth

Two systems often describe the same person. A website chat bot records an inquirer's name, email
and a short message. If the inquirer is also a product user, the email in the chat record may or
may not agree with the user in the backend database. The warehouse has to hold both without
pretending they always agree.

**Fix identity as early as possible.** The cheapest consistency comes from the source. If product
users must sign in to use the chat bot, the chat record can carry the user ID, and the name and
email match by construction. Where that is not possible, resolve identity at ingestion rather than
leaving the records disparate until someone needs them joined.

**Layers.**

- **Raw.** Land each source as delivered, append-only, with source name, load time and batch ID.
  Never edit it. It is the audit trail and the way to replay.
- **Staging.** Light cleaning per source: types, trimming, lower-casing emails, one schema per
  source. No cross-source logic yet.
- **Conformed.** The reconciled model: one person (or `party`) table with a stable internal key,
  plus a link table from each source record to that key.

**Matching.**

1. Deterministic first: exact match on a normalized key. A verified email is a good key; the user
   ID is better when present.
2. Fuzzy second: for records that do not match exactly, compare name, email local part and domain
   with a similarity score, and match above a threshold. A classifier can do this when the volume
   or the stakes justify it.
3. Keep the middle band for review. Store the match method and score on the link record so
   matches can be audited and re-run when the rules change. A wrong merge is costlier than a
   missed one, so be conservative.

**Source-of-record precedence.** Decide per attribute which system wins. For example, the
product database is authoritative for account email, and the chat bot is the only source of the
message text. Write the rule down and apply it in one place.

**Keep both values, with lineage.** Do not overwrite a disagreeing value. Store each source's
value alongside its source, timestamp and batch, and expose a derived "best" column chosen by the
precedence rule. A disagreement is itself information: it can mean a typo, a work email versus a
personal one, or a changed address.

**Survivorship rules.** For attributes with no clear authority, pick by a deterministic rule:
most recently updated, most complete, or highest-trust source, in that order. Break remaining
ties by source priority so reruns give the same answer.

**Other things to design for.** Changes over time (slowly changing dimensions or a bitemporal
model, so history can be queried as of a date), late-arriving data, deletions and privacy
requests that must reach every layer, one person with several emails, and shared emails such as
a family inbox.

## Join cardinality

Let table A have `n` rows with value J in column X, and table B have `m` rows with value J in
column Y, with `n > 0` and `m > 0`. Joining on X = Y produces `n × m` rows containing J, for an
inner, left, right or full outer join alike.

Each of A's `n` rows matches every one of B's `m` rows, so each of A's rows is repeated `m`
times. All four join types keep matched pairs. They differ only in what they do with unmatched
rows, and the J rows are all matched on both sides, so no join type adds or removes any of them.

Worked example, `n = 2`, `m = 3`:

```
A (X)        B (Y, label)
J            J  b1
J            J  b2
             J  b3
```

The join on `X = Y` pairs each A row with each B row:

```
A.X  B.Y  B.label
J    J    b1       (A row 1, B row 1)
J    J    b2       (A row 1, B row 2)
J    J    b3       (A row 1, B row 3)
J    J    b1       (A row 2, B row 1)
J    J    b2       (A row 2, B row 2)
J    J    b3       (A row 2, B row 3)
```

Six rows, `2 × 3`. Unmatched rows with other key values (or NULL keys, which never match) appear
only in the outer joins and do not change the count for J. This fan-out is the usual reason a
join inflates row counts and totals: aggregate or de-duplicate one side first when the intent is
one row per key.
