"""Layer 7: the environment.

Reads the variable for each field the schema declares -- never the other way
round, which is why a prefixed variable matching no field is ignored in silence:
the `MYAPP_*` namespace is not monoconf's alone, and a deployment that sets
`MYAPP_HOME` is not making a mistake monoconf can help with.
"""

from __future__ import annotations

import json
import warnings
from typing import Any

from monoconf.errors import IgnoredValue
from monoconf.merge import Layer, Origin, Origins, SourceKind, Values, set_leaf
from monoconf.schema import FieldKind, FieldSpec, Spec
from monoconf.sources import Context, Rejected, scalar_value

__all__ = ["collect"]

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})
_BOOL_FORMS = "1/0, true/false, yes/no or on/off"


def collect(spec: Spec, ctx: Context) -> Layer:
    """Collect every field whose variable is set in `ctx.env`."""
    if not spec.settings.env_enabled:
        return {}, {}
    values: Values = {}
    origins: Origins = {}
    for field in spec.fields:
        raw = ctx.env.get(field.env_name)
        if raw is None:
            continue
        if field.required:
            # The operator's real problem is that their setting does nothing.
            warnings.warn(
                f"{field.env_name} is set, but '{field.path}' has no default, which "
                f"makes it a positional argument and settable only on the command "
                f"line. The variable is ignored.",
                IgnoredValue,
                stacklevel=2,
            )
            continue
        set_leaf(values, field.path, _value(field, raw))
        origins[field.path] = Origin(SourceKind.ENV, detail=field.env_name)
    return values, origins


def _value(field: FieldSpec, raw: str) -> Any:
    if field.repeated:
        return _items(raw)
    if field.kind is FieldKind.BOOL:
        return _boolean(raw)
    # Everything else is handed over as the string it is, unstripped: whitespace
    # inside a value may well be the point. pydantic does the coercion, once.
    return scalar_value(field, raw)


def _items(raw: str) -> list[Any] | Rejected:
    """Read a repeated field: a JSON array, or a comma-separated list."""
    text = raw.strip()
    if text.startswith("["):
        try:
            parsed: list[Any] = json.loads(text)
        except json.JSONDecodeError as error:
            reason = f"{error.msg[:1].lower()}{error.msg[1:]} at column {error.colno}"
            return Rejected(
                raw,
                f"starts with '[' but is not a valid JSON array ({reason}); write "
                "a JSON array, or a comma-separated list",
            )
        return parsed
    if text == "":
        # An empty variable is the empty list, which is how a list is cleared.
        return []
    # Items are stripped, and an empty item is kept: dropping something the
    # operator typed would be a worse surprise than a list with an empty member.
    return [item.strip() for item in raw.split(",")]


def _boolean(raw: str) -> bool | Rejected:
    """Read a boolean, which is an ordinary value here rather than a bare flag.

    Parsed rather than left to pydantic, whose accepted spellings and error wording
    are not the ones monoconf documents.
    """
    text = raw.strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return Rejected(raw, f"is not a boolean; use {_BOOL_FORMS}")
