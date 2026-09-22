"""Layer 8: the command line, and the help output.

Parsing runs in two passes. Pass one reads only the meta-arguments, so `--help` is
answered before anything can complain about a missing positional. Pass two is the
full parser, built after the lower layers have resolved so that the help text can
show the default a flag would actually fall back to.

argparse converts nothing here -- every value arrives as a string and pydantic
does all of the coercion, once, on the merged result.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, NoReturn

from monoconf.errors import SourceError
from monoconf.merge import Layer, Origin, Origins, SourceKind, Values, set_leaf
from monoconf.schema import FieldKind, FieldSpec, Spec
from monoconf.settings import Settings, program_name
from monoconf.sources import Context, scalar_value

__all__ = ["MetaArgs", "MonoconfHelpFormatter", "collect", "parse_meta", "render_help"]


class _Unset:
    """The argparse default for every optional argument.

    Load-bearing: it is the only way to tell "the operator did not pass this flag"
    from "the operator passed the value that happens to equal the default". If a
    schema default ever reached argparse, an explicit `--port 8080` would be
    indistinguishable from an absent flag and would lose to a file setting 9090.
    """

    __slots__ = ()


_SENTINEL: Final = _Unset()

_HELP_DEST = "_monoconf_help"
_OVERRIDE_DEST = "_monoconf_config_override"
_USED_DEST = "_monoconf_used"

# Help output is part of the product: a maintainer's README capture must not depend
# on the width of the terminal that produced it.
_HELP_WIDTH = 88


@dataclass(frozen=True, slots=True)
class MetaArgs:
    """What pass one found."""

    help_requested: bool = False
    config_override: tuple[str, ...] = ()


class _Parser(argparse.ArgumentParser):
    """An `ArgumentParser` that raises instead of exiting.

    `resolve()` must not exit a process it does not own. `load()` is the only thing
    that decides to exit, and it does so on a `MonoconfError`.
    """

    def error(self, message: str) -> NoReturn:
        raise SourceError(message)

    def exit(  # pragma: no cover - unreachable, and a silent process kill if not
        self, status: int = 0, message: str | None = None
    ) -> NoReturn:
        # Nothing here exits on its own: add_help is False and no action calls exit.
        # The override stands so that a future one cannot kill the process quietly.
        raise SourceError(message or f"argument parsing failed ({status})")


class _Records(argparse.Action):
    """Base for monoconf's actions, remembering the flag as the operator typed it.

    Provenance says `-p` when that is what was typed, not the canonical `--port`.
    """

    def _record(self, namespace: argparse.Namespace, option_string: str | None) -> None:
        used: dict[str, str] | None = getattr(namespace, _USED_DEST, None)
        if used is None:
            used = {}
            setattr(namespace, _USED_DEST, used)
        used[self.dest] = option_string or str(self.metavar or self.dest)


class _Store(_Records):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        setattr(namespace, self.dest, values)
        self._record(namespace, option_string)


class _StoreConst(_Records):
    """Presence-only, for booleans: `--verbose` or `--no-color`, never a value."""

    def __init__(self, option_strings: Sequence[str], dest: str, **kwargs: Any) -> None:
        super().__init__(list(option_strings), dest, nargs=0, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        setattr(namespace, self.dest, self.const)
        self._record(namespace, option_string)


class _Append(_Records):
    """Repeated flag, starting from the sentinel.

    argparse's own append action copies its default and appends to the copy, which
    the sentinel cannot support -- and the sentinel is not negotiable.
    """

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        current = getattr(namespace, self.dest, _SENTINEL)
        items: list[Any] = [] if isinstance(current, _Unset) else list(current)
        items.append(values)
        setattr(namespace, self.dest, items)
        self._record(namespace, option_string)


class MonoconfHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Standard argparse layout, with the description and epilog left as written."""

    def __init__(self, prog: str) -> None:
        super().__init__(prog, max_help_position=24, width=_HELP_WIDTH)


def parse_meta(spec: Spec, argv: Sequence[str]) -> MetaArgs:
    """Pass one: the meta-arguments, and nothing else.

    `--help` has to be seen before the full parser runs, or a bare `myapp --help`
    fails on the missing positionals instead of printing help.
    """
    parser = _meta_parser(spec.settings)
    namespace, _unknown = parser.parse_known_args(list(argv))
    return MetaArgs(
        help_requested=bool(getattr(namespace, _HELP_DEST)),
        config_override=tuple(getattr(namespace, _OVERRIDE_DEST, None) or ()),
    )


def collect(spec: Spec, ctx: Context) -> Layer:
    """Pass two: the full parser, and the values it found."""
    parser = build_parser(spec, {})
    namespace = parser.parse_args(list(ctx.argv))
    values: Values = {}
    origins: Origins = {}
    used: dict[str, str] = getattr(namespace, _USED_DEST, {})
    for field in spec.fields:
        dest = dest_for(field.path)
        raw = getattr(namespace, dest, _SENTINEL)
        if isinstance(raw, _Unset):
            continue
        set_leaf(values, field.path, _value(field, raw))
        detail = used.get(dest, field.flag or field.metavar)
        origins[field.path] = Origin(SourceKind.CLI, detail=detail)
    return values, origins


