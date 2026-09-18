"""Exceptions and warnings raised by monoconf.

`SchemaError` fires at class-definition time; everything else fires during
resolution. `load()` turns the resolution errors into a stderr message plus
`SystemExit(2)`, while `resolve()` lets them propagate.
"""

from __future__ import annotations

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


class MonoconfError(Exception):
    """Base class for every error monoconf raises."""


class SchemaError(MonoconfError):
    """A `Config` subclass is malformed. Raised at class-definition time."""


class ConfigFileError(MonoconfError):
    """A config file could not be read, parsed or interpreted."""


class SourceError(MonoconfError):
    """An environment variable or command-line value could not be read."""


class ValidationFailed(MonoconfError):
    """The merged configuration failed validation.

    Wraps pydantic's `ValidationError`, which stays available as `__cause__`.
    """


class OutputRequested(MonoconfError):
    """The command line asked for output instead of a config object.

    `resolve()` raises this; `load()` writes `text` to stdout and exits with
    `exit_code`.
    """

    def __init__(self, text: str, exit_code: int = 0) -> None:
        super().__init__(text)
        self.text = text
        self.exit_code = exit_code


class HelpRequested(OutputRequested):
    """`--help` or `-h` was given. Carries the rendered help text."""


class ShowConfigRequested(OutputRequested):
    """`--show-config` was given. Carries the rendered TOML."""


class MonoconfWarning(UserWarning):
    """Base class for every warning monoconf emits.

    Filterable as a group: `warnings.filterwarnings("error", category=MonoconfWarning)`.
    """


class UnknownConfigKey(MonoconfWarning):
    """A config file contains a key matching no field."""


class IgnoredValue(MonoconfWarning):
    """A file or environment variable set a required field, which is CLI-only."""


class UnreadableSource(MonoconfWarning):
    """A config path or drop-in directory exists but could not be read."""
