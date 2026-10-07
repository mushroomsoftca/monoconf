"""The TOML behind `--show-config`: the resolved configuration, with provenance.

There is no TOML writer in the dependency budget, and a configuration's values are
a small, known set of shapes, so monoconf emits it itself. The output is a valid
config file: strip nothing, feed it back, and the same values resolve. No I/O
lives in this module.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, time
from enum import Enum
from typing import Any

from monoconf.merge import Origin
from monoconf.schema import SECRET_TYPES, Spec

__all__ = ["dump"]

_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")

_ESCAPES = {
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
    '"': '\\"',
    "\\": "\\\\",
}


@dataclass(frozen=True, slots=True)
class _Line:
    text: str
    comment: str


def dump(
    prog: str,
    spec: Spec,
    values: Mapping[str, Any],
    origins: Mapping[str, Origin],
) -> str:
    """Render a validated model's values as TOML, one provenance comment per leaf.

    `values` is the model dumped in Python mode, so nested models are dicts and
    every leaf is the value the application will be handed. Positionals are
    written commented out: they are CLI-only, and a file setting one would only
    earn a warning when this output is read back.
    """
    positionals = {field.path for field in spec.positionals}
    secrets = {field.path for field in spec.fields if field.secret}
    out = [f"# {prog} resolved configuration"]
    for table, lines in _tables(values, (), origins, positionals, secrets):
        if table:
            out.extend(("", f"[{'.'.join(_key(part) for part in table)}]"))
        out.extend(_aligned(lines))
    return "\n".join(out) + "\n"


def _tables(
    values: Mapping[str, Any],
    table: tuple[str, ...],
    origins: Mapping[str, Origin],
    positionals: set[str],
    secrets: set[str],
) -> list[tuple[tuple[str, ...], list[_Line]]]:
    """Every table with lines of its own, scalars before subtables at each level.

    A dict is always a table: dict-typed fields are refused at class definition,
    so the only dicts in a dumped model are nested models.
    """
    lines: list[_Line] = []
    nested: list[tuple[tuple[str, ...], list[_Line]]] = []
    for name, value in values.items():
        if isinstance(value, dict):
            nested.extend(_tables(value, (*table, name), origins, positionals, secrets))
            continue
        path = ".".join((*table, name))
        lines.append(_leaf(name, path, value, origins, positionals, secrets))
    own = [(table, lines)] if lines else []
    return own + nested


def _leaf(
    name: str,
    path: str,
    value: Any,
    origins: Mapping[str, Origin],
    positionals: set[str],
    secrets: set[str],
) -> _Line:
    key = _key(name)
    rendered = _value(value)
    if path in positionals:
        if path not in origins:
            return _Line(f"# {key}", "positional, CLI-only, not given")
        return _Line(f"# {key} = {rendered}", "positional, CLI-only")
    origin = origins[path].describe()
    if path in secrets or isinstance(value, SECRET_TYPES):
        # Shown masked, and commented out: fed back as a live key, the mask would
        # replace the real secret with a row of asterisks. The field says so for
        # a list of secrets, whose value is a list rather than a secret.
        return _Line(f"# {key} = {rendered}", f"{origin}; secret, not shown")
    if rendered is None:
        # TOML has no null. The key stays visible, so the dump is still a complete
        # picture of the configuration, but reading it back leaves it unset.
        return _Line(f"# {key} = {_null_display(value)}", origin)
    return _Line(f"{key} = {rendered}", origin)


def _aligned(lines: Sequence[_Line]) -> list[str]:
    """Line up the provenance comments within one table."""
    if not lines:
        return []
    width = max(len(line.text) for line in lines)
    return [f"{line.text.ljust(width)}  # {line.comment}" for line in lines]


def _value(value: Any) -> str | None:
    """A leaf as a TOML value, or `None` for one TOML cannot express."""
    if value is None:
        return None
    if isinstance(value, Enum):
        return _value(value.value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _float(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, date | time):
        # Covers datetime, a subclass of date. A string, not a TOML datetime:
        # the field reads either, and a string is what the CLI and env would hand it.
        return _string(value.isoformat())
    if isinstance(value, set | frozenset):
        # Sorted, because a set has no order and the dump has to be stable.
        return _array(sorted(value, key=lambda item: _value(item) or ""))
    if isinstance(value, list | tuple):
        return _array(value)
    # The string-constructibility contract every field type signs: str() is the
    # form it can be built back from. For pydantic's secret types it is the mask.
    return _string(str(value))


def _array(items: Sequence[Any]) -> str | None:
    rendered = [_value(item) for item in items]
    if any(item is None for item in rendered):
        return None
    return f"[{', '.join(item for item in rendered if item is not None)}]"


def _float(value: float) -> str:
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    # repr() is Python's shortest round-tripping form, and every form it produces
    # (1.5, 1e+16, 1e-05) is also a TOML float.
    return repr(value)


def _string(value: str) -> str:
    escaped = "".join(_escape(char) for char in value)
    return f'"{escaped}"'


def _escape(char: str) -> str:
    if char in _ESCAPES:
        return _ESCAPES[char]
    if ord(char) < 0x20 or ord(char) == 0x7F:
        return f"\\u{ord(char):04X}"
    return char


def _key(name: str) -> str:
    return name if _BARE_KEY.fullmatch(name) else _string(name)


def _null_display(value: Any) -> str:
    """How an unrepresentable value reads in its comment: `null` for None."""
    if value is None:
        return "null"
    if isinstance(value, set | frozenset):
        return _null_display(sorted(value, key=_null_display))
    if isinstance(value, list | tuple):
        return f"[{', '.join(_null_display(item) for item in value)}]"
    return _value(value) or "null"
