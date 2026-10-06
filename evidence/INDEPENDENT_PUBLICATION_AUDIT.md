# SQLiteFolio publication audit

## Result

PASS for the reviewed publication content and packaging integrity. No unresolved
publication-content blocker was found. This is not an exact-head remote CI result
or an approval of production use.

Reviewed candidate: SQLiteFolio 0.1.0a1, source commit
`67674345d54267df606286683c7ba2d4b0c8a7bb`.
Candidate bundle SHA-256:
`b7fe13d3716cb80294923256c58f3367eb1a3622b7886476a88c1814d1f44575`.

## Verified directly

- The supplied bundle digest matches. Its 262 members equal the candidate
  directory. Recreating the clean bundle reproduces its exact digest.
- Every one of the 261 manifest-covered files has its stated size and SHA-256;
  there are no missing or unlisted payload files.
- All 69 source files equal the recorded commit. All 15 wheel modules equal
  source, and the wheel RECORD checksums verify. The sdist retains every source
  payload except `.gitignore`, with only expected packaging metadata added.
- ZIP members have no absolute/traversal names or symlinks; the sdist has no
  non-file/non-directory entries.
- An independent source-suite rerun passed all 148 tests on CPython 3.12.14
  (Aug 25 build), SQLite 3.53.1. See `independent-source-tests.log`.
- All 131 LOG_OUTCOMES entries agree with terminal log results: 48 passing,
  41 failed, and 42 without unittest results. Historical failures remain visible.
- Recorded README journeys contain 30 matching module commands and 20 matching
  console commands. Both exact-runtime parity records contain 44 executions and
  zero mismatches. Frozen input and additional-case checksums verify.
- Text scans and spot review found no private workspace/home paths, confidential
  workflow references, private project numbering, or recognizable credentials.
  Observed email examples use the reserved `example.test` domain.

## Setup findings and resolution

The historical candidate instructions had three copy/paste setup gaps: a wheel
path inconsistent with an extracted-source layout, bare console commands without
virtualenv activation, and a direct build command without installing setuptools.
A fresh Python 3.12 virtualenv confirmed setuptools was absent.

The staged public README and runtime recipe resolve these findings by installing
`setuptools==84.0.0`, building the wheel in the source root, installing it, and
explicitly activating the environment before the README journey. Package code
remains byte-identical to the reviewed candidate.

All nine COMPRESSED_EVIDENCE mappings verify compressed and original sizes and
SHA-256 values. Decompression yields the original candidate JSON bytes exactly.
Every other historical evidence file is unchanged. The evidence README explains
the historical layout, intermediate test counts, failed attempts, compression,
offline previews, and the exact-runtime distinction for sample packets.

## Remaining limits

The review is a bounded publication audit, not an exhaustive security review.
Pattern scans cannot prove absence of every possible secret. This audit did not
redownload the public runtime, rerun both installed distribution suites, execute
remote CI, or perform browser visual/interactivity QA. Historical evidence for
those executed local checks was inspected without relabeling it as a fresh run.
Exact-head CI must be verified separately after staging/publication. Browser QA
remains the disclosed coverage gap. Documentation appropriately limits claims to
trusted synthetic samples and distinguishes structural verification from
source-bound replay, without claiming universal correctness or production safety.
