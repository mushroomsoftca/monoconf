"""The environment source: names, parsing rules, and what it refuses to do."""

from __future__ import annotations

import warnings
from enum import StrEnum
from pathlib import Path

import pytest
from pydantic import BaseModel

from monoconf import (
    Config,
    IgnoredValue,
    Origin,
    SourceError,
    SourceKind,
    settings,
)


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"


class Database(BaseModel):
    host: str = "localhost"
    port: int = 5432


class App(Config):
    # The file layers are off: a real /etc/myapp.toml on the machine running
    # these tests is not an input to them.
    model_config = settings(
        app_name="myapp", prog="myapp", system_config=None, user_config=None
    )

    dataset: str

    port: int = 8080
    log_level: LogLevel = LogLevel.INFO
    verbose: bool = False
    color: bool = True
    name: str = "widgets"
    tags: list[str] = []
    hosts: set[str] = set()
    proxy: str | None = None
    database: Database = Database()


def resolve(env: dict[str, str], *argv: str) -> App:
    return App.resolve(argv=["widgets", *argv], env=env)


def test_the_environment_beats_the_defaults() -> None:
    cfg = resolve({"MYAPP_PORT": "9090"})
    assert cfg.port == 9090
    assert cfg.origin_of("port") == Origin(SourceKind.ENV, detail="MYAPP_PORT")
    assert cfg.origin_of("port").describe() == "MYAPP_PORT"


def test_the_cli_beats_the_environment() -> None:
    cfg = resolve({"MYAPP_PORT": "9090"}, "--port", "7070")
    assert cfg.port == 7070
    assert cfg.origin_of("port").source is SourceKind.CLI


def test_each_layer_takes_over_as_the_one_above_is_removed() -> None:
    env = {"MYAPP_PORT": "9090"}
    assert resolve(env, "--port", "7070").port == 7070
    assert resolve(env).port == 9090
    assert resolve({}).port == 8080


def test_nesting_uses_a_double_underscore() -> None:
    cfg = resolve({"MYAPP_DATABASE__HOST": "db.internal"})
    assert cfg.database.host == "db.internal"
    assert cfg.database.port == 5432
    assert cfg.origin_of("database.host").detail == "MYAPP_DATABASE__HOST"


def test_an_empty_prefix_is_honoured() -> None:
    class Unprefixed(Config):
        model_config = settings(
            app_name="myapp", env_prefix="", system_config=None, user_config=None
        )

        port: int = 8080

    assert Unprefixed.resolve(argv=[], env={"PORT": "9090"}).port == 9090


def test_the_environment_can_be_turned_off_entirely() -> None:
    class NoEnv(Config):
        model_config = settings(
            app_name="myapp", env_enabled=False, system_config=None, user_config=None
        )

        port: int = 8080

    cfg = NoEnv.resolve(argv=[], env={"MYAPP_PORT": "9090"})
    assert cfg.port == 8080
    assert cfg.origin_of("port").source is SourceKind.DEFAULT


def test_an_unknown_prefixed_variable_is_ignored_in_silence() -> None:
    # MYAPP_HOME and friends are not monoconf's to complain about.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cfg = resolve({"MYAPP_HOME": "/srv/myapp", "MYAPP_DATABASE": "{}"})
    assert cfg.port == 8080


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "True", "yes", "on", " on "])
def test_boolean_true_forms(value: str) -> None:
    assert resolve({"MYAPP_VERBOSE": value}).verbose is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", " off "])
def test_boolean_false_forms(value: str) -> None:
    assert resolve({"MYAPP_COLOR": value}).color is False


@pytest.mark.parametrize("value", ["maybe", "", "2", "t"])
def test_a_bad_boolean_names_the_variable_and_the_accepted_forms(value: str) -> None:
    with pytest.raises(SourceError) as caught:
        resolve({"MYAPP_VERBOSE": value})
    message = str(caught.value)
    assert "MYAPP_VERBOSE" in message
    assert "1/0, true/false, yes/no or on/off" in message


