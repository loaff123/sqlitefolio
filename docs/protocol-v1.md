# SQLiteFolio protocol 1

SQLiteFolio is a provisional name for a folio of SQLite migration review evidence.
This is the versioned input and evidence contract. Keyed comparison is outside
v1: all row deltas are explicitly common-column multiset projections. There is
no inferred row identity, migration score, production connection or apply command.

## Runtime and trusted scope

Only Linux x86_64, CPython 3.12, SQLite 3.53.1 is initially qualified. Exact patch,
SQLite source ID and compile options are recorded; a different SQLite or Python
minor is refused. `sqlitefolio profile` reports the actual runtime. Python's
version alone does not select SQLite. Qualification records explain availability.
Inputs must be explicitly trusted via `rehearse ... --trust-input` and `replay ...
--trust-input`. This is accidental-misuse containment, not a hostile-input sandbox.
Quiescent regular DELETE-journal databases and original UTF-8 schema/seed and
migration SQL only. Refuse sidecars, symlinks in any path, nonregular files,
hardlinked inputs (nlink != 1), virtual/shadow tables, temp objects, extensions,
ATTACH/DETACH, VACUUM (including ordinary VACUUM), external-file SQL, custom UDFs/collations, unknown PRAGMAs and
nonqualified runtime. No native serialization/deserialization, SQL parser or
SQL rewriting. Application SQL executes through one executescript call as written,
with isolation_level=None and no synthetic BEGIN/COMMIT/ROLLBACK. Source schema and
seed are separate scripts, each must end with no transaction and no SQL error.

## Resource ceilings

All sizes are bytes. Defaults equal ceilings except stated otherwise. Manifest:
1 MiB; source DB: 16 MiB; each SQL file: 1 MiB; all input files together: 32 MiB;
8 candidates; 64 invariants; names 128 UTF-8 bytes; 128 observed tables;
512 schema objects; 256 columns/table or query; 10,000 rows per table/query;
100,000 total observed rows/candidate across phases; value 65,536 bytes;
16 MiB canonical raw evidence/candidate; 20 MiB worker response;
32 MiB packet JSON; 32 MiB disposable DB; 30 seconds worker wall deadline;
2,000,000 VM instructions per script/snapshot/query phase, counted every 1000;
512 MiB worker RLIMIT_AS; 32 MiB RLIMIT_FSIZE; 35 seconds RLIMIT_CPU;
64 open FDs; no core dumps. Parent request <= 32 MiB. Eight candidates run
sequentially, never unbounded subprocess concurrency. Worker output uses a bounded
file; stdout/stderr go to DEVNULL. Parent kills and reaps the worker process group
on deadline and cleans its private workspace. Unexpected worker death/protocol
errors are `worker_error`, never assumed resource exhaustion; only observed
budget/limit exceptions or enforced deadlines become `resource_limit`.

Manifest `limits` optionally lowers only rows_per_query (1..10000),
vm_steps (1000..2000000 in multiples of 1000), wall_seconds (1..30),
evidence_bytes (4096..16777216). Other caps are fixed. Evidence accounting
includes encoded rows before accumulating; final encoded-size checks also apply.
No incomplete snapshot may be labeled complete or generate numeric loss claims.

## Input JSON

UTF-8, duplicate keys/nonfinite numbers/unknown keys rejected at every level.
No booleans where integer counts are expected. Top-level exact keys:
`format`, `scenario`, `source`, `profile`, `candidates`, `invariants`, optional
`limits`. format = `sqlitefolio.input.v1`. scenario: [A-Za-z0-9_-]{1,64}.
Profile exact keys: `foreign_keys` (boolean), `transaction_mode` (`autocommit`).
Source: either {`database`: relative filename} or {`schema`: relative filename,
`seed`: relative filename}. Paths are nonempty, relative, no `..`, empty segments,
backslash, NUL, URL, interpolation or symlink. Candidate exact keys `name`, `sql`;
name has scenario grammar and is unique. SQL file exact bytes are retained.

