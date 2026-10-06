# SQLiteFolio input, output, verification, and CLI review

Review date: 2026-10-05. Authority: `docs/protocol-v1.md`, including the
pre-release exact-runtime clarification. Scope: `contract.py`, `paths.py`,
`publish.py`, `packet.py`, `report.py`, `verify.py`, and `cli.py`.

## Final verdict after independent repair review

**Specification verdict: pass for this reviewed input/output/verification scope.**
All six confirmed findings below have been repaired. No remaining release
blocker was identified within this review's stated scope and limitations.

**Code-quality verdict: acceptable for the bounded trusted-sample alpha.**
The repairs correctly distinguish stage naming from ownership, inspect lexical
path components before normalization, and bind consumers to the exact verified
bytes. Existing dense one-line coordinator/CLI code remains a nonblocking
maintainability concern, not a correctness reason to require a larger rewrite.

The reviewer independently reread all repaired branches and ran **28 focused
tests, all passing**: all 11 new regression/control tests plus the 17 existing
boundary controls listed below. Evidence:
`output-boundaries-independent-rereview-green.log`. The implementer's earlier
11-test green run is preserved separately in
`output-boundaries-review-green.log`. The original red evidence is retained.

### Repair verification

1. `publish.py:33,37-38,75-76`: `stage_owned` becomes true only after successful
   `mkdir`; collision failure cannot enter recursive stage cleanup
2. `verify.py:624-625,687-693` and `cli.py:52-55`: verification returns SHA-256
   digests computed from the exact packet/report bytes it validated. Inspection
   uses a bounded no-follow regular-file capture and emits those in-memory bytes
   only after matching the verified report digest
3. `packet.py:78-83`: replay compares the bounded captured packet bytes against
   the verifier's packet digest before parsing/binding/rerunning. Different bytes
   produce mismatch exit 6 before any replay SQL. This is a valid narrow
   alternative to returning the full verified capture: the digest identifies
   the original validated byte sequence, and the bytes compared are precisely
   those subsequently consumed. There is no further mutable-path reread
4. `paths.py:18-33`: lexical path traversal checks each component before reducing
   a following `..`; input/output regressions now reject the symlink path
5. `report.py:27`: the link now says “Underlying raw facts and diagnostics”
   without asserting completeness
6. `contract.py:35-42`: canonical encoding rejects exponent-overflow nonfinite
   values and the existing exception boundary converts the refusal to InputError

The reviewer edited only the new regression file and this report; the
implementation owner performed the production repairs. No full-suite or native
qualification rerun was performed by this reviewer.

## Initial verdict (superseded by the repair review above)

**Specification verdict: changes required before release.** Four material
boundary defects violate owned-stage cleanup, rejection of lexical symlink
components, verified inspection, and source-bound replay. Two smaller contract
defects concern incomplete-evidence labeling and nonfinite JSON decoding.

**Code-quality verdict: changes required.** The module boundaries, independent
summary evaluator, deterministic renderer, bounded reads, explicit trust flag,
and no-replace publication primitive are sound foundations. The remaining
problems are concentrated in lifetime/ownership transitions: naming a stage is
mistaken for creating it, normalized paths replace the paths being checked, and
verification results are separated from the bytes subsequently consumed. These
need narrow repairs rather than an architectural rewrite. Dense one-line control
flow in the coordinator and CLI makes these state transitions harder to audit;
expand the affected paths while repairing them.

New regressions are confined to `tests/test_output_review.py`. The full existing
test suite and the 44-case native qualification corpus were not repeated.

## Original confirmed findings (all repaired)

### 1. P1: failed stage creation can delete another owner's directory

- Location: `src/sqlitefolio/publish.py:36-37,74-75`
- Requirement: failure before rename removes **only the owned stage**
- Observation: `stage_name` is assigned before `mkdir`. If that directory
  already exists, `mkdir` raises; the `finally` block nevertheless recursively
  removes that name. The focused test precreates a directory with a sentinel,
  supplies a deterministic colliding token, and observes the sentinel deleted
- Regression: `test_stage_name_collision_never_deletes_an_unowned_directory`
- Narrow repair: track successful directory creation independently, set the
  ownership flag only after `mkdir` succeeds, and clean up only when ownership
  was acquired. A bounded fresh-name retry is also acceptable. Do not delete a
  path merely because its name was selected
- Risk qualification: an honest random 128-bit collision is extraordinarily
  unlikely; this is still an explicitly forbidden destructive failure branch,
  reproduced without arbitrary SQL or a hostile native-engine test

### 2. P1: inspect can emit bytes that were never verified

