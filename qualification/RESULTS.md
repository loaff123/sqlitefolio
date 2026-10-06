# Independent qualification results

Qualified runtime: Linux x86_64, CPython 3.12.14, SQLite 3.53.1. Exact interpreter
build, implementation, machine, SQLite source ID, and compile options are retained
in the generated JSON evidence.

Both exact interpreter builds passed the unchanged 44-execution semantic corpus:
- Original environment: Aug 25 2026, 14:00:49, Clang 22.1.3
- Publicly obtainable runtime: Sep 24 2026, 17:57:43, Clang 22.1.3

The publicly obtainable runtime also passed all 17 targeted independent tests and
the then-current full project suite of 125 tests. The replay boundary test changes
only the recorded interpreter build while retaining version 3.12.14 and is rejected.
Repeated qualification on a second build does not increase fixture or check counts.

## Native semantic comparison

- 12 original frozen cases and 30 original checks retained without modification
- All 19 original corpus files verified byte-for-byte against their originals
- 24 original schema/file executions plus 20 executions of ten new benign cases
- 29 additional named checks; 16 original auxiliary live/reopened expectations
- Every raw object, column/table flag/index/FK, view, diagnostic, sequence, typed row,
  query phase, error and transaction fact matched independent direct SQLite
- Every producer summary and independent structural-verifier reconstruction
  matched independently calculated check outcomes and common-column projections
- Zero mismatches across all 44 executions
- Source bytes and source directory contents unchanged throughout

Before the exact-build metadata extension, the serialized comparison traversed
13,568 raw scalar facts and 1,884 derived summary scalar facts. The three added
runtime fields contribute another 132 raw scalar facts across the same 44 runs. These are coverage counts, not statistical confidence or
performance measures. Duplicate values and multiplicities are compared exactly.

## Packet boundaries

Nine tests use real CLI-created packets. Stale raw observations, false summaries
with regenerated HTML, and changed retained SQL with stale hashes are rejected.
Exact original source and migration bytes and runtime facts are bound by replay.
A real incomplete packet remains nonpassing, and no numeric loss is inferred.

Two deliberately coherent replacements are accepted by structural verification:
consistent synthetic observed/reopened rows with independently rebuilt summaries,
and substituted retained SQL with refreshed file digests. Both are rejected by
source-bound replay against the explicit original manifest. This confirms the
published distinction: structural verification checks internal consistency and
hashes; it does not authenticate authorship or native truth.

## Defects found and resolved during integration

- Ordinary SQLite ALTER operations incorrectly encountered the temporary-object
  guard; table/column renames and dropped columns now retain SQLite behavior
- Runtime platform representation differed from the frozen serialized convention
- Open transactions with no SQLite exception incorrectly fabricated an error
- Source-construction connection settings could leak to candidate execution

Protocol ambiguities about canonical JSON, connection FK settings, runtime fields,
raw query order, and error representation were explicitly clarified. Frozen input
SQL and handwritten expected outcomes were not rewritten to conceal differences.

## Scope

Agreement is limited to these original trusted synthetic fixtures and this
qualified runtime. The independent reference uses the same SQLite engine. It is
not evidence of universal correctness, production safety, hostile-input isolation,
crash durability, adoption, performance superiority, or greater detection power
than competent native SQLite recipes or existing migration tools.
