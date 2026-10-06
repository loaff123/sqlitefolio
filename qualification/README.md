# Independent semantic qualification

This directory contains a separately authored native-SQLite reference and original
benign fixtures. It does not import SQLiteFolio's value encoding, snapshot,
invariant, or comparison helpers. At integration boundaries it calls only the
public candidate runner and summary builder. Packet tests invoke the real CLI;
the report renderer is used to demonstrate the documented consistency boundary.

## Corpus and independence

- `fixtures/frozen` retains all 19 files of the earlier frozen corpus byte-for-byte:
  12 original cases, 30 original named checks, handwritten predictions, independent
  oracle, initial validation evidence, and freeze records
- `additional_cases.json` contains ten newly authored cases and 29 handwritten
  named checks; `ADDITIONAL_FREEZE.json` binds their exact bytes
- All 22 cases are exercised both from original schema/seed scripts and from an
  independently constructed, quiescent DELETE-journal sample database, totaling
  44 executions; 24 executions belong to the unchanged original corpus
- `author_additional.py` preserves the authoring record and refuses overwrite
- Predictions were not adjusted to match the implementation; protocol clarifications
  about serialized runtime fields, native raw query ordering, and fresh connection
  settings were applied explicitly to the independent reference

Fixtures cover duplicate multiplicity, rename/no-common-column projections,
generated columns, sqlite_sequence, STRICT/WITHOUT ROWID/composite keys, composite
foreign keys and cascades, expression/partial indexes, declaration-only CHECK
loss, NULL and integer boundaries, infinity and negative zero as SQLite observes
them, invalid-UTF8 TEXT versus BLOB bytes, ordered/bag checks, strict booleans,
partial errors, triggers, invalid views, and controlled-close transaction state.

Every raw observation is compared recursively, including runtime/profile,
exact interpreter build/implementation/machine, execution error and transaction
state, schema declarations, all column/table
flags/index/FK metadata, every typed table row, every diagnostic, view preparation,
sequence values, and all three query phases. Independently reconstructed checks,
statuses, object changes, and multiset projections are compared field-for-field.
File and directory fingerprints are measured before/after each execution.
Original auxiliary live and reopened expectations are validated as well.

## Run

From the project root, with the qualified Python/SQLite runtime:

    PYTHONPATH=src python qualification/independent_semantics.py --output result.json
    PYTHONPATH=src python -m unittest discover -s tests -p test_independent_semantics.py -v

For only the independent native reference, without importing SQLiteFolio:

    python qualification/independent_semantics.py --reference-only --output native.json

For installed-wheel testing, leave source directories off PYTHONPATH and run the
same copied tests/qualification/fixtures tree with the wheel environment's Python.
The packet test subprocess resolves the same package as the test process.

## Limits of these results

This is a bounded, deliberately selected synthetic corpus, not a statistical
reliability sample. Both reference and candidate use the same SQLite engine, so
agreement cannot establish engine correctness, universal migration safety,
production suitability, crash durability, or superior detection over competent
SQLite recipes and existing migration tools. No hostile parser/security tests,
downloaded SQL, live/WAL sources, extensions, or network SQL are used.

`TAMPER_REPLAY_PLAN.md` describes packet consistency versus source-bound replay.
A completely consistent replacement of raw observations may verify structurally;
replay is the separate check that reruns explicitly trusted original input.
