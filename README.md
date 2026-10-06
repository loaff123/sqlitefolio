# SQLiteFolio

A local review packet for an exact SQLite migration on a trusted sample database.

SQLiteFolio preserves your source, runs each SQL candidate in a fresh disposable
copy, and joins typed row changes, schema/index/trigger/FK evidence, declared
checks, SQL errors and transaction state. It separately shows the state observed
in the migration session and the state reopened after controlled close.

**Alpha. Trusted samples only. A pass is not a production-safety verdict.**
There is no production connection, apply command, migration generator, automatic
winner, hosted service, telemetry or network operation.

## Install and check the runtime

The initial qualified profile is Linux x86_64, CPython 3.12 and SQLite 3.53.1.
Python 3.12 alone does **not** select that SQLite version. Other versions are
refused rather than silently treated as qualified. See [runtime details](docs/profile.md).

From a repository checkout or extracted source distribution, build and install
the wheel with a qualified Python (see the checksum-pinned runtime recipe above):

```sh
python -m venv .venv
.venv/bin/python -m pip install "setuptools==84.0.0"
.venv/bin/python qualification/build_dist.py
.venv/bin/python -m pip install ./dist/sqlitefolio-0.1.0a1-py3-none-any.whl
. .venv/bin/activate
sqlitefolio profile
```

The package has no runtime dependencies. Distribution building uses setuptools.
A matching version is only the beginning: source ID, compile options and exact
Python patch are recorded in every packet. Replay requires the same recorded
runtime. No engine correctness or security claim follows from qualification.

## A complete first review

The original synthetic inventory example has a row-preserving rebuild that loses
an index, trigger and CHECK declaration, plus a corrected rebuild. Existing
migration tools can reveal these facts; the packet makes them reviewable together.
Run from that same source directory with the virtual environment still activated:

```sh
sqlitefolio rehearse examples/rebuild/manifest.json --out review --trust-input
sqlitefolio inspect review > review-copy.html
sqlitefolio verify review
sqlitefolio replay review --manifest examples/rebuild/manifest.json --trust-input
```

The single corrected candidate returns 0. Open `review/report.html` in your own
browser. It is static, escaped HTML with no scripts or remote assets. The packet
contains complete raw evidence and the exact SQL files, not just rendered counts.

For the intentionally faulty/corrected comparison:

```sh
sqlitefolio rehearse examples/rebuild/comparison.json --out comparison-review --trust-input
sqlitefolio verify comparison-review
sqlitefolio replay comparison-review --manifest examples/rebuild/comparison.json --trust-input
```

All three commands return **1 intentionally** because one candidate fails the
named preservation checks. A failed candidate does not stop observation of its
partial state or execution of the next candidate. There is no automatic ranking.

`--trust-input` is your explicit assertion that the selected SQL and sample are
trusted. Never use it with arbitrary downloaded SQL, live databases or production
files. Guardrails are accidental-misuse containment, not a hostile-input sandbox.

## Your inputs

A strict UTF-8 JSON manifest names either a quiescent DELETE-journal SQLite file
with an initialized SQLite header (zero-byte files are refused), or schema/seed
SQL, an explicit FK/autocommit profile, candidate SQL and typed
invariants. File references are relative to the manifest, with no symlinks,
hardlinks, interpolation or `..`. Unknown keys and duplicate JSON keys are errors.

```json
{
  "format": "sqlitefolio.input.v1",
  "scenario": "inventory",
  "source": {"database": "sample.db"},
  "profile": {"foreign_keys": true, "transaction_mode": "autocommit"},
  "candidates": [{"name": "proposal", "sql": "migration.sql"}],
  "invariants": [
    {"name": "preserve inventory", "kind": "row_count", "table": "inventory", "expected": "baseline"},
    {"name": "FKs clean", "kind": "no_foreign_key_violations"}
  ]
}
```

Invariant kinds: `query_equals`, `query_preserved`, `row_count`, exact
`object_present`/`object_absent`, `no_foreign_key_violations`, `integrity_ok`.
Queries explicitly choose ordered or bag comparison. Typed values distinguish
NULL, i64 integers, IEEE-754 binary64 reals, exact UTF-8 TEXT bytes and BLOB bytes;
see the [complete protocol](docs/protocol-v1.md). Bag multiplicity is retained.
Boolean queries require exactly one INTEGER 0/1 cell. No Python truthiness or
implicit string/number casts are used.

