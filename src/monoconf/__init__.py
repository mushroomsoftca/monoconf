"""monoconf — one schema, one precedence order.

A pydantic v2 model produces a CLI parser, an environment reader and a layered
TOML loader. See PLAN.md for the design and its rationale.
"""

from __future__ import annotations

from monoconf.errors import (
    ConfigFileError,
    HelpRequested,
    IgnoredValue,
    MonoconfError,
    MonoconfWarning,
    OutputRequested,
    SchemaError,
    ShowConfigRequested,
    SourceError,
    UnknownConfigKey,
    UnreadableSource,
    ValidationFailed,
)

__all__ = [
    "ConfigFileError",
    "HelpRequested",
    "IgnoredValue",
    "MonoconfError",
    "MonoconfWarning",
    "OutputRequested",
    "SchemaError",
    "ShowConfigRequested",
    "SourceError",
    "UnknownConfigKey",
    "UnreadableSource",
    "ValidationFailed",
]