Invariant names are unique; common exact keys `name`, `kind` plus variant:
- query_equals: `sql` (query text <= 65536 UTF-8 bytes), `order` (`ordered`|`bag`),
  `expected` (list of equal-width typed rows); optional `boolean` (boolean false
  default). boolean=true requires expected and observed exactly one integer 0/1
  cell; malformed observed values are error, never Python truthiness
- query_preserved: `sql`, `order`; no expected values
- row_count: `table`, `expected` (nonnegative integer or string `baseline`)
- object_present/object_absent: `object_type` (table/index/trigger/view), `object`
- no_foreign_key_violations/integrity_ok: no additional keys

A value is exactly a two-item JSON array: ["null",null], ["integer",canonical
signed decimal i64 string], ["real",16 lowercase hex IEEE754 big-endian bytes],
["text",canonical base64 of exact SQLite UTF-8 bytes], ["blob",canonical base64].
SQLite TEXT decoding uses bytes at the driver boundary and the observed storage
class; invalid UTF-8 TEXT cell bytes are lossless, while non-UTF8 object names or
metadata are unsupported/incomplete. NaN typed expectations are refused: SQLite
normalizes NaN to NULL. Negative zero and infinity are represented as observed.
Row multiplicities are retained; bag operations sort canonical encoded rows.

## Worker protocol and raw observation

Coordinator request exact keys: format (`sqlitefolio.worker.v1`), source (database
owned path OR schema_sql and seed_sql strings), candidate {name,sql}, profile,
invariants, limits. Worker paths are coordinator-created owned paths only. Worker
response is one candidate raw observation:
`name`, `runtime`, `profile`, `execution`, `before`, `observed`, `reopened`,
`queries`, `source_preserved`.
Runtime: python, sqlite, sqlite_source_id, compile_options, platform,
python_build, implementation, machine (strings/list).
Profile echoes declared settings. source_preserved is boolean; false cannot pass.
Execution exact keys: status, error, in_transaction, foreign_keys_start,
foreign_keys_end. Status: completed, sqlite_error, open_transaction,
resource_limit, unsupported, worker_error, source_changed. Error is null or
{kind,message,sqlite_code,sqlite_name}; code/name nullable. in_transaction bool
or null when unavailable; FK modes bool or null. Controlled connection close,
then reopen, never claims crash durability. SQL error has priority over an open
transaction but records both. No invented failing statement location.

Snapshot exact keys: `complete`, `error`, `objects`, `tables`, `views`,
`diagnostics`, `sequences`. Absent snapshot = null, not empty success. Incomplete
snapshot has complete=false, nonnull error, any facts collected so far; unknown
facts aren't synthesized. objects list sorted type/name: {type,name,table,sql},
including internal sqlite_sequence and autoindexes where present. tables keyed
by name: {columns,column_info,table_info,foreign_keys,indexes,rows}.
columns include generated stored/virtual columns in table_xinfo cid order.
column_info = all native table_xinfo rows; table_info = matching table_list row
(schema,name,type,ncol,wr,strict); foreign_keys = foreign_key_list rows;
indexes = [{info:index_list row,columns:index_xinfo rows}], deterministic name
order. rows are sorted complete typed rows with duplicates retained. Row count
is reconstructed as len(rows), never trusted as a separate raw summary.
views keyed by name: {ok:boolean,error:null|error}. Preparation uses SELECT *
FROM quoted-view LIMIT 0 under read-only guardrails; resolution error is an
observed complete diagnostic, not a hidden success. diagnostics has
foreign_key_check and integrity_check, each {rows:typed rows,error:null|error},
and foreign_keys:boolean. sequences is typed SELECT name,seq FROM sqlite_sequence
or [] when absent, also present as ordinary table facts. Metadata keeps SQLite's
native string/int/null types; no derived meaning is ascribed to CHECK SQL.

