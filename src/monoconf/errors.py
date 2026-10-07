"""Exceptions and warnings raised by monoconf.

`SchemaError` fires at class-definition time; everything else fires during
resolution. `load()` turns the resolution errors into a stderr message plus
`SystemExit(2)`, while `resolve()` lets them propagate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

if TYPE_CHECKING:  # merge.py and schema.py import this module, so these cannot be
    from collections.abc import Mapping, Sequence

    from pydantic_core import ErrorDetails

    from monoconf.merge import Origin
    from monoconf.schema import FieldSpec, Spec
    from monoconf.sources import Rejected

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
    "render_rejected",
    "render_validation_error",
]


class MonoconfError(Exception):
    """Base class for every error monoconf raises."""


class SchemaError(MonoconfError):
    """A `Config` subclass is malformed. Raised at class-definition time."""


class ConfigFileError(MonoconfError):
    """A config file could not be read, parsed or interpreted."""


class SourceError(MonoconfError):
    """The command line could not be parsed: an unknown flag, a missing value.

    A bad value, from the command line or anywhere else, is a `ValidationFailed`.
    """


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


def render_validation_error(
    prog: str,
    spec: Spec,
    origins: Mapping[str, Origin],
    error: ValidationError | None,
    rejected: Mapping[str, tuple[Rejected, Origin]],
) -> str:
    """Rewrite pydantic's validation errors for whoever has to act on them.

    Pydantic's own output is addressed to the author of the model. The operator
    reading this never saw it, and needs to know which setting is wrong and which
    file or variable it came from. One block per error; never only the first.

    `rejected` are the values a source could not read at all, keyed by path. They
    are reported in the same list, in declaration order with the rest: to the
    operator they are bad settings like any other.
    """
    # No "try --help" line here: that is advice for someone at a command line, and
    # load() is the only caller that knows it is talking to one.
    fields = spec.by_path()
    blocks: list[tuple[str, str, str]] = [
        (
            path,
            f"{path}: {render_rejected(fields.get(path), value)}",
            f"from {origin.describe()}",
        )
        for path, (value, origin) in rejected.items()
    ]
    for detail in error.errors() if error is not None else ():
        loc = tuple(detail["loc"])
        field_path, shown = _locate(loc, fields)
        problem = _problem(detail, fields.get(field_path))
        provenance = _provenance(detail, loc, origins, shown)
        blocks.append((field_path, f"{shown or '(root)'}: {problem}", provenance))
    order = [field.path for field in spec.fields]
    blocks.sort(key=lambda block: _position(block[0], order))
    lines = [f"{prog}: invalid configuration", ""]
    for _path, head, provenance in blocks:
        lines.extend((f"  {head}", f"    {provenance}", ""))
    return "\n".join(lines).rstrip()


def render_rejected(field: FieldSpec | None, rejected: Rejected) -> str:
    """A value a source could not read, said as a problem with that value."""
    shown = "the secret" if field is not None and field.secret else _shown(rejected.raw)
    return f"{shown} {rejected.problem}"


def _position(path: str, order: Sequence[str]) -> int:
    """Where a path's first field is declared; a model-wide error goes last."""
    for index, field_path in enumerate(order):
        if field_path == path or field_path.startswith(f"{path}."):
            return index
    return len(order)


def _locate(
    loc: tuple[int | str, ...], fields: Mapping[str, FieldSpec]
) -> tuple[str, str]:
    """The field an error belongs to, and the path to show the operator.

    Past a leaf field, pydantic's location carries segments of its own -- the
    member of a union it tried, a validator's name -- which mean nothing to the
    person who wrote the file. List indices are kept, as `tags[1]`.
    """
    parts: list[str] = []
    shown = ""
    for part in loc:
        field_path = ".".join(parts)
        if isinstance(part, int):
            shown += f"[{part}]"
            continue
        if field_path in fields:
            continue
        parts.append(part)
        shown = f"{shown}.{part}" if shown else part
    return ".".join(parts), shown


