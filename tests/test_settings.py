"""`settings()` and the defaults it leaves to be derived."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from monoconf import Config, settings
from monoconf.settings import UNSET, infer_app_name, resolve_settings


def test_everything_derives_from_app_name() -> None:
    class Model(BaseModel):
        model_config = settings(app_name="myapp")

    resolved = resolve_settings(Model)
    assert resolved.app_name == "myapp"
    assert resolved.env_prefix == "MYAPP_"
    assert resolved.root_table == "myapp"
    assert resolved.env_enabled is True
    assert resolved.dropins is True
    assert resolved.config_override is False
    assert resolved.show_config is False
    assert resolved.strict_unknown is False
    assert resolved.prog is None
    assert resolved.epilog is None
    # Neither derived nor disabled: the platform decides, at resolution time.
    assert resolved.system_config is UNSET
    assert resolved.user_config is UNSET


def test_hyphenated_app_name_is_legal_everywhere_it_is_used() -> None:
    class Model(BaseModel):
        model_config = settings(app_name="my-app")

    resolved = resolve_settings(Model)
    assert resolved.env_prefix == "MY_APP_"  # MY-APP_ would not be a variable name
    assert resolved.root_table == "my-app"  # a legal TOML bare key


def test_empty_env_prefix_is_honoured_not_replaced() -> None:
    class Model(BaseModel):
        model_config = settings(app_name="myapp", env_prefix="")

    assert resolve_settings(Model).env_prefix == ""


def test_root_table_can_be_disabled() -> None:
    class Model(BaseModel):
        model_config = settings(app_name="myapp", root_table=None)

    assert resolve_settings(Model).root_table is None


def test_description_comes_from_the_docstring() -> None:
    class Model(BaseModel):
        """Serve the widget index.

        A second paragraph, folded into one line.
        """

        model_config = settings(app_name="myapp")

    assert resolve_settings(Model).description == (
        "Serve the widget index. A second paragraph, folded into one line."
    )


def test_description_is_not_inherited_from_a_documented_base() -> None:
    class Documented(BaseModel):
        """A base class with a docstring of its own."""

        model_config = settings(app_name="myapp")

    class Undocumented(Documented):
        pass

    assert resolve_settings(Undocumented).description == ""


def test_explicit_description_wins_over_the_docstring() -> None:
    class Model(BaseModel):
        """Ignored."""

        model_config = settings(app_name="myapp", description="Used.")

    assert resolve_settings(Model).description == "Used."


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (Path("/etc/myapp.toml"), (Path("/etc/myapp.toml"),)),
        ([Path("/a.toml"), Path("/b.toml")], (Path("/a.toml"), Path("/b.toml"))),
        (None, None),
    ],
)
def test_config_paths_normalise(given: object, expected: object) -> None:
    class Model(BaseModel):
        model_config = settings(app_name="myapp", system_config=given)  # type: ignore[arg-type]

    assert resolve_settings(Model).system_config == expected


def test_app_name_is_inferred_from_the_defining_module() -> None:
    class AppConfig(BaseModel):
        pass

    # Defined in this test module, so the root package name is the module name.
    assert infer_app_name(AppConfig) == "test_settings"


def test_app_name_falls_back_to_the_class_name_in_a_script() -> None:
    class AppConfig(BaseModel):
        pass

    class Config2(BaseModel):
        pass

    AppConfig.__module__ = "__main__"
    Config2.__module__ = "__main__"
    assert infer_app_name(AppConfig) == "app"
    assert infer_app_name(Config2) == "config2"


def test_models_are_frozen_by_default() -> None:
    class Frozen(Config):
        model_config = settings(app_name="myapp")

        port: int = 8080

    instance = Frozen()
    with pytest.raises(ValidationError, match="frozen"):
        instance.port = 9090


def test_frozen_can_be_declined() -> None:
    class Mutable(Config):
        model_config = settings(app_name="myapp", frozen=False)

        port: int = 8080

    instance = Mutable()
    instance.port = 9090
    assert instance.port == 9090


def test_pydantic_keys_pass_through() -> None:
    class Model(BaseModel):
        model_config = settings(app_name="myapp", str_strip_whitespace=True)

        name: str = ""

    assert Model(name="  padded  ").name == "padded"


def test_unset_is_readable_in_a_repr() -> None:
    assert repr(UNSET) == "UNSET"