- Location: `src/sqlitefolio/cli.py:48-54`
- Requirement: `inspect PACKET` emits verified static HTML
- Observation: verification reads and checks `report.html`, then the CLI opens
  the path again using an ordinary file open. A benign replacement made between
  those two operations is emitted with the earlier successful verification
  result. The second open also loses the verifier's no-follow/regular-file
  checks
- Regression: `test_inspect_does_not_emit_report_replaced_after_verification`
- Narrow repair: expose a verified capture containing the exact bounded report
  bytes and use those bytes for inspection. Alternatively perform verification
  against the exact captured bytes that will be emitted. Another path-based
  verification followed by another path-based open only moves the race

### 3. P1: replay can trust a substituted, unverified input binding

- Location: `src/sqlitefolio/packet.py:69-73,79-89`
- Requirement: source-bound replay checks exact source/input/runtime binding
  against the structurally verified packet
- Observation: replay verifies a packet, then `read_packet` independently reads
  its mutable path. The test starts with a valid packet retaining `SELECT 1;`.
  After verification it changes only the retained-candidate digest in
  `packet.json` to the digest of another trusted original, `SELECT 2;`. Both
  scripts are read-only and leave identical observations. Replay of the second
  original returns `matched=True`, although the packet's retained SQL is still
  the first script and independent verification now rejects the packet
- Regression: `test_replay_does_not_use_binding_replaced_after_verification`
- Narrow repair: pass the exact parsed packet capture that completed
  verification into replay and bind the trusted originals against that capture.
  The same verified-capture API can repair finding 2. Keep the compact public
  verification result separate from the internal captured evidence object
- Scope: this does not cause replay to execute arbitrary packet SQL; execution
  still comes from the explicitly trusted manifest. It does produce a false
  source-binding match, which is the specific promise replay is meant to add

### 4. P2: lexical symlinks disappear before path rejection

- Location: `src/sqlitefolio/paths.py:18-25`; callers include input capture,
  output preflight, and publication
- Requirement: reject symlink components in input and output paths before writes
- Observation: `abspath` removes `alias/..` before `lstat` checks. A path with a
  real symlink component followed by `..` is accepted for the manifest or
  destination. The verifier already has a lexical traversal that handles this
  case correctly in `_safe_root`
- Regressions: `test_input_lexical_symlink_before_dotdot_is_rejected` and
  `test_output_lexical_symlink_before_dotdot_is_rejected_before_writes`
- Narrow repair: inspect lexical components in traversal order before reducing
  `..`; reuse the approach already present in the independent verifier without
  making the verifier depend on producer validation
- No source overwrite was demonstrated; this is a guardrail and path-meaning
  violation, not a claim of a hostile-input sandbox escape

### 5. P2: incomplete report labels claim complete underlying facts

- Location: `src/sqlitefolio/report.py:27`
- Requirement: incomplete snapshots must never be labeled complete
- Observation: with two original rows and `rows_per_query=1`, the real run
  correctly returns resource-limit/incomplete and exit 4. Both after snapshots
  are null, yet their links say “Underlying complete raw facts and diagnostics”
- Regression: `test_incomplete_report_does_not_call_its_raw_snapshots_complete`
- Narrow repair: use a neutral “Underlying raw facts and diagnostics” label, or
  derive a complete/incomplete/not-collected label from that phase's snapshot
- Numeric changes and overall status were correctly nonpassing in this test

### 6. P2: strict JSON reader accepts exponent-overflow infinities

- Location: `src/sqlitefolio/contract.py:35-40`
- Requirement: reject nonfinite JSON numbers at every level
- Observation: `parse_constant` rejects literal Infinity/NaN but does not run
  for `1e999` or `-1e999`; both become Python infinities and are returned
- Regression: `test_overflowed_json_number_is_rejected_as_nonfinite`
- Narrow repair: validate finite decoded values, such as a bounded canonical
  encode with `allow_nan=False` after parsing, as the independent verifier's
  loader already does. Preserve conversion to `InputError`
- Scope: a valid manifest slot that turns this into a passing rehearsal was not
  demonstrated. Existing field validators reject inappropriate numeric types;
  this finding concerns the strict reader's stated contract

## Initial focused evidence