# Pydantic's error types, as the operator would say them. The value itself is
# named in front of each: "'nine thousand' is not a valid integer".
_PROBLEMS: dict[str, str] = {
    "int_parsing": "is not a valid integer",
    "int_type": "is not a valid integer",
    "int_from_float": "is not a whole number",
    "float_parsing": "is not a valid number",
    "float_type": "is not a valid number",
    "decimal_parsing": "is not a valid decimal number",
    "decimal_type": "is not a valid decimal number",
    "bool_parsing": "is not a valid boolean; use true or false",
    "bool_type": "is not a valid boolean; use true or false",
    "string_type": "is not a string; in a config file, quote it",
    "list_type": "is not a list; in a config file, write it as an array",
    "set_type": "is not a list; in a config file, write it as an array",
    "model_type": "is not a table of settings",
    "model_attributes_type": "is not a table of settings",
    "dict_type": "is not a table of settings",
    "uuid_parsing": "is not a valid UUID",
    "uuid_type": "is not a valid UUID",
    "date_parsing": "is not a valid date; use YYYY-MM-DD",
    "date_from_datetime_parsing": "is not a valid date; use YYYY-MM-DD",
    "date_type": "is not a valid date; use YYYY-MM-DD",
    "datetime_parsing": "is not a valid date and time; use YYYY-MM-DDTHH:MM:SS",
    "datetime_from_date_parsing": (
        "is not a valid date and time; use YYYY-MM-DDTHH:MM:SS"
    ),
    "datetime_type": "is not a valid date and time; use YYYY-MM-DDTHH:MM:SS",
    "time_parsing": "is not a valid time; use HH:MM:SS",
    "time_type": "is not a valid time; use HH:MM:SS",
    "time_delta_parsing": "is not a valid duration",
    "time_delta_type": "is not a valid duration",
    "url_parsing": "is not a valid URL",
    "url_type": "is not a valid URL",
}

_CHOICE_ERRORS = frozenset({"enum", "literal_error"})

# How much of a value to quote back. A whole table dumped into one line of an
# error message hides the message.
_MAX_SHOWN = 60


def _problem(detail: ErrorDetails, field: FieldSpec | None) -> str:
    kind = detail["type"]
    message = detail["msg"]
    if kind == "missing":
        return "is required"
    validator = kind in ("value_error", "assertion_error")
    if validator:
        # A validator's own words, without pydantic's "Value error, " label.
        _, _, message = message.partition(", ")
        if field is None:
            # A model-level check: the input is a whole table, and quoting it
            # back would bury the reason.
            return message
    # A rejected secret is usually a near miss of the real one: never quote it.
    shown = (
        "the secret" if field is not None and field.secret else _shown(detail["input"])
    )
    if kind in _CHOICE_ERRORS:
        choices = None if field is None else field.choices
        if choices is None:
            return f"{shown} is not one of {detail.get('ctx', {}).get('expected')}"
        return f"{shown} is not one of: {', '.join(choices)}"
    if kind in _PROBLEMS:
        return f"{shown} {_PROBLEMS[kind]}"
    if validator:
        return f"{shown} was rejected: {message}"
    head, found, rest = message.partition("Input should be ")
    if found and not head:
        return f"{shown} should be {rest}"
    return f"{shown} was rejected: {message[:1].lower()}{message[1:]}"


def _shown(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= _MAX_SHOWN else f"{text[: _MAX_SHOWN - 3]}..."


def _provenance(
    detail: ErrorDetails,
    loc: tuple[int | str, ...],
    origins: Mapping[str, Origin],
    shown: str,
) -> str:
    """Where the offending value came from: the longest located prefix's origin.

    A list is a value one layer set whole, so an error at `tags.1` came from
    wherever `tags` did.
    """
    parts = [str(part) for part in loc]
    for end in range(len(parts), 0, -1):
        origin = origins.get(".".join(parts[:end]))
        if origin is not None:
            return f"from {origin.describe()}"
    if detail["type"] == "missing":
        return "no value from any source"
    subject = f"'{shown}'" if shown else "the configuration"
    return f"from a check on {subject} as a whole"
