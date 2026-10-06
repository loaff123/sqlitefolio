# SQLiteFolio 0.1.0a1 review candidate

Status: locally qualified alpha candidate. No public release or remote CI result
is claimed. Repository publication and exact-head CI remain separate gates.

## Result

The integrated local workflow is implemented: strict trusted-sample input,
source preservation, original SQL, explicit FK/autocommit profile, typed
invariants, bounded disposable workers, in-session versus reopened observations,
structural/data comparison, static HTML, independent structural verification and
source-bound replay. No production application command exists.

Source commit: `67674345d54267df606286683c7ba2d4b0c8a7bb`.
The clean tracked source is in `source/`; installable artifacts are in `dist/`.
`FILE_MANIFEST.json` records each delivered file's size and SHA-256.

## Verified qualification

- 148 tests passed from source, an installed wheel, an installed sdist and an
  extracted sdist, on the original Aug25 and public Sep24 CPython3.12.14 builds
- The wheel and sdist installed into fresh virtual environments; import proofs
  confirm tests loaded the installed package rather than the source checkout
- 30 module-based README journey commands plus 20 installed console-script
  commands matched their documented exit codes; inspected HTML bytes equaled
  the checked report. The intentional faulty comparison correctly returns 1
- All 44 independent direct-SQLite schema/file executions matched raw facts,
  production summaries and independently reconstructed verifier summaries on
  both builds. This remains 24 original executions plus 20 additional executions
- Original 12 cases, 30 checks, 24 executions and 13 prototype tests remain the
  historical feasibility record. All 19 original fixture files are byte-identical;
  these counts were not relabeled as new independent cases
- Ten additional benign cases retain 29 handwritten checks and frozen predictions
- Two independently authored code reviews accepted their final repaired scopes
- Wheel and normalized sdist were rebuilt repeatedly with identical bytes/hashes

## Reviews found real defects

The review evidence preserves red findings and green repairs, including ordinary
ALTER support, source FK-profile separation, error/open-transaction precedence,
optional baseline query limits, internal error classification, minimal failure
framing, staging ownership on name collision, lexical symlink paths, numeric JSON
overflow and verify-then-reread substitutions. Static verification recomputes every
summary and invariant from raw facts; consistently replaced fabricated evidence
can still verify and is rejected only when source-bound replay disagrees.

See `evidence/ENGINE_CODE_REVIEW.md`, `evidence/OUTPUT_BOUNDARY_REVIEW.md`, and
`evidence/LOG_OUTCOMES.json`. The last file records actual terminal outcomes,
including historical failed attempts whose original filenames contained “green”.
Such filenames are not treated as passing evidence.

## Qualified bounds

The public runtime archive was downloaded and its release-asset digest checked:
34,270,188 bytes, SHA-256
`269b2c99e4db15b242bf01832f4fea1e8f1a664f273cff519393f296e9820b41`.
The archive is not redistributed. The exact recipe and recorded profile are in
`source/docs/profile.md` and the runtime evidence.

Seven benign calibrations exercised row/value boundaries, evidence exhaustion
and a real 1-second worker deadline. A separate real coordinator accumulation
refusal preserved all source bytes and published no packet. Observed coordinator
peak RSS was 191,568 KiB in that check; resource measurements are engineering
qualification, not performance comparisons or universal memory guarantees.
The coordinator packet ceiling was conservatively set to 32 MiB before its
implementation; worker limits and all other caps are explicit in protocol 1.

## HTML review boundary

Static HTML escaping, absence of scripts/remote assets, deterministic report
identity and incomplete-state wording are tested. An offline PyMuPDF rendering
was inspected for readable text/tables; its details/summary placement differs
from a browser and does not qualify browser behavior. Native Chromium could not
start in this execution sandbox, and the cloud browser could not open a local
file URL. No security setting was changed and no denied route was worked around.
Browser visual/interactivity QA is therefore an explicit remaining coverage limit.
Offline preview files are labeled accordingly.

## Scope and honest narrowings

Trusted quiescent initialized DELETE-journal samples only. SQLiteFolio registers
no extensions, application UDFs or custom collations. It does not certify intent
or unexercised trigger behavior. Virtual/temp objects, live/WAL sources, external
files/network, ATTACH/DETACH and all VACUUM are outside support. Zero-byte DB files
are refused; explicit schema/seed can represent an empty baseline. Query/table
width is capped at 256; keyed row correspondence is omitted, preserving exact
multiset projection comparisons. Sample DB inclusion requires an explicit flag.

No claims of production safety, crash durability, physical rollback, correctness
for all data, engine correctness, detection superiority, speed, adoption or
incident reduction. Atlas, Alembic, sqlite-utils and capable SQLite recipes are
credited. SHA-256 hashes do not authenticate a submitter.