queries is keyed by invariant name for query_equals and query_preserved only,
each {before,observed,reopened}. Each query fact is {rows:typed rows|null,error:
null|error,complete:boolean}; a failed query is complete=false. Every phase is
recorded even if not run ({rows:null,error:{kind:"not_run",...},complete:false}).
Read-only authorizer applies before preparing invariants. No SQL DML, temp writes,
PRAGMA setters or side effects are permitted. Explicit ordinary read PRAGMAs and
pragma_* metadata queries are supported. Progress budgets reset by explicit phase.

## Packet and reconstructed summaries

Directory files: packet.json, report.html, manifest.json, inputs/<normalized SQL
filenames>, and optional sample.db only with --include-sample. SQL-only packets
retain schema.sql, seed.sql, candidate-N.sql exact original bytes. DB-source
packets retain database content digest but omit database bytes by default.
Manifest is normalized to these internal filenames; original source file content
identities are recorded without disclosing host paths. Original manifest bytes
are inputs/original-manifest.json. Packet JSON exact keys: format
(sqlitefolio.packet.v1), input, input_sha256, files (relative filename -> SHA256),
source (kind,sha256,snapshot_sha256,included), candidates (raw observations),
summary (derived comparison), limitations (fixed versioned list).
Input is normalized manifest. input_sha256 hashes canonical input JSON. Source
snapshot_sha256 hashes canonical first before snapshot. Every candidate must
have same before snapshot and profile; otherwise comparison group is inconsistent
and verification fails. source.sha256 hashes original DB bytes or canonical
{schema_sha256,seed_sha256}. files covers retained exact input files (not report
or packet, to avoid circular hashes). Filename set is exact and bounded.

Summary is reconstructed from raw facts and invariant contract. It has per
candidate {name,status,execution,complete,observed_checks,reopened_checks,
observed_changes,reopened_changes}, where status is pass, failed, execution_error,
incomplete, unsupported, or internal_error. Checks = list {name,status}, status
pass/fail/error/not_run/incomplete. Changes: {complete,objects_added,
objects_removed,objects_changed,tables}; incomplete change uses complete=false,
object arrays null and tables={} (no guessed loss). Complete table changes keyed
by union of names: {kind,common_columns,columns_added,columns_removed,
rows_before,rows_after,projected_added,projected_removed,metadata_changed}.
Kind added/removed/projection; projected counts null for added/removed tables or
no common columns. metadata_changed compares column_info/table_info/FKs/indexes.
Object identities are type:name; changes compare full raw object declarations.
All numerical outputs derive from complete typed raw facts.

Invariant evaluations reconstructed independently for observed and reopened.
Query error/not_run propagates; incomplete snapshot makes nonquery checks
incomplete and blocks overall pass. Query checks can individually pass when a
snapshot is incomplete but overall remains incomplete. View errors are visibly
shown and block pass as failed diagnostics even without a named view check.
Native FK/integrity bad rows block overall pass; semantic absence of violations
is exact empty FK rows and exactly [[text("ok")]] integrity result. An erroring
native diagnostic makes observation incomplete. Completed execution + complete
snapshots + all named checks and diagnostics passing in both after phases only
is pass. No named checks is allowed but never a universal correctness verdict.

Independent verifier validates shapes and every raw typed cell, reconstructs
all summary fields and invariant outcomes with separate code, compares exact
summary and deterministic report, and validates file identities. A rehashed false
summary must fail. A complete consistently replaced raw packet can pass structural
verification: hashes do not authenticate authorship or native truth. Replay takes
an explicit trusted original manifest, checks exact input/source/runtime binding,
reruns disposable execution and compares complete raw facts; it is distinct from
structural verification and also uses the same SQLite engine. Incomplete packet
verification is valid-but-nonpassing; never exit zero.

## CLI, publication, and exits

