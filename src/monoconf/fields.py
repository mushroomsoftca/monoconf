"""The `Field` wrapper and the name derivations every source shares.

`Field` adds three CLI-specific keys to pydantic's own. Everything else here
turns a field's dotted path and type into the names the CLI and environment use,
so that exactly one implementation of each derivation exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path, PurePath
from typing import Any
from uuid import UUID

from pydantic import Field as _pydantic_field
from pydantic.fields import FieldInfo

__all__ = [
    "CliMeta",
    "Field",
    "cli_meta",
    "env_name_for",
    "flag_for",
    "metavar_for",
    "metavar_from_name",
    "singularise",
]

_EXTRA_KEY = "monoconf"


@dataclass(frozen=True, slots=True)
class CliMeta:
    """The CLI-specific keys a maintainer passed to `Field`."""

    short: str | None = None
    metavar: str | None = None
    flag: str | None = None


def Field(
    default: Any = ...,
    *,
    short: str | None = None,
    metavar: str | None = None,
    flag: str | None = None,
    **pydantic_kwargs: Any,
) -> Any:
    """`pydantic.Field` plus `short`, `metavar` and `flag`.

    `short` is an opt-in single-character flag, never derived. `metavar` and `flag`
    override the derived placeholder and long flag name; `flag` exists for list
    fields whose plural does not singularise regularly.

    `description` is pydantic's own key and doubles as the CLI help text.
    """
    extra = {
        key: value
        for key, value in (("short", short), ("metavar", metavar), ("flag", flag))
        if value is not None
    }
    if extra:
        # json_schema_extra is the one place on FieldInfo that survives pydantic's
        # reconstruction of the field. The cost is a "monoconf" key in the model's
        # JSON schema, which is a fair trade for not needing a parallel registry.
        existing = pydantic_kwargs.pop("json_schema_extra", None)
        merged: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
        merged[_EXTRA_KEY] = extra
        pydantic_kwargs["json_schema_extra"] = merged
    return _pydantic_field(default, **pydantic_kwargs)


def cli_meta(info: FieldInfo) -> CliMeta:
    """Read back what `Field` stored, for a field declared either way."""
    extra = info.json_schema_extra
    if not isinstance(extra, dict):
        return CliMeta()
    stored = extra.get(_EXTRA_KEY)
    if not isinstance(stored, dict):
        return CliMeta()
    return CliMeta(
        short=_opt_str(stored.get("short")),
        metavar=_opt_str(stored.get("metavar")),
        flag=_opt_str(stored.get("flag")),
    )


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


_ES_ENDINGS = ("ses", "xes", "zes", "ches", "shes")
_S_EXCEPTIONS = ("ss", "us", "is")


def singularise(name: str) -> str:
    """Singularise a regular plural, leaving anything irregular alone.

    A list field takes a repeated singular flag: `tags` -> `--tag`. Irregular
    plurals keep the field name; `Field(flag=...)` is the way out.
    """
    if len(name) > 4 and name.endswith("ies"):
        return f"{name[:-3]}y"
    if name.endswith(_ES_ENDINGS):
        return name[:-2]
    if name.endswith("s") and not name.endswith(_S_EXCEPTIONS):
        return name[:-1]
    return name


def flag_for(path: str, *, repeated: bool = False, override: str | None = None) -> str:
    """Long flag for a dotted field path: `database.host` -> `--database-host`."""
    if override is not None:
        return f"--{override.lstrip('-')}"
    parts = path.split(".")
    if repeated:
        parts[-1] = singularise(parts[-1])
    return "--" + "-".join(part.replace("_", "-") for part in parts)


def env_name_for(path: str, prefix: str) -> str:
    """Environment variable for a dotted path, nested with a double underscore."""
    return prefix + "__".join(part.upper() for part in path.split("."))


# Exact matches only: datetime subclasses date, so a scan over types would have to
# care about order, and an annotation is virtually always one of these exactly.
_METAVARS: dict[Any, str] = {
    Path: "PATH",
    PurePath: "PATH",
    int: "N",
    float: "X",
    Decimal: "DECIMAL",
    UUID: "UUID",
    datetime: "DATETIME",
    date: "DATE",
    time: "TIME",
    timedelta: "DURATION",
}


def metavar_for(path: str, annotation: Any, override: str | None = None) -> str:
    """Help-text placeholder for a field, derived from its type where it can be.

    Falls back to the dotted path in UPPER_SNAKE, which keeps a nested field's
    placeholder unambiguous in the usage line.
    """
    if override is not None:
        return override
    derived = _METAVARS.get(annotation)
    if derived is not None:
        return derived
    # Enums and Literals fall through: their choices are listed in the help text,
    # so the placeholder stays plain.
    return _upper_snake(path)


def metavar_from_name(path: str, override: str | None = None) -> str:
    """Placeholder for a positional argument.

    A positional's placeholder is also the only name the operator sees for it, so
    it comes from the field name rather than the type: `INPUT_PATH`, not `PATH`.
    """
    return override if override is not None else _upper_snake(path)


def _upper_snake(path: str) -> str:
    return re.sub(r"[.\-]", "_", path).upper()
