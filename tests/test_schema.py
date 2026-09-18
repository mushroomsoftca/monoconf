"""Introspection of a representative model into a `Spec`."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel

from monoconf import Config, Field, SchemaError, settings
from monoconf.schema import FieldKind, FieldSpec, Spec, build_spec


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"


class DatabaseConfig(BaseModel):
    host: str = "localhost"
    port: int = 5432
    replicas: list[str] = []


class AppConfig(Config):
    """Serve the widget index."""

    model_config = settings(app_name="myapp")

    # No default -> required -> positional, CLI-only.
    input_path: Path
    dataset: str

    # Defaulted -> optional everywhere.
    output_dir: Path = Path("out/")
    log_level: LogLevel = LogLevel.INFO
    mode: Literal["fast", "slow"] = "fast"
    verbose: bool = False
    color: bool = True
    retries: int = Field(3, description="Retry count on transient failure.", short="r")
    tags: list[str] = []
    excluded_paths: set[Path] = set()
    proxy: str | None = None
    database: DatabaseConfig = DatabaseConfig()


@pytest.fixture(scope="module")
def spec() -> Spec:
    return AppConfig.__monoconf_spec__


def field(spec: Spec, path: str) -> FieldSpec:
    return spec.by_path()[path]


def test_spec_is_derived_at_class_definition(spec: Spec) -> None:
    assert spec.model is AppConfig
    assert spec.settings.app_name == "myapp"
    assert spec.settings.description == "Serve the widget index."


def test_leaves_are_in_declaration_order_with_nesting_expanded(spec: Spec) -> None:
    assert [f.path for f in spec.fields] == [
        "input_path",
        "dataset",
        "output_dir",
        "log_level",
        "mode",
        "verbose",
        "color",
        "retries",
        "tags",
        "excluded_paths",
        "proxy",
        "database.host",
        "database.port",
        "database.replicas",
    ]


def test_requiredness_splits_positionals_from_options(spec: Spec) -> None:
    assert [f.path for f in spec.positionals] == ["input_path", "dataset"]
    assert all(f.flag is None for f in spec.positionals)
    assert all(f.flag is not None for f in spec.options)
    assert len(spec.positionals) + len(spec.options) == len(spec.fields)


@pytest.mark.parametrize(
    ("path", "flag", "metavar"),
    [
        # Positionals show the field name; options show the type where it helps.
        ("input_path", None, "INPUT_PATH"),
        ("dataset", None, "DATASET"),
        ("output_dir", "--output-dir", "PATH"),
        ("log_level", "--log-level", "LOG_LEVEL"),
        ("mode", "--mode", "MODE"),
        ("retries", "--retries", "N"),
        ("proxy", "--proxy", "PROXY"),
        # Booleans are presence-only, so no placeholder at all.
        ("verbose", "--verbose", None),
        ("color", "--no-color", None),
        # Repeated flags are singular, and so are their placeholders.
        ("tags", "--tag", "TAG"),
        ("excluded_paths", "--excluded-path", "PATH"),
        ("database.host", "--database-host", "DATABASE_HOST"),
        ("database.port", "--database-port", "N"),
        ("database.replicas", "--database-replica", "DATABASE_REPLICA"),
    ],
)
def test_flags_and_metavars(
    spec: Spec, path: str, flag: str | None, metavar: str | None
) -> None:
    assert field(spec, path).flag == flag
    assert field(spec, path).metavar == metavar


@pytest.mark.parametrize(
    ("path", "env_name"),
    [
        ("output_dir", "MYAPP_OUTPUT_DIR"),
        ("database.host", "MYAPP_DATABASE__HOST"),
        # Derived even for a CLI-only field: a file or variable setting it warns,
        # which means the name has to be known.
        ("input_path", "MYAPP_INPUT_PATH"),
    ],
)
def test_env_names(spec: Spec, path: str, env_name: str) -> None:
    assert field(spec, path).env_name == env_name


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("dataset", FieldKind.SCALAR),
        ("verbose", FieldKind.BOOL),
        ("color", FieldKind.BOOL),
        ("tags", FieldKind.LIST),
        ("excluded_paths", FieldKind.SET),
        ("database.replicas", FieldKind.LIST),
    ],
)
def test_kinds(spec: Spec, path: str, kind: FieldKind) -> None:
    assert field(spec, path).kind is kind


def test_boolean_polarity_follows_the_default(spec: Spec) -> None:
    assert field(spec, "verbose").negated is False
    assert field(spec, "color").negated is True
    assert field(spec, "color").flag == "--no-color"


def test_choices_come_from_the_type(spec: Spec) -> None:
    assert field(spec, "log_level").choices == ("debug", "info", "warning")
    assert field(spec, "mode").choices == ("fast", "slow")
    assert field(spec, "dataset").choices is None


def test_defaults_and_descriptions(spec: Spec) -> None:
    assert field(spec, "retries").default == 3
    assert field(spec, "retries").description == "Retry count on transient failure."
    assert field(spec, "retries").short == "r"
    assert field(spec, "output_dir").default == Path("out/")
    assert field(spec, "tags").default == []
    assert field(spec, "database.host").default == "localhost"
    # A required field has no default to report.
    assert field(spec, "input_path").default is None
    assert field(spec, "input_path").required is True
    assert field(spec, "dataset").short is None


def test_nullability_is_recorded(spec: Spec) -> None:
    assert field(spec, "proxy").nullable is True
    assert field(spec, "proxy").base_type is str
    assert field(spec, "dataset").nullable is False


def test_repeated_fields_report_their_item_type(spec: Spec) -> None:
    assert field(spec, "tags").base_type is str
    assert field(spec, "excluded_paths").base_type is Path
    assert field(spec, "tags").repeated is True
    assert field(spec, "dataset").repeated is False


def test_nested_models_are_recorded(spec: Spec) -> None:
    assert [(n.path, n.model) for n in spec.nested] == [("database", DatabaseConfig)]
    assert spec.nested[0].required is False
    assert spec.nested[0].nullable is False


def test_annotated_metadata_survives_for_the_probe() -> None:
    from typing import Annotated

    from pydantic import BeforeValidator

    class Model(Config):
        model_config = settings(app_name="myapp")

        endpoint: Annotated[str, BeforeValidator(str.strip)] = "localhost"

    endpoint = build_spec(Model).by_path()["endpoint"]
    assert hasattr(endpoint.base_type, "__metadata__")
    assert endpoint.metavar == "ENDPOINT"


def test_dict_fields_are_rejected() -> None:
    with pytest.raises(SchemaError, match="dict fields have no command-line form"):

        class BadDict(Config):
            model_config = settings(app_name="myapp")

            labels: dict[str, str] = {}


def test_tuple_fields_are_rejected() -> None:
    with pytest.raises(SchemaError, match="tuple fields have no command-line form"):

        class BadTuple(Config):
            model_config = settings(app_name="myapp")

            extent: tuple[int, int] = (0, 0)


def test_frozenset_fields_are_rejected() -> None:
    with pytest.raises(SchemaError, match="frozenset fields") as caught:

        class BadFrozenset(Config):
            model_config = settings(app_name="myapp")

            names: frozenset[str] = frozenset()

    assert "BadFrozenset.names" in str(caught.value)


def test_list_of_models_is_rejected() -> None:
    with pytest.raises(SchemaError, match="a list of models"):

        class Bad(Config):
            model_config = settings(app_name="myapp")

            databases: list[DatabaseConfig] = []


def test_list_of_containers_is_rejected() -> None:
    with pytest.raises(SchemaError, match="a list of containers"):

        class Bad(Config):
            model_config = settings(app_name="myapp")

            pairs: list[tuple[int, int]] = []


def test_multi_member_optional_union_keeps_its_members() -> None:
    class Model(Config):
        model_config = settings(app_name="myapp")

        value: int | str | None = None
        plain_union: int | str = 0

    spec = build_spec(Model)
    value = spec.by_path()["value"]
    assert value.nullable is True
    assert value.base_type == int | str
    assert value.metavar == "VALUE"
    plain = spec.by_path()["plain_union"]
    assert plain.nullable is False
    assert plain.base_type == int | str


def test_annotated_item_type_of_a_repeated_field() -> None:
    from typing import Annotated

    from pydantic import StringConstraints

    class Model(Config):
        model_config = settings(app_name="myapp")

        tags: list[Annotated[str, StringConstraints(strip_whitespace=True)]] = []

    tags = build_spec(Model).by_path()["tags"]
    assert tags.flag == "--tag"
    assert tags.metavar == "TAG"  # derived from the item type, which is a str
    assert hasattr(tags.base_type, "__metadata__")


def test_nested_annotated_layers_are_stripped() -> None:
    from typing import Annotated

    from monoconf.schema import _strip_annotated

    assert _strip_annotated(Annotated[Annotated[int, "inner"], "outer"]) is int
