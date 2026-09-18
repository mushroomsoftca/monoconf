"""The examples have to keep working: they are what a reader tries first."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

EXAMPLES = Path(__file__).parent.parent / "examples"

# A fixed environment rather than the caller's: the examples resolve real
# configuration, and a stray variable in the test runner would be an input.
ENV = {"COLUMNS": "88"}


def run(
    example: str, *argv: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(EXAMPLES / example), *argv],
        capture_output=True,
        text=True,
        env=ENV if env is None else env,
        check=False,
    )


def test_minimal_help() -> None:
    result = run("minimal.py", "--help")
    assert result.returncode == 0
    assert result.stdout.startswith("usage: minimal.py [-h] [--output-dir PATH]")
    assert "Serve the widget index." in result.stdout


def test_minimal_run() -> None:
    result = run("minimal.py", "in.csv", "--verbose")
    assert result.returncode == 0, result.stderr
    assert "in.csv -> out (verbose=True)" in result.stdout
    assert "output_dir came from default" in result.stdout


def test_minimal_rejects_a_missing_positional() -> None:
    result = run("minimal.py")
    assert result.returncode == 2
    assert "the following arguments are required: INPUT_PATH" in result.stderr
    assert "Try 'minimal.py --help' for usage." in result.stderr


def test_minimal_reads_the_environment() -> None:
    # app_name is inferred: a class named AppConfig in __main__ gives "app", so the
    # prefix is APP_. That inference is the documented fallback for a script.
    result = run("minimal.py", "in.csv", env={**ENV, "APP_OUTPUT_DIR": "/srv/out"})
    assert result.returncode == 0, result.stderr
    assert "in.csv -> /srv/out" in result.stdout
    assert "output_dir came from APP_OUTPUT_DIR" in result.stdout
