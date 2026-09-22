"""Every definition-time error, and the shapes that must keep working.

Each error is asserted at class-definition time: the `class` statement itself is
inside `pytest.raises`, because a malformed schema has to fail on import rather
than at `load()`.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum, IntEnum, StrEnum
from ipaddress import IPv4Address, IPv6Address
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from uuid import UUID

import pytest
from pydantic import BaseModel, BeforeValidator, Strict

from monoconf import Config, Field, SchemaError, settings


class Database(BaseModel):
    host: str = "localhost"
    port: int = 5432


def define(
    class_name: str,
    field_name: str,
    annotation: object,
    default: object = None,
) -> type[Config]:
    """Define a Config subclass whose field name or type is parametrised.

    Same thing as a `class` statement, including firing the definition-time checks.
    """
    namespace: dict[str, Any] = {
        "model_config": settings(app_name="myapp"),
        "__annotations__": {field_name: annotation},
        field_name: default,
    }
    return cast("type[Config]", type(class_name, (Config,), namespace))


def test_flag_collision() -> None:
    with pytest.raises(SchemaError, match=r"--database-host is already taken") as e:

        class Colliding(Config):
            model_config = settings(app_name="myapp")

            database: Database = Database()
            database_host: str = ""

    assert "Colliding.database_host" in str(e.value)


def test_env_collision() -> None:
    with pytest.raises(SchemaError, match="MYAPP_DATABASE__HOST is already taken"):

        class Colliding(Config):
            model_config = settings(app_name="myapp")

            database: Database = Database()
            database__host: str = ""


def test_short_flag_collision() -> None:
    with pytest.raises(SchemaError, match=r"-p is already taken by 'port'"):

        class Colliding(Config):
            model_config = settings(app_name="myapp")

            port: int = Field(8080, short="p")
            protocol: str = Field("https", short="p")


def test_short_flag_cannot_claim_h() -> None:
    with pytest.raises(SchemaError, match="-h is reserved for --help"):

        class Claiming(Config):
            model_config = settings(app_name="myapp")

            host: str = Field("localhost", short="h")


def test_short_flag_must_be_one_character() -> None:
    with pytest.raises(SchemaError, match="single alphanumeric character"):

        class TooLong(Config):
            model_config = settings(app_name="myapp")

            port: int = Field(8080, short="po")


def test_short_flag_on_a_positional_is_meaningless() -> None:
    with pytest.raises(SchemaError, match="without a default"):

        class Positional(Config):
            model_config = settings(app_name="myapp")

            dataset: str = Field(short="d")


@pytest.mark.parametrize("name", ["help", "config_override", "show_config"])
def test_reserved_field_names(name: str) -> None:
    with pytest.raises(SchemaError, match=f"'{name}' is reserved"):
        define("Reserved", name, str, "")


def test_reserved_flag_via_an_override() -> None:
    with pytest.raises(SchemaError, match=r"the flag --help is reserved"):

        class Overriding(Config):
            model_config = settings(app_name="myapp")

            topic: str = Field("", flag="help")


def test_field_named_after_the_root_table() -> None:
    with pytest.raises(SchemaError, match=r"\[myapp\]"):

        class Shadowing(Config):
            model_config = settings(app_name="myapp")

            myapp: str = ""


def test_field_named_after_the_head_of_a_dotted_root_table() -> None:
    # [tool.myapp] puts 'tool' at the top level of a file, so a field of that
    # name is the ambiguous one -- 'myapp' never appears there at all.
    with pytest.raises(SchemaError, match=r"\[tool\.myapp\]"):

        class Shadowing(Config):
            model_config = settings(app_name="myapp", root_table="tool.myapp")

            tool: str = ""


def test_field_named_after_a_renamed_root_table_is_fine() -> None:
    class Renamed(Config):
        model_config = settings(app_name="myapp", root_table="tool.myapp")

        myapp: str = ""

    assert Renamed.__monoconf_spec__.by_path()["myapp"].flag == "--myapp"


def test_required_field_in_a_nested_model() -> None:
    class NeedsHost(BaseModel):
        host: str

    with pytest.raises(SchemaError, match="every field of a nested model needs"):

        class Nested(Config):
            model_config = settings(app_name="myapp")

            database: NeedsHost = NeedsHost(host="localhost")


def test_required_nested_model() -> None:
    with pytest.raises(SchemaError, match="a nested model needs a default"):

        class Required(Config):
            model_config = settings(app_name="myapp")

            database: Database


def test_required_list() -> None:
    with pytest.raises(SchemaError, match="a required list cannot be a positional"):

        class Required(Config):
            model_config = settings(app_name="myapp")

            paths: list[Path]


def test_required_set() -> None:
    with pytest.raises(SchemaError, match="a required set cannot be a positional"):

        class Required(Config):
            model_config = settings(app_name="myapp")

            paths: set[Path]


def test_optional_without_a_default() -> None:
    with pytest.raises(SchemaError, match=r"'proxy: str \| None = None'"):

        class Nullable(Config):
            model_config = settings(app_name="myapp")

            proxy: str | None


def test_nullable_nested_model() -> None:
    with pytest.raises(SchemaError, match=r"must not be\s+nullable"):

        class Nullable(Config):
            model_config = settings(app_name="myapp")

            database: Database | None = None


def test_nested_config_class() -> None:
    class Child(Config):
        model_config = settings(app_name="child")

        host: str = "localhost"

    with pytest.raises(SchemaError, match="must be a plain"):

        class Parent(Config):
            model_config = settings(app_name="myapp")

            child: Child = Child()


@pytest.mark.parametrize("name", ["no_cache", "cache_no"])
def test_negative_boolean_names(name: str) -> None:
    with pytest.raises(SchemaError, match="must not be named"):
        define("Negative", name, bool, False)


def test_nullable_boolean() -> None:
    with pytest.raises(SchemaError, match="presence-only"):

        class TriState(Config):
            model_config = settings(app_name="myapp")

            color: bool | None = None


def test_type_that_cannot_be_built_from_a_string() -> None:
    class Endpoint:
        def __init__(self, value: str) -> None:
            self.value = value

    with pytest.raises(SchemaError, match="cannot be constructed") as e:

        class Custom(Config):
            model_config = settings(app_name="myapp", arbitrary_types_allowed=True)

            endpoint: Endpoint = Endpoint("localhost")

    message = str(e.value)
    assert "Custom.endpoint" in message
    assert "BeforeValidator" in message  # the message says what to do about it


def test_strict_type_cannot_come_from_a_string() -> None:
    with pytest.raises(SchemaError, match="cannot be constructed"):

        class StrictInt(Config):
            model_config = settings(app_name="myapp")

            port: Annotated[int, Strict()] = 8080


def test_pydantic_dataclass_cannot_come_from_a_string() -> None:
    import pydantic.dataclasses

    @pydantic.dataclasses.dataclass
    class Point:
        x: int = 0

    with pytest.raises(SchemaError, match="cannot be constructed"):

        class WithPoint(Config):
            model_config = settings(app_name="myapp")

            point: Point = Point()


def test_unconstructible_item_type_of_a_repeated_field() -> None:
    with pytest.raises(SchemaError, match="cannot be constructed"):

        class StrictItems(Config):
            model_config = settings(app_name="myapp")

            ports: list[Annotated[int, Strict()]] = []


class Colour(StrEnum):
    RED = "red"


class Size(IntEnum):
    SMALL = 1


class Mixed(Enum):
    ONE = 1


def parse_endpoint(value: object) -> str:
    return str(value)


@pytest.mark.parametrize(
    "annotation",
    [
        str,
        int,
        float,
        bool,
        Decimal,
        Path,
        UUID,
        datetime,
        date,
        time,
        timedelta,
        IPv4Address,
        IPv6Address,
        re.Pattern,
        Colour,
        Size,
        Mixed,
        Literal["a", "b"],
        int | str,
        Annotated[str, BeforeValidator(parse_endpoint)],
    ],
)
def test_string_constructible_types_are_accepted(annotation: object) -> None:
    # The probe has to accept every type the docs promise, including the ones that
    # reject the probe string itself with a parsing error.
    # The default is irrelevant here: the probe reads the annotation.
    model = define("Accepted", "value", annotation)
    assert model.__monoconf_spec__.by_path()["value"].path == "value"


def test_a_well_formed_schema_raises_nothing() -> None:
    class Fine(Config):
        """Every rule respected."""

        model_config = settings(app_name="myapp", show_config=True)

        input_path: Path
        dataset: str

        output_dir: Path = Path("out/")
        verbose: bool = False
        color: bool = True
        retries: int = Field(3, short="r")
        tags: list[str] = []
        children: list[str] = Field(default_factory=list, flag="child")
        proxy: str | None = None
        database: Database = Database()

    spec = Fine.__monoconf_spec__
    assert [f.path for f in spec.positionals] == ["input_path", "dataset"]
    assert spec.by_path()["children"].flag == "--child"
