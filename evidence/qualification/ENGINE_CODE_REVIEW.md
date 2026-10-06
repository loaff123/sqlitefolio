# Independent engine/code review

## Verdict

Approved for the explicitly bounded trusted-input alpha scope. All four correctness findings below have been repaired and independently re-executed: the five review regressions pass, including the public packet path and the minimal internal-error envelope. README and the protocol now explicitly disclose all three scope narrowings. No false-pass condition was found in the reviewed engine or summary code. This is a bounded trusted-input code review, not an assurance about arbitrary SQLite input or universal migration safety.

## Scope and evidence

Authority: `docs/protocol-v1.md`, including explicit pre-release clarifications and scope refinements. Reviewed `runner.py`, `worker.py`, `guardrails.py`, `snapshot.py`, `values.py`, and `summary.py`; inspected the corresponding verifier checks at cross-boundaries.

I did not rerun the existing 118-test suite or the existing 44-case native parity qualification. Their prior green artifacts were inspected. New testing used small original benign SQL, fresh owned temporary files, and Python-level observation-failure injection. No live databases, downloaded SQL, native parser/serialization security investigation, extensions, hostile native inputs, or network SQL were used. No implementation or frozen fixture was edited by this reviewer. All reviewer regressions are in the new `tests/test_engine_review.py`.

## Findings and dispositions

### 1. Required baseline query limit could create an invalid execution envelope (fixed)

Original location: `worker.py:158-159` before review repair; current clean-state assignment is near `worker.py:173-174`, with centralized priority near `worker.py:85-97`.

Exact benign setup:

- schema: `CREATE TABLE t(x);`
- seed: `INSERT INTO t VALUES(1);`
- `rows_per_query=1`
- query_preserved: `SELECT x FROM t UNION ALL SELECT x FROM t WHERE x=1`
- migration: `UPDATE t SET x=2; BEGIN;`

The baseline query exceeded its per-query limit while all snapshots and both later queries completed. The migration then overwrote execution status with `open_transaction` while retaining an error of kind `resource_limit`. Public `rehearse` raised `VerificationError: non-error execution contains an error` before publication.

Bounded repair: preserve higher-priority failure status and only set no-error open_transaction from a clean completed state. Regressions assert the transaction remains visible, raw status/error agree, and the public packet validates as nonpassing with exit 4.

Evidence: `engine-review-initial.log` (red); `engine-review-query-fault-red.log` shows both related tests green after repair.

### 2. Optional query_equals baseline limits incorrectly blocked an otherwise passing rehearsal (fixed)

Location: `worker.py:100-111` after repair.

Use the same source, query and row limit, change the invariant to query_equals with expected `[[["integer","2"]]]`, and run `UPDATE t SET x=2;` without BEGIN. Both required after-state query checks and all snapshots pass. Previously the optional baseline resource failure promoted execution to resource_limit, producing overall incomplete contrary to the protocol's explicit rule that query_equals does not require its baseline query to succeed.

Bounded repair: retain the baseline's exact incomplete query fact without promoting that optional resource failure to execution status. Global evidence exhaustion still affects later required collection and cannot become a false pass.

Evidence: `engine-review-initial.log` (red), subsequent focused run green.

### 3. Unexpected query observation failures were mislabeled ordinary failed checks (fixed)

Location: `worker.py:103-106` after repair.

Python-level fault injection makes only the row encoder for benign `SELECT 777` raise `RuntimeError("synthetic observation failure")`; other native snapshot rows use the original encoder. Previously all query phases correctly recorded error.kind=worker_error, but execution remained completed and the summary became failed/exit 1.

Bounded repair: propagate worker_error from every query phase, including optional baseline phases, with existing highest-priority failure handling. Focused regression verifies complete native snapshots remain present, the query error is retained, and the final result is internal_error/exit 5.

Evidence: `engine-review-query-fault-red.log` (red), `engine-review-error-envelope-red.log` shows this test green after repair.

### 4. Minimal worker-error envelopes were incompatible with low evidence caps (fixed)

Location: `verify.py:403-408` at discovery, paired with `worker.py:_bounded_result`.

Use 64 benign SELECT 777 invariants, evidence_bytes=4096, and the same Python-level fault injection. The engine correctly retains worker_error while collapsing snapshots to null and every query slot to not_run. Mandatory framing is about 28 KiB, below the fixed 20 MiB response ceiling but above the lowered evidence cap. The verifier previously permitted this exemption only for resource_limit and rejected the valid internal-error frame.

