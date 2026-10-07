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
    # output_dir is a Path, printed with this platform's separator.
    assert f"in.csv -> {Path('/srv/out')}" in result.stdout
    assert "output_dir came from APP_OUTPUT_DIR" in result.stdout


def test_full_help() -> None:
    result = run("full.py", "--help")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("usage: widgetd [-h] [--config-override PATH]")
    assert "Serve the widget index over HTTP." in result.stdout
    assert "--show-config" in result.stdout


def test_full_run() -> None:
    env = {**ENV, "WIDGETD_LOG_LEVEL": "debug"}
    result = run("full.py", "-p", "9090", "index.csv", env=env)
    assert result.returncode == 0, result.stderr
    assert "serving index.csv on port 9090" in result.stdout
    assert "log level debug from WIDGETD_LOG_LEVEL" in result.stdout


def test_full_show_config_needs_no_input_file() -> None:
    result = run("full.py", "--show-config", "--port", "9090")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("# widgetd resolved configuration\n")
    lines = [" ".join(line.split()) for line in result.stdout.splitlines()]
    assert "# input_path # positional, CLI-only, not given" in lines
    assert "port = 9090 # --port" in lines


def test_full_show_config_output_pins_the_configuration(tmp_path: Path) -> None:
    env = {**ENV, "WIDGETD_DATABASE__HOST": "db.internal"}
    dumped = run("full.py", "--show-config", "--port", "9090", env=env)
    assert dumped.returncode == 0, dumped.stderr
    pinned = tmp_path / "pinned.toml"
    pinned.write_text(dumped.stdout, encoding="utf-8")
    result = run("full.py", "--config-override", str(pinned), "index.csv")
    assert result.returncode == 0, result.stderr
    assert "serving index.csv on port 9090" in result.stdout
    assert "database db.internal:5432" in result.stdout


def test_full_reports_a_bad_value_with_its_source() -> None:
    result = run("full.py", "index.csv", env={**ENV, "WIDGETD_PORT": "high"})
    assert result.returncode == 2
    assert "port: 'high' is not a valid integer\n    from WIDGETD_PORT" in (
        result.stderr
    )


def test_full_refuses_an_unknown_key(tmp_path: Path) -> None:
    override = tmp_path / "typo.toml"
    override.write_text("prot = 9090\n", encoding="utf-8")
    result = run("full.py", "--config-override", str(override), "index.csv")
    assert result.returncode == 2
    assert "prot" in result.stderr
    assert "port" in result.stderr


def test_nested_names_one_path_three_ways() -> None:
    result = run("nested.py", "nightly", "--database-pool-size", "20")
    assert result.returncode == 0, result.stderr
    assert "nightly on localhost with a pool of 20" in result.stdout
    assert "pool size came from --database-pool-size" in result.stdout

    env = {**ENV, "MYAPP_DATABASE__POOL_SIZE": "30"}
    result = run("nested.py", "nightly", env=env)
    assert result.returncode == 0, result.stderr
    assert "pool size came from MYAPP_DATABASE__POOL_SIZE" in result.stdout


def test_nested_help_lists_the_dotted_flags() -> None:
    result = run("nested.py", "--help")
    assert result.returncode == 0, result.stderr
    assert "--database-pool-size N" in result.stdout
    assert "--database-port N" in result.stdout
