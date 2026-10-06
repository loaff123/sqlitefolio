# SQLiteFolio CI independent review

## Decision

Approved for the stated trusted-sample, one-runtime alpha scope. No blocking correctness, isolation, count-integrity, or failure-masking defect was found in the two reviewed CI additions. This approves the CI implementation; it is not a claim that GitHub-hosted CI has already run.

Reviewed files: `.github/workflows/ci.yml` and `qualification/ci_qualify.py`. Exact reviewed SHA-256 values are in `reviewed-file-hashes.json`. No repository files were changed.

## Evidence

- Independently resolved both full action SHAs to upstream v7.0.1 tags and inspected each pinned action manifest. Both use Node 24. Upstream checkout documents Actions Runner 2.327.1 or newer for Node 24. The workflow selects GitHub-hosted Ubuntu 24.04, rather than an uncontrolled self-hosted runner.
- Workflow permissions are limited to `contents: read`; checkout disables persisted credentials. The only triggers are push, pull_request, and workflow_dispatch. No privileged pull_request_target trigger, secrets, write token, or failure-suppression directive is present.
- The archive URL, 34,270,188-byte size, and SHA-256 match the documented public runtime. Independently hashed the already-downloaded archive. The real acquisition shell block rejected an injected corrupt download before extraction and preserved the SHA failure through tee. Both shell pipelines enable pipefail.
- The driver checks the exact CPython build string, SQLite 3.53.1 source ID, CPython implementation, Linux, and x86_64. Compile options are recorded. The archive digest provides the stronger CI build identity. A real incompatible-interpreter invocation returned failure and retained runtime, traceback, and false summary.
- Audited two terminal local runs (`ci-final` and `ci-single-workflow-final`): each has 63 distinct command records, every observed exit matches its expectation, and the final summary is true. The latest run took about 82 seconds of recorded command time.
- Each run genuinely exercises four separate package paths: copied source, installed wheel in its own venv, extracted sdist source, and installed sdist in another venv. Installed test trees contain no production source directory. PYTHONPATH/PYTHONHOME are cleared before installed tests, user-site imports are disabled, and origin logs resolve inside the correct venv. Sdist-installed tests come from the extracted sdist.
- All four paths ran the same 148 unique test methods with plain OK, without skips or expected-failure allowances. These are 148 tests repeated across four distribution paths, not 592 independent test definitions.
- Each path's explicit parity report contains 44 unique case/mode pairs: 24 frozen and 20 additional. Every raw, summary, verifier, prediction, and preservation comparison is clean. Repetition across package paths does not enlarge the corpus.
- Each path independently ran all seven cap calibrations with expected statuses. The worker-wall case actually reported the parent-enforced wall deadline in these runs.
- README corrected journeys explicitly require exit 0; intentionally nonpassing comparison journeys require exit 1. Unexpected nonzero exits are not accepted. Inspect output must exactly equal the generated report bytes. Installed paths exercise the console entry point.
- Two fresh build trees produce matching wheel and sdist hashes, and independently computed hashes of retained distribution bytes match those recorded hashes. Build tooling is pinned to setuptools 84.0.0 and its imported version is checked.
- Executed 14 focused independent controls covering accepted exit 1, rejected exit 7, timeout propagation/log retention, low test count, skips, short/mismatched parity, wrong frozen count, short/mismatched calibration, corrupt archive rejection, wrong runtime rejection, and the archive digest. All passed. Synthetic report fixtures in these controls validate driver gates; they are not additional product qualification.
- ELF symbol inspection of the downloaded runtime and its shared libraries found a maximum GLIBC version requirement of 2.17 and the conventional Linux x86_64 loader. This gives no obvious binary incompatibility with Ubuntu 24.04. Actual hosted-run behavior still needs the normal first Actions run.

## Failure handling and small improvement

Command stdout/stderr are opened before execution; command outcomes are persisted in a finally block; exceptions produce a false summary and traceback; the workflow always attempts artifact upload, even after a preceding step fails. No suppressed failure was found.

Nonblocking diagnostic improvement: when repeat-build hashes differ, the driver writes both hash maps and fails before copying either build to the artifact output. Logs and hashes survive, but the mismatching archives themselves remain only in the temporary build trees. Preserving both sets under the output directory would make a future determinism failure easier to diagnose. This does not convert a failure into success.

## Limits and sources

The local runs used Debian/glibc 2.41, not a GitHub Ubuntu runner. The hosted execution result is unobserved in this review. Dependency acquisition still depends on network access to the pinned runtime and setuptools; the project itself has no runtime network dependency. Qualification does not establish production safety, hostile-SQL safety, independent engine correctness, broader platform support, or browser layout quality.

Upstream sources inspected:
- [Checkout tag](https://api.github.com/repos/actions/checkout/git/refs/tags/v7.0.1)
- [Upload artifact tag](https://api.github.com/repos/actions/upload-artifact/git/refs/tags/v7.0.1)
- [Pinned checkout manifest](https://github.com/actions/checkout/blob/3d3c42e5aac5ba805825da76410c181273ba90b1/action.yml)
- [Pinned upload-artifact manifest](https://github.com/actions/upload-artifact/blob/043fb46d1a93c77aae656e7c1c64a875d1fc6a0a/action.yml)
- [Checkout runner requirement](https://github.com/actions/checkout/blob/3d3c42e5aac5ba805825da76410c181273ba90b1/README.md)
- [Ubuntu 24.04 hosted image](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md)

Review evidence: `action-sources.json`, `controls.json`, `independent-run-audit.json`. The control driver and full local command logs are retained in the publication bundle. Absolute temporary paths in this public copy are normalized; result values and outcomes are unchanged.