def render_help(spec: Spec, resolved: Values) -> str:
    """The help text, with the defaults the lower layers actually resolved.

    The resolved values are handed in rather than collected here: a source reads
    its own inputs only, and what the layers below settled on is the resolver's to
    know.
    """
    return build_parser(spec, resolved).format_help()


def build_parser(spec: Spec, resolved: Values) -> argparse.ArgumentParser:
    """Build the full parser. `resolved` only ever reaches the help text."""
    settings = spec.settings
    parser = _Parser(
        prog=program_name(settings),
        description=settings.description or None,
        epilog=settings.epilog,
        add_help=False,
        allow_abbrev=False,
        formatter_class=MonoconfHelpFormatter,
    )
    parser.add_argument(
        "-h",
        "--help",
        action="store_true",
        dest=_HELP_DEST,
        help="show this help message and exit",
    )
    _add_override(parser, settings)
    for field in spec.positionals:
        parser.add_argument(
            dest_for(field.path),
            metavar=field.metavar,
            action=_Store,
            help=_help_text(field, resolved),
        )
    for field in spec.options:
        _add_option(parser, field, resolved)
    return parser


def dest_for(path: str) -> str:
    """argparse destination for a dotted path: `database.host` -> `database__host`.

    Two fields cannot collide here: the same transformation uppercased is the
    environment variable name, and that collision is a definition-time error.
    """
    return path.replace(".", "__")


def _meta_parser(settings: Settings) -> _Parser:
    parser = _Parser(prog=program_name(settings), add_help=False, allow_abbrev=False)
    parser.add_argument("-h", "--help", action="store_true", dest=_HELP_DEST)
    _add_override(parser, settings)
    return parser


def _add_override(parser: argparse.ArgumentParser, settings: Settings) -> None:
    """Declare `--config-override`, in both passes.

    Pass one needs it because it decides which files exist; pass two needs it
    because an argument the full parser has never heard of is an error, and
    because this is where help output comes from.
    """
    if not settings.config_override:
        return
    parser.add_argument(
        "--config-override",
        action="append",
        dest=_OVERRIDE_DEST,
        default=[],
        metavar="PATH",
        help=(
            "Read this file as well, above every discovered file and below the "
            "environment. Repeatable; the last one wins."
        ),
    )


def _add_option(
    parser: argparse.ArgumentParser, field: FieldSpec, resolved: Values
) -> None:
    flag = field.flag
    # spec.options is every field with a default, and every one of those has a flag.
    assert flag is not None
    flags = [flag] if field.short is None else [f"-{field.short}", flag]
    shared: dict[str, Any] = {
        "dest": dest_for(field.path),
        "default": _SENTINEL,
        "help": _help_text(field, resolved),
    }
    if field.kind is FieldKind.BOOL:
        # `--verbose` sets True; `--no-color` sets False. Only one form is generated.
        parser.add_argument(
            *flags, action=_StoreConst, const=not field.negated, **shared
        )
    elif field.repeated:
        parser.add_argument(*flags, action=_Append, metavar=field.metavar, **shared)
    else:
        parser.add_argument(*flags, action=_Store, metavar=field.metavar, **shared)


def _value(field: FieldSpec, raw: Any) -> Any:
    """Apply the empty-string rule to what argparse handed back."""
    if field.repeated:
        # One empty value is how a list is cleared from the command line.
        return [] if raw == [""] else raw
    if field.kind is FieldKind.BOOL:
        return raw
    return scalar_value(field, raw)


def _help_text(field: FieldSpec, resolved: Values) -> str:
    parts: list[str] = []
    if field.description:
        parts.append(field.description)
    if field.choices:
        parts.append(f"One of: {', '.join(field.choices)}.")
    if field.repeated:
        parts.append("Repeatable.")
    if not field.required and field.kind is not FieldKind.BOOL:
        # A boolean needs no bracket: which flag exists says what the default is.
        parts.append(f"[{_display(_resolved(resolved, field))}]")
    return " ".join(parts)


def _resolved(resolved: Values, field: FieldSpec) -> Any:
    """The value this flag would fall back to, per the layers resolved so far."""
    current: Any = resolved
    for part in field.path.split("."):
        if not isinstance(current, dict) or part not in current:
            return field.default
        current = current[part]
    return current


def _display(value: Any) -> str:
    """Render a resolved default for the help text."""
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, set | frozenset):
        # Sorted, because a set has no order and help output has to be stable.
        return ", ".join(_display(item) for item in sorted(value, key=str)) or "none"
    if isinstance(value, list | tuple):
        return ", ".join(_display(item) for item in value) or "none"
    if value == "":
        return '""'
    return str(value)
