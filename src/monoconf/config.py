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
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, ClassVar, Self, TypeVar, cast
from weakref import WeakKeyDictionary

from pydantic import BaseModel, ValidationError, create_model

from monoconf import toml
from monoconf.errors import (
    HelpRequested,
    MonoconfError,
    OutputRequested,
    ShowConfigRequested,
    ValidationFailed,
    render_rejected,
    render_validation_error,
)
from monoconf.merge import Layer, Origin, Values, leaves, merge, set_leaf
from monoconf.schema import Spec, build_spec
from monoconf.settings import program_name, settings
from monoconf.sources import Context, Rejected, cli, defaults, files
from monoconf.sources import env as env_source

__all__ = ["Config"]

_ORIGIN_ATTR = "__monoconf_origin__"

# Set while monoconf derives a model of its own from an application's schema. The
# derived model is not a schema anyone wrote, so it is neither introspected nor
# checked: it borrows the spec of the class it came from.
_DERIVING: ContextVar[bool] = ContextVar("monoconf_deriving", default=False)


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
        if _DERIVING.get():
            return
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
            hint = f"Try '{prog} --help' for usage."
            if isinstance(error, ValidationFailed):
                # Already a titled block naming the program; it gets a blank line
                # before the hint rather than a second "myapp:" in front of it.
                _write(sys.stderr, f"{error}\n\n{hint}")
            else:
                _write(sys.stderr, f"{prog}: {error}\n{hint}")
            raise SystemExit(2) from None

    @classmethod
    def resolve(
        cls,
        argv: Sequence[str] | None = None,
        env: Mapping[str, str] | None = None,
    ) -> Self:
        """Resolve the configuration with library semantics.

        Raises `MonoconfError` subclasses rather than exiting, and reports `--help`
        and `--show-config` as `HelpRequested` and `ShowConfigRequested`, carrying
        the rendered text. Use this in tests and when embedding.

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
            show_config=meta.show_config,
        )

        if meta.help_requested:
            raise HelpRequested(_help(spec, ctx))

        layers, rejected = _accepted(_lower_layers(spec, ctx))
        values, origins = merge([merge(layers), cli.collect(spec, ctx)])
        model = _without_positionals(cls) if meta.show_config else cls
        prog = program_name(spec.settings)
        try:
            resolved = model.model_validate(values)
        except ValidationError as error:
            raise ValidationFailed(
                render_validation_error(prog, spec, origins, error, rejected)
            ) from error
        if rejected:
            # Everything that could be read was valid; what could not be read is
            # still the operator's to fix, and is reported the same way.
            raise ValidationFailed(
                render_validation_error(prog, spec, origins, None, rejected)
            )
        if meta.show_config:
            raise ShowConfigRequested(
                toml.dump(prog, spec, resolved.model_dump(), origins)
            )
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


_C = TypeVar("_C", bound=Config)

# Weakly held: a schema class defined and dropped, as tests do by the hundred,
# should not be kept alive by a copy of itself.
_derived: WeakKeyDictionary[type[Config], type[Config]] = WeakKeyDictionary()


def _without_positionals(cls: type[_C]) -> type[_C]:
    """The schema with every positional optional, for `--show-config`.

    Derived from the application's own class, so its validators still run and a
    value that would fail a real run fails this one too. Positionals given on the
    command line validate exactly as they would have; only an absent one is let
    through, as None. Built once per class.
    """
    cached = _derived.get(cls)
    if cached is not None:
        # A subclass of cls, since that is how it was built below.
        return cast(type[_C], cached)
    positionals: dict[str, Any] = {
        field.name: (field.annotation | None, None)
        for field in cls.__monoconf_spec__.positionals
    }
    token = _DERIVING.set(True)
    try:
        derived = create_model(
            cls.__name__, __base__=cls, __module__=cls.__module__, **positionals
        )
    finally:
        _DERIVING.reset(token)
    _derived[cls] = derived
    return derived


def _help(spec: Spec, ctx: Context) -> str:
    """The help text, showing the defaults the lower layers resolved.

    Guarded, because help is what an operator reaches for when something is
    already wrong: a file that cannot be parsed must not be able to withhold it.
    Where a source raises, the schema's own defaults stand in and the reason is
    printed beneath them, naming the file or the variable. Help always answers.
    """
    prog = program_name(spec.settings)
    try:
        layers, rejected = _accepted(_lower_layers(spec, ctx))
        values, _origins = merge(layers)
    except MonoconfError as error:
        return (
            f"{cli.render_help(spec, {})}\n{prog}: {error}\n{prog}: the defaults "
            "shown above are the schema's own, since that could not be read."
        )
    text = cli.render_help(spec, values)
    if not rejected:
        return text
    fields = spec.by_path()
    notes = [
        f"{prog}: {origin.describe()} is left out of the defaults shown above: "
        f"{render_rejected(fields.get(path), value)}"
        for path, (value, origin) in rejected.items()
    ]
    return f"{text}\n" + "\n".join(notes)


Rejections = dict[str, tuple[Rejected, Origin]]


def _accepted(layers: list[Layer]) -> tuple[list[Layer], Rejections]:
    """The layers without the values their sources rejected, and those values.

    A rejected value comes out before the merge, so pydantic never sees one and the
    layers beneath decide its field. It is kept, with its origin, to be reported
    with the validation errors: one run names every problem, not only the first.
    """
    kept: list[Layer] = []
    rejected: Rejections = {}
    for values, origins in layers:
        bad = {
            path: value for path, value in leaves(values) if isinstance(value, Rejected)
        }
        if not bad:
            kept.append((values, origins))
            continue
        clean: Values = {}
        for path, value in leaves(values):
            if path not in bad:
                set_leaf(clean, path, value)
        kept.append((clean, {k: v for k, v in origins.items() if k not in bad}))
        rejected.update({path: (value, origins[path]) for path, value in bad.items()})
    return kept, rejected


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
