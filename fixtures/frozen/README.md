# Independent SQLite migration rehearsal corpus

This is a feasibility corpus of 12 original, benign, hand-authored cases across six workflow scenarios. It is not a statistical reliability sample or a migration security test. It contains no downloaded migration SQL, extension loading, ATTACH, arbitrary paths, or production databases.

## Frozen inputs and interpretation

- `01_*.json` through `12_*.json` are the independently authored inputs. Each has `schema_sql`, `seed_sql`, `migration_sql`, an explicit `foreign_keys` execution profile, and read-only `checks`.
- A check's `expect_rows` is the **desired invariant**, not necessarily the expected outcome of the faulty migration. A mismatch or SQL error is an intended check failure in several cases.
- `expected.json` was handwritten before executing the scripts. It predicts actual migration status, open-transaction state, final FK mode, check outcomes, and additional live/committed observations. None of the initial predictions required correction.
- `author_fixtures.py` preserves the authoring record and refuses to overwrite predictions. It is not part of the prototype.
- `oracle.py` is independent standard-library SQLite code, with no prototype imports. `oracle-prediction-validation.json` records all raw facts and the successful initial validation of all 12 predictions.
- `FROZEN_SHA256.json` records the hash of each input, prediction, authoring record, oracle, and initial measurement before any prototype execution. `SHA256SUMS` provides standard hash verification.

The status rule gives SQL exceptions priority (`error`), then reports `open_transaction` for a script with no SQL error that leaves a transaction open, otherwise `applied`. An `error` can independently have `in_transaction=true`. Neither local passing checks nor `applied` establishes migration safety.

## Scenarios

1. Safe evolution: additive/default/index migration; multi-statement trigger with body/literal semicolons; composite WITHOUT ROWID primary key
2. Rebuild preservation: row-preserving rebuild drops a CHECK constraint, an index, and an audit trigger
3. Referential integrity: FK-enabled parent rebuild cascades away children; explicitly FK-off correct copy preserves them; FK-off deletion introduces an orphan
4. Value preservation: lossy text-to-integer conversion while preserving the row count
5. Failure and transaction state: unique collision after earlier committed statements; same collision within an open transaction; successful script without COMMIT
6. Dependent schema: a view survives but references a column removed by table recreation

## Execution contract

The oracle builds a fresh source, seeds and commits it, and uses SQLite's backup API to make a disposable rehearsal copy. It configures foreign_keys before schema setup and independently on the migration connection. It then uses one `executescript` call on the migration exactly as written. It adds no transaction wrapper and performs no synthetic commit or rollback before observing outcomes.

Facts include live rows, schema definitions, column/PK metadata, PRAGMA foreign_key_check, PRAGMA integrity_check, view prepare results, and check results. A separate connection reports committed visibility while an unfinished transaction is still open. Closing the migration connection rolls back an open transaction as normal; reopening confirms that persistent facts remain unchanged. The source is never migrated.

Run after verifying the freeze:

    sha256sum --check fixtures/SHA256SUMS
    python fixtures/oracle.py --output /tmp/sqlite-oracle-recheck.json

Run these commands from the assessment root. Keep new measurements outside the frozen fixture directory, or use distinct names; never overwrite the frozen evidence.

## Deliberate limits

The oracle trusts these original inputs. It is not an SQL sandbox, an injection test, a proof of schema-equivalence, a performance assessment, or a predictor for unseen data. The corpus uses small whole-table observations for independent ground truth and does not claim production-scale coverage. Detecting changes is separate from deciding whether a change was intended.
