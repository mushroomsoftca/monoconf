"""`settings()`, the `SettingsConfigDict` it produces, and the resolved form.

`settings()` returns a plain `TypedDict` extending pydantic's `ConfigDict`, so
`model_config = settings(...)` stays a valid pydantic config. Everything in it is
optional: each key is either derivable from `app_name` or has a fixed default.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Unpack, cast

from pydantic import BaseModel, ConfigDict

__all__ = ["UNSET", "ConfigPaths", "Settings", "SettingsConfigDict", "settings"]


class _Unset:
    """A setting the maintainer did not mention, as distinct from one set to `None`."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Final = _Unset()

# A path layer is either derived from the platform (UNSET), disabled (None), or
# pinned to explicit paths. Tests pin it; that is what makes them hermetic.
ConfigPaths = tuple[Path, ...] | None | _Unset


class SettingsConfigDict(ConfigDict, total=False):
    """pydantic's `ConfigDict` plus monoconf's own keys."""

    app_name: str
    prog: str
    description: str
    epilog: str | None
    env_prefix: str
    env_enabled: bool
    root_table: str | None
    system_config: Path | Sequence[Path] | None
    user_config: Path | Sequence[Path] | None
    dropins: bool
    config_override: bool
    show_config: bool
    strict_unknown: bool


def settings(
    *,
    app_name: str | _Unset = UNSET,
    prog: str | _Unset = UNSET,
    description: str | _Unset = UNSET,
    epilog: str | _Unset | None = UNSET,
    env_prefix: str | _Unset = UNSET,
    env_enabled: bool | _Unset = UNSET,
    root_table: str | _Unset | None = UNSET,
    system_config: Path | Sequence[Path] | _Unset | None = UNSET,
    user_config: Path | Sequence[Path] | _Unset | None = UNSET,
    dropins: bool | _Unset = UNSET,
    config_override: bool | _Unset = UNSET,
    show_config: bool | _Unset = UNSET,
    strict_unknown: bool | _Unset = UNSET,
    **pydantic_config: Unpack[ConfigDict],
) -> SettingsConfigDict:
    """Build a `model_config` for a `Config` subclass.

    Every argument is optional. Anything omitted is derived from `app_name`, which
    is itself inferred from the defining module or class when omitted. pydantic's
    own config keys may be passed through alongside, so `settings(frozen=False)`
    opts out of the frozen default.
    """
    data: dict[str, Any] = {"frozen": True}
    data.update(pydantic_config)
    supplied = {
        "app_name": app_name,
        "prog": prog,
        "description": description,
        "epilog": epilog,
        "env_prefix": env_prefix,
        "env_enabled": env_enabled,
        "root_table": root_table,
        "system_config": system_config,
        "user_config": user_config,
        "dropins": dropins,
        "config_override": config_override,
        "show_config": show_config,
        "strict_unknown": strict_unknown,
    }
    data.update({k: v for k, v in supplied.items() if not isinstance(v, _Unset)})
    # The keys are exactly SettingsConfigDict's; a TypedDict cannot be built from
    # a dict literal of computed keys, so the cast stands in for that.
    return cast(SettingsConfigDict, data)


@dataclass(frozen=True, slots=True)
class Settings:
    """`settings()` with every default filled in, as the sources see it."""

    app_name: str
    prog: str | None
    description: str
    epilog: str | None
    env_prefix: str
    env_enabled: bool
    root_table: str | None
    system_config: ConfigPaths
    user_config: ConfigPaths
    dropins: bool
    config_override: bool
    show_config: bool
    strict_unknown: bool


_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def snake_case(name: str) -> str:
    return _CAMEL_BOUNDARY.sub("_", name).lower()


def infer_app_name(model: type[BaseModel]) -> str:
    """Derive `app_name` from where the class was defined.

    The root package name is the best available guess at what the application is
    called. A class defined in a script has no package to speak of, so its own
    name stands in, with the conventional `Config` suffix dropped.
    """
    root = model.__module__.split(".", 1)[0]
    if root and root != "__main__":
        return root
    name = snake_case(model.__name__)
    return name.removesuffix("_config") or name


def _paths(value: Path | Sequence[Path] | _Unset | None) -> ConfigPaths:
    if isinstance(value, _Unset) or value is None:
        return value
    if isinstance(value, Path):
        return (value,)
    return tuple(value)


def resolve_settings(model: type[BaseModel]) -> Settings:
    """Read a model's `model_config` and fill in everything it left out."""
    raw = cast(dict[str, Any], model.model_config)
    app_name = raw.get("app_name") or infer_app_name(model)
    # __doc__ is read off the class itself: a subclass without a docstring must
    # not inherit its parent's as a help header.
    own_doc = model.__dict__.get("__doc__")
    description = raw.get("description")
    if description is None:
        description = " ".join(own_doc.split()) if own_doc else ""
    env_prefix = raw.get("env_prefix")
    if env_prefix is None:
        env_prefix = f"{app_name.upper().replace('-', '_')}_"
    return Settings(
        app_name=app_name,
        prog=raw.get("prog"),
        description=description,
        epilog=raw.get("epilog"),
        env_prefix=env_prefix,
        env_enabled=raw.get("env_enabled", True),
        root_table=raw.get("root_table", app_name),
        system_config=_paths(raw.get("system_config", UNSET)),
        user_config=_paths(raw.get("user_config", UNSET)),
        dropins=raw.get("dropins", True),
        config_override=raw.get("config_override", False),
        show_config=raw.get("show_config", False),
        strict_unknown=raw.get("strict_unknown", False),
    )