Each comparison has one source and one profile. Use separate manifests for
separate scenarios; results across different profiles are not ranked together.
Your SQL runs as written with no transaction wrapper, error retry, synthetic
commit or automatic rollback. An open transaction never passes, even if its
in-session checks pass. Reopened state follows ordinary controlled close, not a
crash-recovery experiment.

## Packets, verification and replay

- `packet.json`: raw before/observed/reopened facts, errors and derived summaries
- `report.html`: static local review, with raw facts beneath every candidate
- `manifest.json`: normalized declared contract
- `inputs/`: exact original manifest and selected SQL
- `sample.db`: only with explicit `--include-sample` and a database source

By default the sample database is omitted. Row evidence still contains **all
observed sample values**. Treat packets as sensitive if the sample is sensitive;
review them before sharing. SQLiteFolio never uploads them.

`verify` does not execute SQL. Its separately implemented evaluator reconstructs
all displayed summaries, projection counts and named check verdicts from raw
facts, checks shapes/file hashes, and checks deterministic report bytes. A
rehashed false summary fails. A fully fabricated, internally consistent set of
facts can still verify: hashes **do not authenticate a submitter or native truth**.

`replay` explicitly reruns trusted inputs, binds exact SQL/source/contract/runtime,
and compares the raw evidence. It is stronger source-bound evidence, but uses the
same SQLite engine and is not an independent engine-correctness proof. You must
supply the original manifest/source, including a DB omitted from the packet.

## Bounds and refusal behavior

Source <=16 MiB; each SQL <=1 MiB; eight candidates; 64 invariants;
128 tables; 512 schema objects; 256 columns/table/query; 10,000 rows/table/query;
100,000 total observed rows/candidate; 64 KiB/value; 16 MiB evidence/candidate;
32 MiB packet JSON; 64 MiB rendered HTML. Workers have a 30-second wall deadline,
2,000,000 counted VM instructions per phase, 512 MiB address-space ceiling and
32 MiB disposable DB cap. Selected limits can only be lowered. See the protocol
for every bound and exact status semantics.

Incomplete observations never become zero-loss claims or passing checks. Resource
exhaustion, SQLite errors and unexpected worker/protocol failure remain distinct.
Small samples are the supported workflow; streaming/large-database support is not
implemented. Virtual tables, temp objects, extensions, custom UDFs/collations,
ATTACH/DETACH, VACUUM (including ordinary VACUUM), external-file SQL, network and live/WAL sources are unsupported.
Views are checked for preparation/name resolution, not all-query correctness.
Raw CHECK declarations are compared, not semantically normalized.

Output parents must already exist. Existing destinations, including empty
directories and racing writers, are never overwritten. Linux
`renameat2(RENAME_NOREPLACE)` is required, with no check-then-replace fallback.
Failure after publication is reported honestly and preserves the packet.
A closed stdout after successful publication returns 7; inspect the output path.

Exit codes: 0 passing bounded checks; 1 failed checks/diagnostics; 2 invalid or
unsupported input; 3 SQLite execution error/open transaction; 4 incomplete or
resource/source-change refusal; 5 unexpected internal/worker error; 6 structural
tamper or replay mismatch; 7 publication/I/O failure. A structurally valid but
nonpassing packet keeps its nonzero result. Exit 0 never means universally safe.

## Tests and evidence

[Public qualification evidence](evidence/README.md) preserves original failing
attempts as well as passing runs. The [CI workflow](.github/workflows/ci.yml) pins
the publicly qualified runtime archive and retains fresh logs/build artifacts.
Browser visual/interactivity QA remains unqualified: static report tests and a
separately labeled offline rendering do not establish browser behavior.


```sh
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python qualification/independent_semantics.py --output parity.json
```

The 12 original feasibility cases and 30 checks remain frozen under
`fixtures/frozen`, along with the original independent predictions. Their
24 schema/file executions remain distinct from later additional cases and from
production tests. The reference observer imports no production observation or
summary helpers. See [qualification](qualification/README.md).

## Existing tools deserve credit

Atlas already supports seeded migration tests, SQL assertions and expected errors.
Alembic batch migrations plus Python assertions are a strong baseline.
Current sqlite-utils includes migration machinery and verbose before/after
schema diffs. dbmate, Liquibase and competent SQLite/sqldiff recipes cover much
of this workflow. [Comparison and sources](docs/competitors.md)

SQLiteFolio claims integrated review convenience, not unique detection,
performance superiority, measured adoption, incident reduction or novelty in the
underlying SQLite algorithms. All included examples are original synthetic data.

## License

MIT. SQLite and other tools retain their own licenses; no third-party engine is
bundled in the wheel.
