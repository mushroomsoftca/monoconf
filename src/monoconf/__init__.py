"""monoconf — one schema, one precedence order.

A pydantic v2 model produces a CLI parser, an environment reader and a layered
TOML loader. See PLAN.md for the design and its rationale.
"""

from __future__ import annotations

from monoconf.config import Config
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
from monoconf.fields import Field
from monoconf.merge import Origin, SourceKind
from monoconf.settings import settings

__all__ = [
    "Config",
    "ConfigFileError",
    "Field",
    "HelpRequested",
    "IgnoredValue",
    "MonoconfError",
    "MonoconfWarning",
    "Origin",
    "OutputRequested",
    "SchemaError",
    "ShowConfigRequested",
    "SourceError",
    "SourceKind",
    "UnknownConfigKey",
    "UnreadableSource",
    "ValidationFailed",
    "settings",
]
