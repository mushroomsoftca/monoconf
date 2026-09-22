"""`--show-config`: the resolved configuration as TOML, and where each value came from.

The exact layout is pinned by the snapshots. These are the behaviours: when the
flag exists, what it needs, what it runs, and that its output is a config file.
"""

from __future__ import annotations

import re
import warnings
from enum import StrEnum
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, model_validator

from monoconf import (
    Config,
    HelpRequested,
    MonoconfWarning,
    ShowConfigRequested,
    SourceError,
    ValidationFailed,
    settings,
)


class Level(StrEnum):
    INFO = "info"
    DEBUG = "debug"


class Database(BaseModel):
    host: str = "localhost"
    port: int = 5432


class App(Config):
    """Serve the widget index."""

    model_config = settings(
        app_name="myapp",
        prog="myapp",
        show_config=True,
        system_config=None,
        user_config=None,
    )

    input_path: Path
    dataset: str

    port: int = 8080
    ratio: float = 0.5
    name: str = "widgets"
    verbose: bool = False
    level: Level = Level.INFO
    proxy: str | None = None
    tags: list[str] = []
    database: Database = Database()

    @model_validator(mode="after")
    def check_port(self) -> App:
        if self.port == 7:
            raise ValueError("port 7 is reserved for the echo service")
        return self


def show(*argv: str, env: dict[str, str] | None = None) -> str:
    with pytest.raises(ShowConfigRequested) as caught:
        App.resolve(argv=["--show-config", *argv], env=env or {})
    assert caught.value.exit_code == 0
    return caught.value.text


def test_it_is_off_unless_enabled() -> None:
    class Plain(Config):
        model_config = settings(app_name="myapp", system_config=None, user_config=None)

        port: int = 8080

    with pytest.raises(SourceError, match="unrecognized arguments: --show-config"):
        Plain.resolve(argv=["--show-config"], env={})
    with pytest.raises(HelpRequested) as caught:
        Plain.resolve(argv=["--help"], env={})
    assert "--show-config" not in caught.value.text


def test_it_is_listed_in_help_when_enabled() -> None:
    with pytest.raises(HelpRequested) as caught:
        App.resolve(argv=["--help"], env={})
    assert "--show-config" in caught.value.text


def test_help_beats_it() -> None:
    with pytest.raises(HelpRequested):
        App.resolve(argv=["--show-config", "--help"], env={})


def test_it_does_not_need_the_positionals() -> None:
    text = show()
    assert text.startswith("# myapp resolved configuration\n")
    assert re.search(r"^# input_path +# positional, CLI-only, not given$", text, re.M)
    assert re.search(r"^# dataset +# positional, CLI-only, not given$", text, re.M)


def test_a_given_positional_is_shown_commented_out() -> None:
    text = show("in.csv")
    assert re.search(r'^# input_path = "in.csv" +# positional, CLI-only$', text, re.M)
    assert re.search(r"^# dataset +# positional, CLI-only, not given$", text, re.M)


def test_it_reflects_the_rest_of_the_command_line() -> None:
    text = show("--port", "9090", env={"MYAPP_DATABASE__HOST": "db.internal"})
    assert re.search(r"^port = 9090 +# --port$", text, re.M)
    assert re.search(r'^host = "db.internal" +# MYAPP_DATABASE__HOST$', text, re.M)


def test_a_bad_value_fails_as_it_would_in_a_real_run() -> None:
    with pytest.raises(ValidationFailed, match="'x' is not a valid integer"):
        App.resolve(argv=["--show-config", "--port", "x"], env={})


def test_the_application_s_own_validators_still_run() -> None:
    with pytest.raises(ValidationFailed, match="reserved for the echo service"):
        App.resolve(argv=["--show-config", "--port", "7"], env={})


def test_a_normal_run_still_needs_its_positionals() -> None:
    show()  # derives the relaxed model, which must not leak into a normal run
    with pytest.raises(SourceError, match="required: INPUT_PATH, DATASET"):
        App.resolve(argv=[], env={})


def test_load_prints_it_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        App.load(argv=["--show-config"], env={})
    assert caught.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("# myapp resolved configuration\n")


def round_trip(tmp_path: Path, argv: list[str], env: dict[str, str]) -> None:
    """Dump, feed the dump back as the only config file, and compare.

    The positionals are given both times: they are the one thing a dump cannot
    carry, being CLI-only.
    """
    positionals = ["in.csv", "widgets"]
    before = App.resolve(argv=[*positionals, *argv], env=env)
    dumped = show(*positionals, *argv, env=env)
    path = tmp_path / "dumped.toml"
    path.write_text(dumped, encoding="utf-8")

    class FromDump(App):
        model_config = settings(
            app_name="myapp", prog="myapp", system_config=path, user_config=None
        )

    with warnings.catch_warnings():
        warnings.simplefilter("error", MonoconfWarning)
        after = FromDump.resolve(argv=positionals, env={})
    assert after.model_dump() == before.model_dump()


def test_the_output_round_trips(tmp_path: Path) -> None:
    round_trip(
        tmp_path,
        ["--port", "9090", "--verbose", "--tag", "a", "--tag", "b"],
        {"MYAPP_LEVEL": "debug", "MYAPP_DATABASE__HOST": "db.internal"},
    )


@given(
    port=st.integers(),
    ratio=st.floats(allow_nan=False),
    name=st.text().filter(lambda s: "\x00" not in s),
    tags=st.lists(st.text().filter(lambda s: s and "\x00" not in s)),
    verbose=st.booleans(),
)
def test_any_values_round_trip(
    tmp_path_factory: pytest.TempPathFactory,
    port: int,
    ratio: float,
    name: str,
    tags: list[str],
    verbose: bool,
) -> None:
    if port == 7:
        return
    argv = [f"--port={port}", f"--ratio={ratio!r}", f"--name={name}"]
    argv += [f"--tag={tag}" for tag in tags]
    if verbose:
        argv.append("--verbose")
    round_trip(tmp_path_factory.mktemp("dump"), argv, {})
