"""End-to-end resolution over the layers that exist: defaults and the CLI."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel

from monoconf import (
    Config,
    Field,
    HelpRequested,
    MonoconfError,
    Origin,
    SourceError,
    SourceKind,
    ValidationFailed,
    settings,
)


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"


class Database(BaseModel):
    host: str = "localhost"
    port: int = 5432


class App(Config):
    """Serve the widget index."""

    # The file layers are off: a real /etc/myapp.toml on the machine running
    # these tests is not an input to them.
    model_config = settings(
        app_name="myapp", prog="myapp", system_config=None, user_config=None
    )

    input_path: Path
    dataset: str

    output_dir: Path = Path("out/")
    log_level: LogLevel = LogLevel.INFO
    mode: Literal["fast", "slow"] = "fast"
    verbose: bool = False
    color: bool = True
    port: int = Field(8080, description="Listen port.", short="p")
    retries: int = 3
    suffix: str = ".csv"
    tags: list[str] = []
    hosts: set[str] = set()
    proxy: str | None = None
    database: Database = Database()


POSITIONALS = ["in.csv", "widgets"]


def resolve(*argv: str) -> App:
    return App.resolve(argv=[*POSITIONALS, *argv], env={})


def test_defaults_resolve_when_nothing_is_passed() -> None:
    cfg = resolve()
    assert cfg.input_path == Path("in.csv")
    assert cfg.dataset == "widgets"
    assert cfg.output_dir == Path("out/")
    assert cfg.log_level is LogLevel.INFO
    assert cfg.verbose is False
    assert cfg.color is True
    assert cfg.tags == []
    assert cfg.database.host == "localhost"


def test_positionals_are_required_and_ordered() -> None:
    with pytest.raises(SourceError, match="the following arguments are required"):
        App.resolve(argv=["in.csv"], env={})


def test_the_cli_beats_the_defaults() -> None:
    cfg = resolve("--output-dir", "/tmp/out", "--log-level", "debug", "--retries", "5")
    assert cfg.output_dir == Path("/tmp/out")
    assert cfg.log_level is LogLevel.DEBUG
    assert cfg.retries == 5


def test_a_cli_value_equal_to_the_default_still_comes_from_the_cli() -> None:
    # The sentinel is what makes this distinguishable, and the whole precedence
    # chain rests on it: --port 8080 must not lose to a file setting 9090 in M7.
    cfg = resolve("--port", "8080")
    assert cfg.port == 8080
    assert cfg.origin_of("port") == Origin(SourceKind.CLI, detail="--port")
    assert resolve().origin_of("port") == Origin(SourceKind.DEFAULT)


def test_the_flag_as_typed_is_what_provenance_reports() -> None:
    assert resolve("-p", "9090").origin_of("port").detail == "-p"
    assert resolve("--port", "9090").origin_of("port").detail == "--port"


def test_boolean_flags_are_presence_only() -> None:
    cfg = resolve("--verbose", "--no-color")
    assert cfg.verbose is True
    assert cfg.color is False
    assert cfg.origin_of("verbose").detail == "--verbose"
    assert cfg.origin_of("color").detail == "--no-color"


def test_a_true_default_generates_only_the_negative_form() -> None:
    with pytest.raises(SourceError, match="unrecognized arguments: --color"):
        resolve("--color")


def test_booleans_take_no_value() -> None:
    # `--verbose true` would leave "true" as an extra positional.
    with pytest.raises(SourceError, match="unrecognized arguments: true"):
        resolve("--verbose", "true")


def test_repeated_flags_build_a_list() -> None:
    cfg = resolve("--tag", "a", "--tag", "b")
    assert cfg.tags == ["a", "b"]
    assert cfg.origin_of("tags").detail == "--tag"


def test_a_repeated_flag_replaces_the_default_rather_than_appending() -> None:
    class WithDefaults(Config):
        model_config = settings(
            app_name="myapp", prog="myapp", system_config=None, user_config=None
        )

        tags: list[str] = ["base"]

    assert WithDefaults.resolve(argv=["--tag", "extra"], env={}).tags == ["extra"]


def test_a_single_empty_value_clears_a_list() -> None:
    assert resolve("--tag", "").tags == []


def test_sets_deduplicate() -> None:
    assert resolve("--host", "a", "--host", "b", "--host", "a").hosts == {"a", "b"}


def test_an_empty_string_clears_a_nullable_field() -> None:
    assert resolve("--proxy", "http://proxy:3128").proxy == "http://proxy:3128"
    assert resolve("--proxy", "").proxy is None


def test_an_empty_string_stays_a_string_when_the_field_is_not_nullable() -> None:
    assert resolve("--suffix", "").suffix == ""


def test_nested_fields_take_their_own_flags() -> None:
    cfg = resolve("--database-host", "db.internal", "--database-port", "6000")
    assert cfg.database.host == "db.internal"
    assert cfg.database.port == 6000
    assert cfg.origin_of("database.host").detail == "--database-host"
    assert cfg.origin_of("database.port").describe() == "--database-port"


def test_flags_are_never_abbreviated() -> None:
    with pytest.raises(SourceError, match="unrecognized arguments: --output"):
        resolve("--output", "/tmp")


def test_an_unknown_flag_is_an_error() -> None:
    with pytest.raises(SourceError, match="unrecognized arguments: --nope"):
        resolve("--nope")


def test_a_bad_value_names_the_field_and_where_it_came_from() -> None:
    with pytest.raises(ValidationFailed) as caught:
        resolve("--port", "nine thousand")
    message = str(caught.value)
    assert "myapp: invalid configuration" in message
    assert "port:" in message
    assert "'nine thousand'" in message
    assert "from --port" in message


def test_every_leaf_knows_where_it_came_from() -> None:
    cfg = resolve("--port", "9090", "--tag", "a")
    paths = {field.path for field in App.__monoconf_spec__.fields}
    assert set(cfg.__monoconf_origin__) == paths
    assert cfg.origin_of("input_path").source is SourceKind.CLI
    assert cfg.origin_of("retries").source is SourceKind.DEFAULT


def test_origin_of_rejects_an_unknown_field() -> None:
    with pytest.raises(MonoconfError, match="no field 'nope'"):
        resolve().origin_of("nope")


def test_a_directly_constructed_instance_has_no_provenance() -> None:
    with pytest.raises(MonoconfError, match="constructed directly"):
        App(input_path=Path("in.csv"), dataset="widgets").origin_of("port")


def test_resolved_instances_are_frozen_and_dump_cleanly() -> None:
    cfg = resolve()
    # Origins live outside the model's fields: dumping a config must not leak them.
    assert "__monoconf_origin__" not in cfg.model_dump()
    assert cfg.model_dump()["port"] == 8080


def test_help_short_circuits_before_the_positionals_are_checked() -> None:
    with pytest.raises(HelpRequested) as caught:
        App.resolve(argv=["--help"], env={})
    assert "usage: myapp" in caught.value.text
    assert caught.value.exit_code == 0


def test_short_help_flag_works_too() -> None:
    with pytest.raises(HelpRequested):
        App.resolve(argv=["-h"], env={})


def test_help_wins_even_alongside_other_arguments() -> None:
    with pytest.raises(HelpRequested):
        App.resolve(argv=["--port", "9090", "--help"], env={})