`profile`; `rehearse MANIFEST --out DIR --trust-input [--include-sample]`;
`inspect PACKET` emits verified static HTML to stdout;
`verify PACKET` emits a compact verification result;
`replay PACKET --manifest MANIFEST --trust-input` compares to source-bound rerun.
One scenario/profile per manifest. No cross-profile ranking API.
Exit 0 passing completed sample checks / structurally consistent passing packet
(with explicit structural-only label); 1 complete failed checks/diagnostics;
2 invalid/unsupported input; 3 SQLite execution error or open transaction;
4 resource/incomplete/source-changed; 5 unexpected worker/protocol/internal error;
6 tamper or replay mismatch; 7 output publication/I/O failure. Priority 5>4>3>2>1>0.
Argument parse failures use 2. Closed stdout after successful publication returns
7, with the existing packet preserved; no claim publication was rolled back.

Output parent must pre-exist; no directory ancestors are made implicitly. Reject
output equal to, aliasing, or containing any input, and reject all symlink path
components before writes. Read inputs with O_NOFOLLOW, regular-file/nlink/size
checks; hash and stat before/after reads and again after execution. Source files
are never opened writable. Snapshot capture uses byte-preserving bounded reads,
then source backup through read-only immutable SQLite from the owned capture;
this avoids SQLite creating sidecars beside the user's source. User attests
quiescence; hashes cannot prove absence of concurrent transient changes.

Stage in an owned random sibling directory. Write+fsync all files then use Linux
renameat2(RENAME_NOREPLACE) to publish one new directory. No check-then-replace
fallback: refuse if the primitive/filesystem is unavailable. Parent directory is
fsynced after rename. Failure before rename removes only owned stage. Failure
after rename reports publication-I/O error and preserves published evidence.
No success marker is printed before publication. Existing destinations, including
empty directories and races, are never overwritten. No untrusted shell command,
configurable Python callback, custom worker command, or dynamic import hook.

## Canonical encoding and observation phases