def test_a_list_is_comma_separated() -> None:
    assert resolve({"MYAPP_TAGS": "a,b,c"}).tags == ["a", "b", "c"]


def test_list_items_are_stripped_and_empty_items_are_kept() -> None:
    # Dropping an item the operator typed would be the worse surprise.
    assert resolve({"MYAPP_TAGS": "a, b,"}).tags == ["a", "b", ""]


def test_a_list_can_be_written_as_a_json_array() -> None:
    # Which is the only way to express an item containing a comma.
    assert resolve({"MYAPP_TAGS": '["a,b", "c"]'}).tags == ["a,b", "c"]
    assert resolve({"MYAPP_TAGS": "[]"}).tags == []


def test_a_malformed_json_array_names_the_variable_and_both_forms() -> None:
    with pytest.raises(SourceError) as caught:
        resolve({"MYAPP_TAGS": '["a", "b"'})
    message = str(caught.value)
    assert "MYAPP_TAGS" in message
    assert "comma-separated" in message


def test_an_empty_variable_clears_a_list() -> None:
    assert resolve({"MYAPP_TAGS": ""}).tags == []


def test_a_set_deduplicates() -> None:
    assert resolve({"MYAPP_HOSTS": "a,b,a"}).hosts == {"a", "b"}


def test_an_empty_variable_clears_a_nullable_field() -> None:
    assert resolve({"MYAPP_PROXY": "http://proxy:3128"}).proxy == "http://proxy:3128"
    assert resolve({"MYAPP_PROXY": ""}).proxy is None


def test_an_empty_variable_is_an_empty_string_when_the_field_is_not_nullable() -> None:
    assert resolve({"MYAPP_NAME": ""}).name == ""


def test_a_scalar_keeps_the_whitespace_it_was_given() -> None:
    assert resolve({"MYAPP_NAME": " padded "}).name == " padded "


def test_an_enum_comes_through_as_its_value() -> None:
    assert resolve({"MYAPP_LOG_LEVEL": "debug"}).log_level is LogLevel.DEBUG


def test_a_variable_setting_a_required_field_warns_and_is_ignored() -> None:
    with pytest.warns(IgnoredValue, match="MYAPP_DATASET is set"):
        cfg = App.resolve(argv=["from-cli"], env={"MYAPP_DATASET": "from-env"})
    assert cfg.dataset == "from-cli"
    assert cfg.origin_of("dataset").source is SourceKind.CLI


def test_a_required_field_is_still_required_when_the_environment_sets_it() -> None:
    with pytest.warns(IgnoredValue), pytest.raises(SourceError, match="required"):
        App.resolve(argv=[], env={"MYAPP_DATASET": "from-env"})


def test_a_bad_value_reports_the_variable_it_came_from() -> None:
    from monoconf import ValidationFailed

    with pytest.raises(ValidationFailed) as caught:
        resolve({"MYAPP_PORT": "nine thousand"})
    message = str(caught.value)
    assert "port:" in message
    assert "from MYAPP_PORT" in message


def test_the_environment_is_an_argument_rather_than_global_state() -> None:
    class Unprefixed(Config):
        model_config = settings(
            app_name="myapp", env_prefix="", system_config=None, user_config=None
        )

        path: str = "unset"

    # env={} means an empty environment, not "fall back to os.environ" -- and PATH
    # is certainly set in the process running this test.
    assert Unprefixed.resolve(argv=[], env={}).path == "unset"
    assert Unprefixed.resolve(argv=[], env={"PATH": "/bin"}).path == "/bin"


def test_case_matters() -> None:
    assert resolve({"myapp_port": "9090"}).port == 8080


def test_a_path_field_comes_through_as_a_path() -> None:
    class WithPath(Config):
        model_config = settings(app_name="myapp", system_config=None, user_config=None)

        output_dir: Path = Path("out/")

    cfg = WithPath.resolve(argv=[], env={"MYAPP_OUTPUT_DIR": "/srv/out"})
    assert cfg.output_dir == Path("/srv/out")