Bounded repair: permit worker_error (and the explicitly supported source_changed failure envelope) only with the existing strictly minimal null/not_run shape, matching status/error kinds, and unchanged absolute response cap. Do not allow collected over-budget observations or passing results.

Evidence: `engine-review-error-envelope-red.log` (red); `engine-review-final.log` (all five review tests green); regression `test_internal_error_minimal_envelope_survives_lowered_evidence_cap`. The final verifier allows only resource_limit, worker_error, or source_changed to use the strictly minimal framing exception.

## Supported-scope clarifications

- Ordinary VACUUM is denied because SQLite uses internal ATTACH. This produces an honest unsupported result and is now explicitly documented in README and the protocol.
- The 256-column cap applies to invariant result and expected row widths as well as table columns. A 257-column native SELECT is refused, and the narrowing is now explicit in README and protocol.
- A zero-byte file produced by `sqlite3.connect(path).close()` is a valid native empty SQLite starting point but is refused because the supported database-file path requires an initialized SQLite header and DELETE-journal markers. Empty schema/seed SQL is the supported alternative. This is now explicit in README and the protocol.

## Additional implementation assessment

- Application schema, seed and migration scripts run through their own native executescript calls, with no added transaction wrapper. Fixed internal connection setup occurs before the authorizer; the authorizer is installed before any application script or invariant preparation.
- Baseline construction and candidate execution use distinct connections. Candidate and reopened FK profiles are reapplied, and observed versus reopened transaction state is kept separate. A benign deferred-FK COMMIT error preserved the native SQLite error and open transaction while reopen reflected controlled rollback.
- Snapshots retain native table_xinfo/table_list, complete index_xinfo including expression/auxiliary columns, FK rows, generated columns, sequence facts, sorted typed row multiplicity and view-preparation diagnostics. Typed TEXT/BLOB discrimination is at the driver boundary. A UTF-16le source with multibyte identifiers/text round-tripped as exact observed UTF-8 typed text.
- Read-only invariants and views use the authorizer before preparation. Native metadata PRAGMA table functions, ordinary ANALYZE and REINDEX, and JSON query values worked in focused benign probes. Recursive view errors remained visible failed diagnostics.
- Byte/row accounting occurs before accumulation; incomplete observations do not create complete numeric change claims. VM phase resets, deadline checks, process-group kill/reap, fixed subprocess command, DEVNULL output, bounded response reads, and owned workspace cleanup are straightforward and appropriately separated.
- Runtime qualification checks the specified implementation/minor/version/platform/architecture, while runtime provenance and replay identity now include exact interpreter build, SQLite source ID and compile options. This is a narrow compatibility qualification, not an engine authenticity or correctness proof.
- The production summary's common-column multiset comparison, explicit boolean checks, diagnostic gates, incomplete propagation and error ordering are clear and do not infer row identity.

## Connection-lifecycle improvement (fixed)

At initial review `runner.runtime_profile` used a sqlite3 connection context manager without an explicit close. SQLite's connection context manager controls a transaction; it does not close the connection. This makes repeated in-process profile calls depend on garbage collection. The implementation now uses contextlib.closing. A real-connection regression confirms a ProgrammingError when the retained connection is used after runtime_profile returns. This improvement was independently rechecked along with the five review regressions.

## Final independent verification

Command: `PYTHONPATH=src:tests python -m unittest -v test_engine_review test_engine_limits.EngineLimitContract.test_runtime_profile_closes_its_native_connection`

Result: 6 focused tests passed on the qualified CPython 3.12 / SQLite 3.53.1 runtime. Log: `engine-review-final.log`. Reviewed-file SHA-256 values are recorded in `engine-review-file-hashes.txt`. Implementation workers also reran the wider suite; this reviewer did not duplicate their full-suite or native-parity runs.

No engine/code-review blockers remain. The report records reviewed implementation behavior; the parent owns final aggregate, installation, distribution, and release checks.

Final documentation check: the protocol now explicitly permits a pre-existing worker_error/source_changed status in the minimal null-snapshot/not_run error envelope, while retaining the fixed 20 MiB response cap and refusing retained over-budget facts. This resolves the last wording inconsistency discovered during review. The three conservative SQL/database narrowings remain explicit in the current README and protocol.