Canonical JSON means Python json.dumps(value, sort_keys=True,
separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('utf-8').
All internal digests and bag ordering use those bytes. Metadata text retains
native decoded string values. Reopened observer starts with the manifest's
initial foreign_keys profile, explicitly reapplied: connection FK mode is not
durable database state. Native sqlite exceptions have error.kind=sqlite_error;
error.message is str(exception), with native sqlite_errorcode/sqlite_errorname.
Worker limits has exactly the four normalized keys rows_per_query, vm_steps,
wall_seconds, evidence_bytes. SQL PRAGMA writes permit only foreign_keys and
defer_foreign_keys; the read-only check phase permits neither setter. Both
before and reopened observers record their actually configured FK value.
The evidence_bytes cap counts raw collected facts (including encoded metadata,
rows and query observations), not the mandatory minimal status/error envelope;
if collection exceeds the cap, retain resource_limit (or an already higher-priority worker_error/source_changed)
status and not_run query
slots even when this minimal envelope exceeds a user-lowered cap. Fixed 20 MiB
response and 160 MiB packet ceilings still apply. No collected facts beyond the
cap are retained. Error envelope fields are never silently omitted.

## Summary conventions

Summary.execution is the execution status string. Object identity arrays sort
lexically. Common columns follow baseline column order; added columns follow
after order; removed columns follow baseline order. Added/removed tables use
common_columns=[], absent-side row count=0, projected counts=null,
metadata_changed=true. Sequences and table rows sort canonical typed rows;
foreign_key_check/integrity_check rows retain native order. Runtime.python is
platform.python_version(); runtime.platform is sys.platform (`linux`), compile
options sorted strings, sqlite_source_id exact SELECT sqlite_source_id().

Summary.complete means all three snapshots are nonnull and complete, and source
preserved. Query SQLite errors or invalid boolean results are explicit error
checks and overall failed. Resource-limited, not_run, or incomplete required
queries yield overall incomplete. Query_equals does not require its baseline
query to succeed (e.g. a newly added column); query_preserved requires both.
Priority: worker_error -> internal_error; incomplete snapshots/source_changed/
resource_limit/required query incomplete -> incomplete; sqlite_error or open
transaction -> execution_error; unsupported -> unsupported; failed/error checks
or bad view/FK/integrity diagnostics -> failed; otherwise pass.

Normalized DB input uses source={"database":"sample.db"} whether included or
omitted. limitations exact array (these strings are part of packet version 1):
1. Trusted sample input only; guardrails are not a hostile-input security sandbox.
2. Sample checks do not prove production safety or correctness for other data.
3. Reopened state follows controlled close; no crash durability or rollback guarantee.
4. Row deltas are common-column multiset projections, not inferred row identity.
5. Structural verification checks consistency, not authorship or SQLite engine correctness.
6. Source-bound replay is separate and uses the same qualified SQLite engine.
7. Existing migration tools and competent SQLite recipes can obtain these same facts.

## Query order and connection lifecycle

Raw query facts always retain native result order. `order=bag` changes only the
invariant comparison; it does not rewrite raw query facts. No-error
open_transaction has error=null and in_transaction=true. Source construction and
candidate execution use distinct connections: candidate configuration applies
the declared FK profile afresh after backup and before baseline observation.
Source SQL changes to connection-local PRAGMAs do not silently alter the candidate
profile. Migration SQL may change allowed FK settings and those are recorded.

## Coordinator bounds

The packet JSON ceiling is 32 MiB. Rendered HTML is capped at 64 MiB; total retained
input files remain at 32 MiB. The coordinator serializes after each sequential
candidate and refuses further accumulation above its packet cap, exit 4 with no
published directory. This is an explicit refusal, not a partial passing packet.
No implicit parent-process RLIMIT is imposed on a caller using the library API.
Source SQL sample DB inclusion is unsupported in v1: --include-sample requires
a database source. Such inclusion stores the exact captured original DB bytes.

## Incomplete comparison groups

All complete baseline snapshots within a packet must agree exactly. A worker
that ends before baseline completion may have null or a partial baseline; it
remains visibly nonpassing and produces no numeric comparison. Its partial
baseline is not claimed equal to a complete baseline. Shared declared source
binding uses source hash plus input contract. source.snapshot_sha256 hashes the
first candidate's before value, even null/incomplete, without upgrading it to
complete evidence. Incomplete snapshots retain exact snapshot keys; diagnostics
may be {} before collection starts or the full diagnostics object with explicit
not_run errors. Partial object/table correspondence is not complete coverage.

## Exact runtime and error identity

Runtime records exact python_build=sys.version,
implementation=platform.python_implementation(), and machine=platform.machine().
These are mandatory runtime keys, and all are part of source-bound replay identity.
Same Python patch and SQLite source ID do not silently equate differently built
interpreters. Both the original qualification interpreter and separately installed
public distribution are documented with their exact build details.
Error execution statuses require matching error.kind; completed and no-error
open_transaction require error=null. row_count.expected is any nonnegative JSON
integer (booleans refused), independent of the observation row ceiling; a larger
expectation is a valid failing check when complete observations have fewer rows.

## Declared scope refinement

All VACUUM statements are unsupported because SQLite uses an internal ATTACH for
ordinary VACUUM; the authorizer does not distinguish that from the excluded
external-database path. The 256-column ceiling applies to tables, invariant
queries and expected rows. These are explicit conservative refusals, not claims
of general SQL compatibility.

## Verified read binding

Successful structural verification returns packet_sha256 and report_sha256 for
the exact file bytes it validated. Any subsequent inspect/replay read must be
bounded, regular and symlink-free, and match those returned identities before
being used. A changed file is a structural mismatch, never newly trusted output
or replay input. Staging cleanup applies only after successful exclusive mkdir;
a random name collision must leave the existing unowned directory untouched.

A file source must have an initialized SQLite header of at least 100 bytes; a
zero-byte native empty database is refused. Use schema/seed source mode for an
empty baseline.

The minimal-error-envelope exception requires null before/observed/reopened
snapshots and only explicit not_run query facts. Any retained collected facts
remain subject to evidence_bytes; the fixed 20 MiB response ceiling always applies.
