"""The help output: standard argparse layout, with resolved defaults.

Asserted as fragments rather than one literal block on purpose: argparse's
rendering of an option carrying both a short and a long form changed in 3.13, so a
whole-block snapshot would pass on one row of the CI matrix and fail on another.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path

import pytest
from pydantic import BaseModel

from monoconf import Config, Field, HelpRequested, settings


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"


class Database(BaseModel):
    host: str = "localhost"
    port: int = 5432


class App(Config):
    """Serve the widget index."""

    model_config = settings(
        app_name="myapp",
        prog="myapp",
        epilog="See https://example.com/docs for the full config reference.",
    )

    input_path: Path
    dataset: str

    output_dir: Path = Path("out/")
    log_level: LogLevel = LogLevel.INFO
    verbose: bool = Field(False, description="Enable verbose logging.")
    color: bool = Field(True, description="Disable coloured output.")
    retries: int = Field(3, description="Retry count on transient failure.", short="r")
    tags: list[str] = Field(default_factory=list, description="Tag to apply.")
    hosts: set[str] = {"localhost"}
    proxy: str | None = None
    database: Database = Database()


def help_text() -> str:
    with pytest.raises(HelpRequested) as caught:
        App.resolve(argv=["--help"], env={})
    return caught.value.text


def test_usage_lists_options_then_positionals() -> None:
    text = help_text()
    assert text.startswith("usage: myapp [-h]")
    assert "INPUT_PATH DATASET" in " ".join(text.split())


def test_description_and_epilog_frame_the_output() -> None:
    text = help_text()
    assert "Serve the widget index." in text
    assert text.rstrip().endswith(
        "See https://example.com/docs for the full config reference."
    )


def test_positionals_are_listed_by_name() -> None:
    text = help_text()
    assert "positional arguments:" in text
    assert "INPUT_PATH" in text
    assert "DATASET" in text


def test_metavars_come_from_the_type() -> None:
    text = help_text()
    assert "--output-dir PATH" in text
    assert "--tag TAG" in text


def test_resolved_defaults_are_shown_in_brackets() -> None:
    text = help_text()
    assert "[out]" in text  # output_dir
    assert "[info]" in text  # an enum shows its value
    assert "[3]" in text  # retries
    assert "[none]" in text  # proxy is None and tags is empty
    assert "[localhost]" in text  # a set is rendered sorted


def test_booleans_show_no_default() -> None:
    text = help_text()
    # Which flag exists says what the default is; a bracket would be noise.
    assert re.search(r"--verbose\s+Enable verbose logging\.", text)
    assert "--no-color" in text
    assert "[false]" not in text
    assert "[true]" not in text


def test_choices_are_enumerated_from_the_type() -> None:
    assert "One of: debug, info, warning." in help_text()


def test_repeatable_flags_say_so() -> None:
    assert re.search(r"--tag TAG\s+Tag to apply\. Repeatable\. \[none\]", help_text())


def test_short_flags_appear_with_their_long_form() -> None:
    text = help_text()
    assert "-r" in text
    assert "--retries N" in text


def test_help_is_readable_with_no_metadata_at_all() -> None:
    class Bare(Config):
        model_config = settings(app_name="bare", prog="bare")

        source: Path
        limit: int = 10

    with pytest.raises(HelpRequested) as caught:
        Bare.resolve(argv=["--help"], env={})
    text = caught.value.text
    assert text.startswith("usage: bare [-h] [--limit N] SOURCE")
    assert "--limit N" in text
    assert "[10]" in text


def test_width_does_not_depend_on_the_terminal() -> None:
    # Help output is part of the product: a README capture must not vary with the
    # terminal that produced it.
    assert all(len(line) <= 88 for line in help_text().splitlines())


def test_defaults_that_need_rendering_rather_than_printing() -> None:
    class Rendered(Config):
        model_config = settings(app_name="myapp", prog="myapp")

        suffix: str = ""
        flags: list[bool] = [True, False]

    with pytest.raises(HelpRequested) as caught:
        Rendered.resolve(argv=["--help"], env={})
    text = caught.value.text
    assert '[""]' in text  # an empty string is not the same as no value
    assert "[true, false]" in text


def test_help_shows_a_default_resolved_from_the_environment() -> None:
    class Served(Config):
        model_config = settings(app_name="myapp", prog="myapp")

        port: int = 8080

    with pytest.raises(HelpRequested) as caught:
        Served.resolve(argv=["--help"], env={"MYAPP_PORT": "9090"})
    # The bracketed value is what omitting the flag would actually give, which is
    # the whole reason help renders after the lower layers have resolved.
    assert "[9090]" in caught.value.text
    assert "[8080]" not in caught.value.text
