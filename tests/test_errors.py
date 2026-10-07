"""Rewritten validation errors: what went wrong, said to the operator.

Each test asserts the one line that matters rather than the whole block; the
whole block, as printed by `load()`, is pinned by the snapshots.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import (
    BaseModel,
    Field,
    Secret,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from monoconf import Config, ValidationFailed, settings


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"


class Database(BaseModel):
    host: str = "localhost"
    port: int = 5432

    @model_validator(mode="after")
    def check_port(self) -> Database:
        if self.port == 7:
            raise ValueError("port 7 is reserved for the echo service")
        return self


class App(Config):
    model_config = settings(
        app_name="myapp", prog="myapp", system_config=None, user_config=None
    )

    retries: int = Field(3, gt=0)
    name: str = "widgets"
    log_level: LogLevel = LogLevel.INFO
    codes: list[int] = []
    database: Database = Database()


def failure(
    argv: list[str] | None = None,
    env: dict[str, str] | None = None,
    model: type[Config] = App,
) -> str:
    with pytest.raises(ValidationFailed) as caught:
        model.resolve(argv=argv or [], env=env or {})
    return str(caught.value)


def with_file(tmp_path: Path, text: str) -> type[Config]:
    path = tmp_path / "myapp.toml"
    path.write_text(text)

    class FromFile(App):
        model_config = settings(
            app_name="myapp", prog="myapp", system_config=path, user_config=None
        )

    return FromFile


def test_a_bad_value_is_named_along_with_where_it_came_from(tmp_path: Path) -> None:
    message = failure(model=with_file(tmp_path, 'retries = "nine thousand"\n'))
    assert message.startswith("myapp: invalid configuration\n\n")
    assert "  retries: 'nine thousand' is not a valid integer\n" in message
    assert f"    from {tmp_path / 'myapp.toml'}" in message


def test_an_enum_mismatch_lists_the_choices() -> None:
    message = failure(env={"MYAPP_LOG_LEVEL": "loud"})
    assert "log_level: 'loud' is not one of: debug, info" in message
    assert "from MYAPP_LOG_LEVEL" in message


def test_a_bad_list_item_shows_its_index_and_the_flag_it_came_from() -> None:
    message = failure(argv=["--code", "200", "--code", "ok"])
    assert "codes[1]: 'ok' is not a valid integer" in message
    assert "from --code" in message


def test_a_nested_value_is_shown_by_its_dotted_path() -> None:
    message = failure(env={"MYAPP_DATABASE__PORT": "high"})
    assert "database.port: 'high' is not a valid integer" in message
    assert "from MYAPP_DATABASE__PORT" in message


def test_a_constraint_reads_as_what_the_value_should_be() -> None:
    message = failure(argv=["--retries", "0"])
    assert "retries: '0' should be greater than 0" in message
    assert "from --retries" in message


def test_a_nested_model_validator_is_attributed_to_the_model() -> None:
    message = failure(env={"MYAPP_DATABASE__PORT": "7"})
    assert "database: port 7 is reserved for the echo service" in message
    assert "from a check on 'database' as a whole" in message


def test_an_unquoted_number_where_a_string_belongs(tmp_path: Path) -> None:
    message = failure(model=with_file(tmp_path, "name = 5\n"))
    assert "name: 5 is not a string; in a config file, quote it" in message


def test_a_value_where_a_table_belongs(tmp_path: Path) -> None:
    message = failure(model=with_file(tmp_path, "database = 5\n"))
    assert "database: 5 is not a table of settings" in message


def test_every_error_gets_a_block() -> None:
    message = failure(
        argv=["--retries", "x"], env={"MYAPP_LOG_LEVEL": "loud", "MYAPP_NAME": ""}
    )
    blocks = [
        line
        for line in message.splitlines()
        if line.startswith("  ") and line[2] != " "
    ]
    assert len(blocks) == 2
    assert "retries:" in message
    assert "log_level:" in message


def test_a_long_value_is_cut_short() -> None:
    message = failure(argv=["--retries", "9" * 200 + "x"])
    line = next(line for line in message.splitlines() if "retries:" in line)
    assert "..." in line
    assert len(line) < 120


def test_pydantic_s_error_is_kept_as_the_cause() -> None:
    with pytest.raises(ValidationFailed) as caught:
        App.resolve(argv=["--retries", "x"], env={})
    assert isinstance(caught.value.__cause__, ValidationError)


def test_load_prints_the_block_once_with_the_hint_beneath(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as caught:
        App.load(argv=["--retries", "x"], env={})
    assert caught.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("myapp: invalid configuration\n")
    assert "myapp: myapp:" not in err
    assert err.endswith("from --retries\n\nTry 'myapp --help' for usage.\n")


def test_a_field_validator_s_own_words_are_kept() -> None:
    class Checked(App):
        @field_validator("name")
        @classmethod
        def no_spaces(cls, value: str) -> str:
            if " " in value:
                raise ValueError("names cannot contain spaces")
            return value

    message = failure(argv=["--name", "a b"], model=Checked)
    assert "name: 'a b' was rejected: names cannot contain spaces" in message


def test_any_other_constraint_falls_back_to_pydantic_s_words() -> None:
    class Short(App):
        name: str = Field("widgets", min_length=3)

    message = failure(argv=["--name", "ab"], model=Short)
    assert "name: 'ab' was rejected: string should have at least 3 characters" in (
        message
    )


def test_a_union_member_name_is_not_shown_as_part_of_the_path() -> None:
    class Either(App):
        mode: Literal["fast", "safe"] | int = "safe"

    message = failure(argv=["--mode", "slow"], model=Either)
    assert "mode: 'slow' is not one of 'fast' or 'safe'" in message
    assert "mode: 'slow' is not a valid integer" in message
    assert "mode.int" not in message


def test_a_value_that_never_arrived_is_required() -> None:
    class Dropping(Config):
        model_config = settings(
            app_name="myapp", prog="myapp", system_config=None, user_config=None
        )

        dataset: str

        # Contrived: nothing monoconf does can leave a field unset by the time it
        # validates, but a validator of the application's can.
        @model_validator(mode="before")
        @classmethod
        def drop_dataset(cls, data: Any) -> Any:
            return {k: v for k, v in data.items() if k != "dataset"}

    message = failure(argv=["widgets"], model=Dropping)
    assert "dataset: is required" in message


def test_a_rejected_secret_is_not_quoted_back() -> None:
    class Guarded(Config):
        model_config = settings(
            app_name="myapp", prog="myapp", system_config=None, user_config=None
        )

        token: SecretStr = Field(SecretStr("x" * 12), min_length=12)
        pin: Secret[int] = Secret(1234)

    message = failure(
        env={"MYAPP_TOKEN": "hunter2", "MYAPP_PIN": "12a4"}, model=Guarded
    )
    # A rejected secret is usually a near miss of the real one.
    assert "hunter2" not in message
    assert "12a4" not in message
    assert "  token: the secret was rejected: " in message
    assert "  pin: the secret is not a valid integer\n    from MYAPP_PIN" in message


def test_a_secret_that_cannot_be_read_is_not_quoted_back() -> None:
    class Keyed(Config):
        model_config = settings(
            app_name="myapp", prog="myapp", system_config=None, user_config=None
        )

        keys: list[SecretStr] = []

    message = failure(env={"MYAPP_KEYS": '["hunter2"'}, model=Keyed)
    assert "hunter2" not in message
    assert "  keys: the secret starts with '[' but is not a valid JSON array" in message
