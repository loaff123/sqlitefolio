# Capable baselines

Capability research and original toy runtime observations dated 2026-10-05.
This comparison is deliberately generous to incumbents. The same explicit
sample and invariant checks can be supplied to a competent SQLite recipe.

- [Atlas migration testing](https://atlasgo.io/testing/migrate): fixture seeds,
  SQL assertions and expected errors already exist. Its Pro/account-gated
  harness was credited from documentation, not runtime-tested here. v1.3.0 was
  the researched release; current entitlement was not verified by login
- [sqlite-utils migrations](https://sqlite-utils.datasette.io/en/4.0/migrations.html):
  version 4.2.1 was installed and exercised on original benign scripts. Verbose
  mode prints before/after schemas and unified diff including triggers/indexes.
  [sqlite-migrate](https://github.com/simonw/sqlite-migrate) is now a compatibility
  wrapper, not evidence of a missing modern capability
- [Alembic batch operations](https://alembic.sqlalchemy.org/en/latest/batch.html):
  Alembic 1.20.0 / SQLAlchemy 2.1.3 were exercised on original examples. Batch
  migrations and ordinary Python assertions are capable alternatives; warnings
  about unnamed CHECK constraints deserve credit
- [dbmate](https://github.com/amacneil/dbmate): SQLite, SQL-first migrations,
  transactions and schema export paired with inspection; documentary credit
- [Liquibase SQLite](https://contribute.liquibase.com/extensions-integrations/directory/database-tutorials/sqlite/):
  actual updates/rollback testing, preview, scalar sqlCheck preconditions and
  object diffs are relevant. Preconditions are not mislabeled postconditions;
  documentary credit only
- [SQLite sqldiff](https://sqlite.org/sqldiff.html): use a strong recipe including
  typed data and full sqlite_schema comparison, not a deliberately weak
  schema-only baseline

In the original toy baseline, sqlite-utils and Alembic both applied an
add/backfill/index/CHECK/audit-trigger scenario and passed six checks. Both
rejected a deliberately false Python assertion and rolled back its DML/trigger
effects. No arbitrary DDL rollback or crash-safety conclusion follows. Their
native invocations did not automatically emit a unified typed partial-state
packet; custom surrounding code can. That is an observed packaging difference,
not detection superiority.

Primary motivation includes [sqlite-utils #611](https://github.com/simonw/sqlite-utils/issues/611)
on trigger/index preservation during transforms and [Alembic #1207](https://github.com/sqlalchemy/alembic/issues/1207)
on view-dependent table rebuilds. These are historical user reports, not newly
reproduced current defects or measured demand for this product. The
[SQLite forum discussion](https://sqlite.org/forum/info/4bfd78f2c46ea045?t=c)
about NOT NULL/schema-diff coverage also credits full sqlite_schema inspection
as the incumbent remedy.
