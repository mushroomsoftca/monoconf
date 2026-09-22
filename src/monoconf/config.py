"""The `Config` base class and the resolver behind it.

A subclass is introspected once, at class-definition time: its `Spec` is derived
and its schema validated, so a malformed schema fails on import rather than at
`load()`. Resolution itself is a fold over the sources, in one fixed order, with
one validation pass at the end.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, ClassVar, Self

from pydantic import BaseModel, ValidationError

from monoconf.errors import (
    HelpRequested,
    MonoconfError,
    OutputRequested,
    ValidationFailed,
    render_validation_error,
)
from monoconf.merge import Layer, Origin, merge
from monoconf.schema import Spec, build_spec
from monoconf.settings import program_name, settings
from monoconf.sources import Context, cli, defaults, files
from monoconf.sources import env as env_source

__all__ = ["Config"]

_ORIGIN_ATTR = "__monoconf_origin__"


class Config(BaseModel):
    """Base class for a monoconf configuration model.

    A field without a default is required, positional, and settable only from the
    command line. A field with a default is optional everywhere. Instances are
    frozen; `settings(frozen=False)` opts out.
    """

    model_config = settings()

    if TYPE_CHECKING:
        __monoconf_spec__: ClassVar[Spec]

        # Declared as a read-only property for type checkers only. A plain
        # annotation would make pydantic's dataclass_transform treat it as a field
        # and demand it as a constructor argument; at runtime it is attached to the
        # resolved instance with object.__setattr__.
        @property
        def __monoconf_origin__(self) -> Mapping[str, Origin]: ...

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        # Runs after pydantic has finished building the model, so model_fields is
        # populated -- which __init_subclass__ cannot promise.
        super().__pydantic_init_subclass__(**kwargs)
        cls.__monoconf_spec__ = build_spec(cls)

    @classmethod
    def load(
        cls,
        argv: Sequence[str] | None = None,
        env: Mapping[str, str] | None = None,
    ) -> Self:
        """Resolve the configuration with CLI semantics.

        Bad input writes a message to stderr and raises `SystemExit(2)`; `--help`
        prints and exits 0. Use this in `main()`.
        """
        try:
            return cls.resolve(argv=argv, env=env)
        except OutputRequested as requested:
            _write(sys.stdout, requested.text)
            raise SystemExit(requested.exit_code) from None
        except MonoconfError as error:
            prog = program_name(cls.__monoconf_spec__.settings)
            _write(sys.stderr, f"{prog}: {error}\nTry '{prog} --help' for usage.")
            raise SystemExit(2) from None

    @classmethod
    def resolve(
        cls,
        argv: Sequence[str] | None = None,
        env: Mapping[str, str] | None = None,
    ) -> Self:
        """Resolve the configuration with library semantics.

        Raises `MonoconfError` subclasses rather than exiting, and reports `--help`
        as `HelpRequested` carrying the rendered text. Use this in tests and when
        embedding.

        `argv=None` means `sys.argv[1:]` and `env=None` means `os.environ`; an empty
        sequence or mapping means exactly that, not a default.
        """
        spec = cls.__monoconf_spec__
        command_line = tuple(sys.argv[1:] if argv is None else argv)
        # Pass one, before anything can complain about a missing positional, and
        # before the file layer can be collected: it is what says which files
        # there are.
        meta = cli.parse_meta(spec, command_line)
        ctx = Context(
            argv=command_line,
            env=os.environ if env is None else env,
            overrides=meta.config_override,
        )

        if meta.help_requested:
            raise HelpRequested(_help(spec, ctx))

        lower = merge(_lower_layers(spec, ctx))
        values, origins = merge([lower, cli.collect(spec, ctx)])
        try:
            resolved = cls.model_validate(values)
        except ValidationError as error:
            prog = program_name(spec.settings)
            raise ValidationFailed(
                render_validation_error(prog, origins, error)
            ) from error
        # The instance is frozen, and origins are not a field: this is the one way
        # to attach them, and it leaves model_dump, equality and repr untouched.
        object.__setattr__(resolved, _ORIGIN_ATTR, origins)
        return resolved

    def origin_of(self, path: str) -> Origin:
        """Where the value at a dotted field path came from."""
        origins: Mapping[str, Origin] | None = getattr(self, _ORIGIN_ATTR, None)
        if origins is None:
            raise MonoconfError(
                f"this {type(self).__name__} was constructed directly, so it has no "
                "provenance. Resolve one with load() or resolve() to ask where a "
                "value came from."
            )
        try:
            return origins[path]
        except KeyError:
            raise MonoconfError(
                f"{type(self).__name__} has no field '{path}'."
            ) from None


def _help(spec: Spec, ctx: Context) -> str:
    """The help text, showing the defaults the lower layers resolved.

    Guarded, because help is what an operator reaches for when something is
    already wrong: a file that cannot be parsed must not be able to withhold it.
    Where a source raises, the schema's own defaults stand in and the reason is
    printed beneath them, naming the file or the variable. Help always answers.
    """
    try:
        values, _origins = merge(_lower_layers(spec, ctx))
    except MonoconfError as error:
        prog = program_name(spec.settings)
        return (
            f"{cli.render_help(spec, {})}\n{prog}: {error}\n{prog}: the defaults "
            "shown above are the schema's own, since that could not be read."
        )
    return cli.render_help(spec, values)


def _lower_layers(spec: Spec, ctx: Context) -> list[Layer]:
    """Every layer below the command line, lowest priority first.

    The ordering of the chain lives in this one place and nowhere else. `files`
    covers layers 2 to 5, which it folds internally: they share every code path,
    so they are one source with an order of its own.
    """
    return [
        defaults.collect(spec, ctx),
        files.collect(spec, ctx),
        env_source.collect(spec, ctx),
    ]


def _write(stream: Any, text: str) -> None:
    stream.write(text if text.endswith("\n") else f"{text}\n")
