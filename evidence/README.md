# Qualification evidence

These are engineering observations on original trusted synthetic inputs. They do
not certify production migrations, SQLite itself, hostile inputs, concurrency,
crash durability, performance superiority, adoption, or incident reduction.

## Historical candidate

`qualification/` preserves the pre-publication candidate's logs, two independent
code-review reports, calibration results, import proofs, and README journeys.
The source candidate was commit `67674345d54267df606286683c7ba2d4b0c8a7bb`.
Its review is `qualification/CANDIDATE_REVIEW.md`; paths beginning `source/`
there refer to the candidate bundle layout. Public repository source is at root.
These files describe their historical stage, not the current remote CI outcome.
The word "publication" in those historical test names means atomic local packet
publication, not GitHub publication.

`qualification/LOG_OUTCOMES.json` records terminal results. Failed attempts remain
failed even when their old filenames contain "green". Earlier counts of 118 or
125 are intermediate suite sizes; final candidate source and installed/extracted
distribution qualification reached 148 tests on each of two exact Python builds.
The repeated 44-execution semantic corpus is the same 22 cases on each build,
not 88 independent cases. It includes 12 original cases with 30 checks and ten
additional cases with 29 checks, each exercised from schema/seed and file input.

Large raw JSON evidence is stored losslessly as `.json.gz` to keep the repository
compact. `qualification/COMPRESSED_EVIDENCE.json` maps the historical names to
their stored names and binds both compressed bytes and decompressed original
bytes with SHA-256. References in historical reports retain the original names.
For example, `gzip -dc qualification/independent-parity-public-runtime.json.gz`
prints the original JSON. Do not overwrite the historical evidence with a rerun.

`qualification/offline-render-*.png` are explicitly offline PyMuPDF previews,
not browser screenshots. Native Chromium could not start in the qualification
sandbox and the cloud browser rejected local file URLs. Static HTML escaping,
absence of scripts/remote assets, deterministic bytes and incomplete-state text
are tested, but browser visual/interactivity QA remains a coverage limit.

## Reproduce and inspect

From the repository root, follow `docs/profile.md` to obtain and checksum the
exact publicly qualified runtime, then follow the README install instructions.
Source and installed-package tests can be run through `qualification/ci_qualify.py`.
The Actions workflow runs this driver and retains fresh evidence and distributions
on both successful and failed runs. Inspect the run's exact commit before using
its result. It performs no package-registry or GitHub Release publication.

`sample-packets/` at repository root contains original synthetic corrected and
faulty/comparison examples. Their profiles record the original interpreter build;
structural verification does not execute SQL, while replay needs their exact
recorded runtime and the original explicitly trusted inputs. Generate fresh
packets for the public Sep24 runtime rather than relabeling an Aug25 packet.

The original narrow source workflow is preserved as
`qualification/original-workflow.yml.txt`; the current root workflow is
`.github/workflows/ci.yml` and performs the broader distribution qualification.

`ci-review/report.md` independently reviews that broader workflow. Its final
local qualification ran 148 test definitions, 44 case/mode pairs, seven boundary
calibrations, and corrected/faulty terminal journeys through each of four package
paths: source, installed wheel, extracted sdist, and installed sdist. Repeating
the same cases across these paths does not multiply the independent case count.
The final build hashes differ from the historical candidate because installation
documentation and packaged CI were improved; production modules are unchanged.
