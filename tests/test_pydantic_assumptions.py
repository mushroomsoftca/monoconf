"""Assumptions about pydantic that monoconf's public API depends on.

`settings()` returns a `ConfigDict` carrying monoconf's own keys, and `Field()`
carries CLI metadata through to the model's `FieldInfo`. Both rely on pydantic
behaviour that is not explicitly guaranteed, so they are pinned here: if a
pydantic release breaks one, this file fails rather than the whole suite.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def test_model_config_tolerates_unknown_keys() -> None:
    class Model(BaseModel):
        model_config = ConfigDict(frozen=True, app_name="myapp")  # type: ignore[typeddict-unknown-key]

    assert Model.model_config["frozen"] is True
    assert Model.model_config["app_name"] == "myapp"  # type: ignore[typeddict-item]


def test_json_schema_extra_survives_field_info_reconstruction() -> None:
    class Model(BaseModel):
        retries: int = Field(
            3,
            description="Retry count.",
            json_schema_extra={"monoconf": {"short": "r", "metavar": "N"}},
        )

    info = Model.model_fields["retries"]
    assert info.description == "Retry count."
    extra = info.json_schema_extra
    assert isinstance(extra, dict)
    monoconf_extra: Any = extra["monoconf"]
    assert monoconf_extra == {"short": "r", "metavar": "N"}


def test_frozen_models_allow_private_state_via_object_setattr() -> None:
    # __monoconf_origin__ has to be attached to a frozen instance.
    class Model(BaseModel):
        model_config = ConfigDict(frozen=True)

        port: int = 8080

    instance = Model()
    object.__setattr__(instance, "__monoconf_origin__", {"port": "default"})
    assert getattr(instance, "__monoconf_origin__") == {"port": "default"}  # noqa: B009
