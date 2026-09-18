"""Deep merge of sparse layers, and the provenance that comes out of it.

Every source returns `(values, origins)`: the values it actually set, and where
each of them came from. `merge` folds those layers lowest priority first and
records the winning origin for each leaf as it wins, so "why is port 9090?" is
answerable for every field, always. No I/O lives in this module.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from monoconf.errors import MonoconfError

__all__ = [
    "Layer",
    "Origin",
    "Origins",
    "SourceKind",
    "Values",
    "leaves",
    "merge",
    "origins_for",
    "set_leaf",
]


class SourceKind(StrEnum):
    """Where a value came from, in precedence order."""

    DEFAULT = "default"
    SYSTEM_FILE = "system file"
    SYSTEM_DROPIN = "system drop-in"
    USER_FILE = "user file"
    USER_DROPIN = "user drop-in"
    OVERRIDE_FILE = "override file"
    ENV = "environment"
    CLI = "command line"


@dataclass(frozen=True, slots=True)
class Origin:
    """Where one resolved value came from."""

    source: SourceKind
    path: Path | None = None
    """The file, for a file source."""

    detail: str | None = None
    """The variable name, or the flag as typed."""

    def describe(self) -> str:
        """The shortest thing that answers "where did this come from?"."""
        if self.path is not None:
            return str(self.path)
        if self.detail is not None:
            return self.detail
        return str(self.source)


Values = dict[str, Any]
"""A sparse tree of the values one source set, nested as the schema is."""

Origins = dict[str, Origin]
"""One origin per leaf, keyed by dotted path."""

Layer = tuple[Values, Origins]


def leaves(values: Mapping[str, Any], prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Walk a value tree, yielding `(dotted path, value)` for every leaf.

    A `dict` is structure, never a value: monoconf rejects dict-typed fields at
    class-definition time precisely so that this distinction holds.
    """
    for key, value in values.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            yield from leaves(value, f"{path}.")
        else:
            yield path, value


def origins_for(values: Mapping[str, Any], origin: Origin) -> Origins:
    """Attribute every leaf of one source's values to the same origin."""
    return {path: origin for path, _ in leaves(values)}


def merge(layers: Iterable[Layer]) -> Layer:
    """Fold layers lowest priority first into one tree plus its provenance.

    Merging is per leaf, so a drop-in setting `database.port` leaves
    `database.host` from the base file intact. Lists are values, not structure, so
    a later layer's list replaces the earlier one wholesale.
    """
    values: Values = {}
    origins: Origins = {}
    for layer_values, layer_origins in layers:
        _fold(values, origins, layer_values, layer_origins, "")
    return values, origins


def _fold(
    values: Values,
    origins: Origins,
    incoming: Mapping[str, Any],
    incoming_origins: Mapping[str, Origin],
    prefix: str,
) -> None:
    for key, value in incoming.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            existing = values.get(key)
            child: Values = existing if isinstance(existing, dict) else {}
            _fold(child, origins, value, incoming_origins, f"{path}.")
            # A source that set nothing sets nothing: an empty branch never
            # materialises a key that no layer actually filled in.
            if child:
                values[key] = child
            continue
        values[key] = value
        origins[path] = _origin_of(incoming_origins, path)


def _origin_of(origins: Mapping[str, Origin], path: str) -> Origin:
    origin = origins.get(path)
    if origin is None:
        raise MonoconfError(
            f"a source returned a value for '{path}' without an origin. Every "
            "resolved value has to know where it came from; the source is at fault."
        )
    return origin


def set_leaf(values: Values, path: str, value: Any) -> None:
    """Set a dotted path in a value tree, creating the branches it needs.

    The inverse of `leaves`, for a source that knows dotted paths and has to hand
    back the nested shape the schema has. The path's branches must be branches --
    which the definition-time checks guarantee, a field being either a leaf or a
    model and never both.
    """
    head, _, rest = path.partition(".")
    if not rest:
        values[head] = value
        return
    set_leaf(values.setdefault(head, {}), rest, value)
