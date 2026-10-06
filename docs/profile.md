# Qualified runtime and bounds

Initial platform: Linux x86_64, CPython 3.12, SQLite 3.53.1. Other platforms,
Python minor versions and SQLite versions are refused. This is a bounded
trusted-sample qualification, not an engine-security recommendation or a reason
to freeze production dependencies.

Two exact CPython 3.12.14 builds were exercised on Debian GNU/Linux 13,
Linux x86_64, glibc 2.41:

- Original qualification: `3.12.14 (main, Aug 25 2026, 14:00:49) [Clang 22.1.3 ]`
- Publicly acquired qualification: `3.12.14 (main, Sep 24 2026, 17:57:43) [Clang 22.1.3 ]`

Both report SQLite source ID:

```
2026-05-05 10:34:17 c88b22011a54b4f6fbd149e9f8e4de77658ce58143a1af0e3785e4e6475127e9
```

Packets record exact Python patch/build, implementation, machine, SQLite version,
source ID and sorted compile options. Replay requires exact runtime equality;
same version strings alone do not equate different builds. The corpus is
independently qualified on each build, rather than treating a successful run on
one as evidence for the other.

## Obtain the publicly qualified build

[Astral python-build-standalone release 20260924](https://github.com/astral-sh/python-build-standalone/releases/tag/20260924)
contains the exact Linux x86_64 GNU archive used here:

`cpython-3.12.14+20260924-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz`

34,270,188 bytes; SHA-256:
`269b2c99e4db15b242bf01832f4fea1e8f1a664f273cff519393f296e9820b41`

The archive was independently downloaded and its digest checked against the
release-asset digest. It is a Python/SQLite runtime, not part of the SQLiteFolio
wheel or source distribution. Download and verify it before extracting:

```sh
curl --fail --location 'https://github.com/astral-sh/python-build-standalone/releases/download/20260924/cpython-3.12.14%2B20260924-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz' -o python-runtime.tar.gz
printf '%s  %s\n' 269b2c99e4db15b242bf01832f4fea1e8f1a664f273cff519393f296e9820b41 python-runtime.tar.gz | sha256sum --check
mkdir runtime
tar -xzf python-runtime.tar.gz -C runtime
runtime/python/bin/python3 -m venv .venv
.venv/bin/python -m pip install "setuptools==84.0.0"
.venv/bin/python qualification/build_dist.py
.venv/bin/python -m pip install ./dist/sqlitefolio-0.1.0a1-py3-none-any.whl
.venv/bin/sqlitefolio profile
```

The acquisition was also exercised through uv 0.12.19 with an explicit install
directory. For an equivalent install without a shell-link step, use
`uv python install --no-bin --install-dir .runtime 3.12.14`.
[uv documents its use of python-build-standalone distributions](https://docs.astral.sh/uv/guides/install-python/).
Version selection can resolve to a later build in future; pin the archive and
check `profile` for exact reproducibility. Python does not bundle a universally
fixed SQLite across all installation methods.

[SQLite 3.53.1 release notes](https://sqlite.org/releaselog/3_53_1.html)
identify the upstream source ID. Qualifying this library combination makes no
claim about a different SQLite build, native-engine correctness or production
concurrency. No native parser, engine serialization or crash-durability research
is part of the qualification.

## Bound behavior

The [protocol](protocol-v1.md) defines all byte/row/object/time/process limits.
Tests and calibration exercise passing small/near-bound observations and honest
refusals above bounds. These are engineering resource ceilings, not throughput
benchmarks. Parent/result limits are checked separately from worker RLIMITs.
A source file must have an initialized SQLite header of at least 100 bytes and
DELETE-journal header markers. A native zero-byte empty SQLite file is refused;
use explicit schema/seed SQL for an empty baseline instead.

Only original trusted benign fixture SQL is executed. Table/view checks are
finite observations; no virtual tables, temp objects, extensions, custom
functions/collations, live/WAL sources, ATTACH/DETACH, VACUUM or external-file SQL.
