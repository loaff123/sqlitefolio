"""Repeat the bounded public-runtime qualification without modifying the checkout.

Run with the exact Python described in docs/profile.md:
    python qualification/ci_qualify.py --output /tmp/sqlitefolio-ci-results

The output directory must be new. --wheelhouse enables an offline local run with
the pinned setuptools wheel. All command logs survive errors. CI covers one
Linux x86_64 runtime, trusted synthetic inputs, and terminal workflows; it does
not qualify other engines, production workloads, hostile SQL, or browser layout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]
BUILD_VERSION = "3.12.14 (main, Sep 24 2026, 17:57:43) [Clang 22.1.3 ]"
SQLITE_SOURCE_ID = (
    "2026-05-05 10:34:17 "
    "c88b22011a54b4f6fbd149e9f8e4de77658ce58143a1af0e3785e4e6475127e9"
)
COPY_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", "*.egg-info", ".git", "dist", "build"
)
SOURCE_ENTRIES = (
    "pyproject.toml", "MANIFEST.in", "README.md", "LICENSE", "src", "docs",
    "examples", "fixtures", "qualification", "tests", ".github",
)
TEST_ENTRIES = ("tests", "fixtures", "qualification", "examples")


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def copy_tree(source, destination, entries):
    destination.mkdir()
    for name in entries:
        origin = source / name
        if origin.is_dir():
            shutil.copytree(origin, destination / name, ignore=COPY_IGNORE)
        else:
            shutil.copy2(origin, destination / name)


class Qualification:
    def __init__(self, output, wheelhouse):
        self.output = output
        self.wheelhouse = wheelhouse
        self.work = Path(tempfile.mkdtemp(prefix="sqlitefolio-ci-"))
        self.records = []
        self.env = dict(os.environ)
        for key in ("PYTHONPATH", "PYTHONHOME"):
            self.env.pop(key, None)
        self.env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
                        PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_CACHE_DIR="1")

    def run(self, label, command, cwd, *, pythonpath=None, expected=0, timeout=600):
        env = dict(self.env)
        if pythonpath is not None:
            env["PYTHONPATH"] = str(pythonpath)
        command = list(map(str, command))
        record = {"label": label, "command": command, "cwd": str(cwd),
                  "pythonpath": env.get("PYTHONPATH"), "expected_exit": expected,
                  "matched": False}
        start = time.monotonic()
        self.records.append(record)
        print(label, flush=True)
        try:
            with (self.output / (label + ".stdout.log")).open("wb") as out:
                with (self.output / (label + ".stderr.log")).open("wb") as err:
                    result = subprocess.run(command, cwd=cwd, env=env, stdout=out,
                                            stderr=err, timeout=timeout)
            record.update(observed_exit=result.returncode,
                          matched=result.returncode == expected)
        except Exception as exc:
            record["error"] = str(exc)
            raise
        finally:
            record["elapsed_seconds"] = round(time.monotonic() - start, 3)
            write_json(self.output / "commands.json", self.records)
        require(record["matched"], f"{label}: expected exit {expected}, got {result.returncode}; see logs")
        return self.output / (label + ".stdout.log")

    def environment(self, name, *, build_tools=False):
        destination = self.work / (name + "-venv")
        self.run(name + "-venv", [sys.executable, "-m", "venv", destination], self.work)
        python = destination / "bin" / "python"
        if build_tools:
            options = ["--no-index", "--find-links", self.wheelhouse] if self.wheelhouse else []
            self.run(name + "-setuptools", [python, "-m", "pip", "install",
                     "--no-deps", *options, "setuptools==84.0.0"], self.work)
            self.run(name + "-tool-version", [python, "-c",
                     "import setuptools; assert setuptools.__version__ == '84.0.0'; print(setuptools.__version__)"], self.work)
        return python

    def check_package(self, label, python, cwd, package_parent, pythonpath=None):
        self.run(label + "-import-origin", [python, "-c",
                 "from pathlib import Path; import sqlitefolio, sys; "
                 "p=Path(sqlitefolio.__file__).resolve(); print(p); "
                 "assert p.is_relative_to(Path(sys.argv[1]).resolve()), 'source shadowing'",
                 package_parent], cwd, pythonpath=pythonpath)

    def qualify(self, label, python, cwd, *, pythonpath=None):
        self.run(label + "-tests", [python, "-m", "unittest", "discover", "-s", "tests", "-v"],
                 cwd, pythonpath=pythonpath)
        test_log = (self.output / (label + "-tests.stderr.log")).read_text()
        count = re.search(r"Ran (\d+) tests? in", test_log)
        require(count is not None and int(count[1]) >= 148,
                label + ": fewer than the 148 qualified tests ran")
        require("skipped=" not in test_log, label + ": skipped tests are not qualification")
        parity = self.output / (label + "-parity.json")
        self.run(label + "-parity", [python, "qualification/independent_semantics.py", "--output", parity],
                 cwd, pythonpath=pythonpath)
        report = json.loads(parity.read_text())
        require(len(report["executions"]) == 44 and not report["mismatches"]
                and report["frozen_execution_count"] == 24,
                label + ": expected 44 matching native comparisons (24 frozen)")
        calibration = self.output / (label + "-calibration.json")
        self.run(label + "-calibration", [python, "qualification/calibrate_limits.py", "--output", calibration],
                 cwd, pythonpath=pythonpath)
        report = json.loads(calibration.read_text())
        require(len(report["results"]) == 7 and report["all_expected"],
                label + ": expected seven matching boundary calibrations")
        self.journeys(label, python, cwd, pythonpath)

    def journeys(self, label, python, cwd, pythonpath):
        # Installed distributions exercise the real console entry point too.
        cli = [python.parent / "sqlitefolio"] if pythonpath is None else [python, "-m", "sqlitefolio"]
        self.run(label + "-profile", [*cli, "profile"], cwd, pythonpath=pythonpath)
        for scenario, expected in (("manifest", 0), ("comparison", 1)):
            prefix = label + "-" + scenario
            manifest = "examples/rebuild/" + scenario + ".json"
            packet = self.output / (prefix + "-packet")
            for verb, arguments in (
                ("rehearse", [manifest, "--out", packet, "--trust-input"]),
                ("verify", [packet]),
                ("inspect", [packet]),
                ("replay", [packet, "--manifest", manifest, "--trust-input"]),
            ):
                stdout = self.run(prefix + "-" + verb, [*cli, verb, *arguments], cwd,
                                  pythonpath=pythonpath, expected=expected)
                if verb == "inspect":
                    require(stdout.read_bytes() == (packet / "report.html").read_bytes(),
                            prefix + ": inspected HTML differs from packet report")

    def execute(self):
        with sqlite3.connect(":memory:") as connection:
            runtime = {"python_build": sys.version, "sqlite": sqlite3.sqlite_version,
                       "sqlite_source_id": connection.execute("SELECT sqlite_source_id()").fetchone()[0],
                       "compile_options": sorted(row[0] for row in connection.execute("PRAGMA compile_options")),
                       "implementation": platform.python_implementation(), "system": platform.system(),
                       "machine": platform.machine(), "platform": platform.platform(),
                       "libc": platform.libc_ver()}
        write_json(self.output / "runtime.json", runtime)
        require(sys.version == BUILD_VERSION and sqlite3.sqlite_version == "3.53.1"
                and runtime["sqlite_source_id"] == SQLITE_SOURCE_ID
                and runtime["implementation"] == "CPython"
                and runtime["system"] == "Linux" and runtime["machine"] == "x86_64",
                "Run with the exact qualified archive from docs/profile.md")
        source = self.work / "source"
        copy_tree(ROOT, source, SOURCE_ENTRIES)
        self.check_package("source", Path(sys.executable), source, source / "src", source / "src")
        self.qualify("source", Path(sys.executable), source, pythonpath=source / "src")

        builder = self.environment("build", build_tools=True)
        builds = []
        for number in (1, 2):
            tree = self.work / ("build-" + str(number))
            copy_tree(source, tree, SOURCE_ENTRIES)
            self.run("build-" + str(number), [builder, "qualification/build_dist.py"], tree)
            builds.append(tree / "dist")
        hashes = [{p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir())}
                  for d in builds]
        write_json(self.output / "build-hashes.json", {"builds": hashes, "identical": hashes[0] == hashes[1]})
        require(len(hashes[0]) == 2 and hashes[0] == hashes[1], "Wheel/sdist builds are not byte-identical")
        shutil.copytree(builds[0], self.output / "dist")
        wheel, = builds[0].glob("*.whl")
        sdist, = builds[0].glob("*.tar.gz")

        wheel_python = self.environment("wheel")
        self.run("wheel-install", [wheel_python, "-m", "pip", "install", "--no-index", "--no-deps", wheel], self.work)
        wheel_tests = self.work / "wheel-tests"
        copy_tree(source, wheel_tests, TEST_ENTRIES)
        self.check_package("wheel", wheel_python, wheel_tests, wheel_python.parent.parent / "lib")
        self.qualify("wheel", wheel_python, wheel_tests)

        extracted = self.work / "extracted"
        extracted.mkdir()
        with tarfile.open(sdist, "r:gz") as archive:
            archive.extractall(extracted, filter="data")
        extracted_source, = extracted.iterdir()
        self.check_package("sdist-extracted", Path(sys.executable), extracted_source,
                           extracted_source / "src", extracted_source / "src")
        self.qualify("sdist-extracted", Path(sys.executable), extracted_source,
                     pythonpath=extracted_source / "src")
        sdist_python = self.environment("sdist", build_tools=True)
        self.run("sdist-install", [sdist_python, "-m", "pip", "install", "--no-index",
                 "--no-deps", "--no-build-isolation", sdist], self.work)
        sdist_tests = self.work / "sdist-tests"
        copy_tree(extracted_source, sdist_tests, TEST_ENTRIES)
        self.check_package("sdist", sdist_python, sdist_tests, sdist_python.parent.parent / "lib")
        self.qualify("sdist", sdist_python, sdist_tests)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    require(not output.is_relative_to(ROOT), "Keep qualification output outside the checkout")
    output.mkdir(parents=True, exist_ok=False)
    runner = Qualification(output, args.wheelhouse.resolve() if args.wheelhouse else None)
    summary = {"passed": False, "scope": "One exact Linux x86_64 runtime; trusted synthetic inputs only"}
    try:
        runner.execute()
        summary["passed"] = True
        return 0
    except Exception:
        failure = traceback.format_exc()
        (output / "failure.log").write_text(failure)
        print(failure, file=sys.stderr)
        return 1
    finally:
        summary["command_count"] = len(runner.records)
        write_json(output / "summary.json", summary)
        print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