Commands run from the project root:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_output_review.py -v
```

Result: **11 tests; 4 passed; 7 failing test methods, totaling 8 reported
failures** because both exponent-overflow subcases fail. Evidence:
`output-boundaries-review-red.log`. The same assertions passed after repair.

Passing new controls establish:

- Real partial successful writes are retried until all bytes are written
- The coordinator's accumulation refusal stops before the next candidate and
  publishes nothing (lowered test cap exercises the branch without large work)
- The HTML-capacity refusal publishes nothing (lowered test cap)
- A sparse `packet.json` of 32 MiB + 1 byte is rejected before `os.read`

Seventeen existing targeted controls passed; evidence:
`output-boundaries-focused-controls.log`. They cover actual concurrent
`RENAME_NOREPLACE` contenders, existing empty/nonempty destination preservation,
write-failure cleanup, truthful post-rename fsync failure, closed stdout retaining
a published packet with exit 7, explicit trust, normal SQL rehearsal/inspection/
verification/replay, transaction nonpass/source preservation, changed-SQL replay
refusal, explicit DB-byte inclusion, HTML escaping/no remote assets, rejection
of rehashed false summaries and boolean/integer substitutions, nonzero internal
error packets, evidence limits, and lexical packet-path symlink refusal.

## Reviewed strengths and remaining limits

- Structural reconstruction is separately implemented; it does not import the
  producer's contract, comparison, or invariant evaluator. It reconstructs
  typed multiset deltas and named checks and compares canonical summaries, so
  rehashing a false summary does not make it valid
- Verification checks exact packet filename sets, bounded retained files and
  typed cells, normalized/original input consistency, and deterministic report
  bytes. Complete baseline groups, runtime identity, and phase/profile facts
  are checked. Exact interpreter-build fields have been added under the
  pre-release protocol clarification and are included in replay equality
- Resource, incomplete, open-transaction, SQLite-error, source-changed, and
  internal-error observations cannot produce overall pass on the reviewed
  summary branches. No incomplete numeric loss claims were observed
- Source captures are bounded read-only regular-file reads with no-follow,
  hardlink rejection, content/stat checks, and post-execution rechecks. Output
  requires an existing parent. No source file is opened writable in this scope
- DB bytes require `--include-sample`; normalized packet identities omit the
  original manifest's absolute host location. Exact original SQL and manifest
  bytes are intentionally retained, and row evidence still contains sample data
- The rendered data path uses HTML escaping and includes a no-remote-resource
  CSP. This protection does not repair the separate inspect reread defect
- Full, consistently fabricated raw facts can still pass structural
  verification. The implementation correctly describes hashes as consistency,
  not authenticity or independent SQLite truth; replay uses the same engine

This is an implementation review with deterministic benign filesystem and
verification-boundary reproducers. It is not a security audit, native parser or
serialization investigation, crash-durability proof, large-scale performance
claim, or universal migration-correctness verdict. Ancestor-directory swaps by
a hostile concurrent process were not exhaustively analyzed. No public writes,
downloaded SQL, live/WAL sources, or external services were used.

## Initial reviewed source identities

Line numbers above refer to these reviewed source revisions. Other concurrent
runtime/semantic repairs may move line numbers; rerun regressions after repair.

```text
5d58fc72be18d483df0c0f92cee2d4aed6d3edafb8c54b3c893206f7d8fa3c16 contract.py
5df50860f16ce92e3458a6f45b7a4779282f620e7bfa09028d0fd5b382024e6c paths.py
2788f7e72d869d2c8d0610e9c1da768dd75554f72608e54ae70de256394245f0 publish.py
14c6e3ced11895a71af350281b90883def25f7f2c433e3b1052ed3b9a9dd7821 packet.py
771c227b7171653260ed85a49e227d4779496b9b24a22b5a08a6e8d6e162c951 report.py
3ca9467be2b65144f18de24a5240c0134a8832aace1eff7fedd1071f2b958283 verify.py
9f90d24cea2d5fcf49b3d139797d609a9b1923be5f37f4bdd66b9747805dc48e cli.py
```

## Independently re-reviewed repaired source identities

```text
3df220c17ca592cd3a256f75aecf4bc2ec822292afc53bc82d9025865e496b6b contract.py
74db8f3626bc9dc5a13fdff6e9794dc1ef8ed5001bb69453db798c00bf0f0fab paths.py
43a427221a9300af99f991742f083733d00aff110e8991a40a235dd9be36deae publish.py
64bb8d31b36932c8d459d74a7949d4cee8de7e9dbe51d9fcac09ad5908407800 packet.py
5ca492d131a5ea388dab6e30788aaa08a0595d4f29cadfb809da2b0dc7b619a8 report.py
2b325824e0ba1f48ed8a9151239f605b2fcf87fe5e8a465f39a53cbeeb0d67ed verify.py
825c3285cf6329b4d76fd08c17b538203f0ee096f12dcc71dd86874dec82636c cli.py
```
