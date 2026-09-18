"""Name derivations: one implementation each, pinned here."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel

from monoconf import Field
from monoconf.fields import (
    CliMeta,
    cli_meta,
    env_name_for,
    flag_for,
    metavar_for,
    metavar_from_name,
    singularise,
)


@pytest.mark.parametrize(
    ("plural", "singular"),
    [
        ("tags", "tag"),
        ("entries", "entry"),
        ("addresses", "address"),
        ("boxes", "box"),
        ("branches", "branch"),
        ("ties", "tie"),
        # Irregular or not a plural at all: left alone.
        ("status", "status"),
        ("extra", "extra"),
        ("class", "class"),
        ("analysis", "analysis"),
        ("children", "children"),
    ],
)
def test_singularise(plural: str, singular: str) -> None:
    assert singularise(plural) == singular


@pytest.mark.parametrize(
    ("path", "repeated", "override", "flag"),
    [
        ("output_dir", False, None, "--output-dir"),
        ("database.host", False, None, "--database-host"),
        ("tags", True, None, "--tag"),
        ("database.tags", True, None, "--database-tag"),
        ("children", True, None, "--children"),
        ("children", True, "child", "--child"),
        ("children", True, "--child", "--child"),
    ],
)
def test_flag_for(path: str, repeated: bool, override: str | None, flag: str) -> None:
    assert flag_for(path, repeated=repeated, override=override) == flag


@pytest.mark.parametrize(
    ("path", "prefix", "name"),
    [
        ("port", "MYAPP_", "MYAPP_PORT"),
        ("database.host", "MYAPP_", "MYAPP_DATABASE__HOST"),
        ("port", "", "PORT"),
    ],
)
def test_env_name_for(path: str, prefix: str, name: str) -> None:
    assert env_name_for(path, prefix) == name


@pytest.mark.parametrize(
    ("annotation", "metavar"),
    [
        (Path, "PATH"),
        (int, "N"),
        (float, "X"),
        (Decimal, "DECIMAL"),
        (UUID, "UUID"),
        (datetime, "DATETIME"),
        (date, "DATE"),
        (time, "TIME"),
        (timedelta, "DURATION"),
        (str, "SOME_FIELD"),
    ],
)
def test_metavar_for(annotation: Any, metavar: str) -> None:
    assert metavar_for("some_field", annotation) == metavar


def test_metavar_overrides_and_nesting() -> None:
    assert metavar_for("port", int, "PORT") == "PORT"
    assert metavar_for("database.host", str) == "DATABASE_HOST"
    assert metavar_from_name("input_path") == "INPUT_PATH"
    assert metavar_from_name("input_path", "FILE") == "FILE"


def test_field_round_trips_cli_metadata() -> None:
    class Model(BaseModel):
        retries: int = Field(3, description="Retry count.", short="r", metavar="N")
        children: list[str] = Field(default_factory=list, flag="child")
        plain: int = 1

    retries = cli_meta(Model.model_fields["retries"])
    assert (retries.short, retries.metavar, retries.flag) == ("r", "N", None)
    assert Model.model_fields["retries"].description == "Retry count."
    assert cli_meta(Model.model_fields["children"]).flag == "child"
    assert cli_meta(Model.model_fields["plain"]) == CliMeta()


def test_field_preserves_pydantic_keys_and_user_schema_extra() -> None:
    class Model(BaseModel):
        port: int = Field(
            8080,
            ge=1,
            le=65535,
            short="p",
            json_schema_extra={"example": 8080},
        )

    info = Model.model_fields["port"]
    assert cli_meta(info).short == "p"
    extra = info.json_schema_extra
    assert isinstance(extra, dict)
    assert extra["example"] == 8080
    with pytest.raises(ValueError, match="less than or equal to 65535"):
        Model(port=70000)


def test_field_without_cli_keys_stores_nothing() -> None:
    class Model(BaseModel):
        plain: int = Field(1, description="No CLI keys here.")

    assert cli_meta(Model.model_fields["plain"]) == CliMeta()
    assert Model.model_fields["plain"].json_schema_extra is None


def test_unrelated_schema_extra_is_not_mistaken_for_cli_metadata() -> None:
    from pydantic import Field as pydantic_field

    class Model(BaseModel):
        plain: int = pydantic_field(1, json_schema_extra={"example": 1})
        confused: int = pydantic_field(1, json_schema_extra={"monoconf": "not a dict"})

    assert cli_meta(Model.model_fields["plain"]) == CliMeta()
    assert cli_meta(Model.model_fields["confused"]) == CliMeta()
