"""Introspection of a `Config` subclass into a `Spec`.

A `Spec` is the single description of a schema that every other module consumes:
the CLI builder, the environment reader, the file loader and the help formatter
all read names from here rather than deriving their own. No I/O lives in this
module.
"""

from __future__ import annotations

import types
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum, StrEnum
from functools import reduce
from operator import or_
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from monoconf.errors import SchemaError
from monoconf.fields import (
    cli_meta,
    env_name_for,
    flag_for,
    metavar_for,
    metavar_from_name,
    singularise,
)
from monoconf.settings import Settings, resolve_settings

__all__ = ["FieldKind", "FieldSpec", "NestedSpec", "Spec", "build_spec"]


class FieldKind(StrEnum):
    """How a field is expressed on the command line."""

    SCALAR = "scalar"
    BOOL = "bool"
    LIST = "list"
    SET = "set"


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One leaf field, with every name the sources need already derived."""

    path: str
    """Dotted path from the root model: `database.host`."""

    name: str
    """The leaf field name."""

    kind: FieldKind
    required: bool
    """No default, therefore a positional argument and CLI-only."""

    nullable: bool
    """The annotation admits `None`, so an empty string clears it."""

    annotation: Any
    """As declared, including any `Annotated` metadata and `| None`."""

    base_type: Any
    """What a single string from the CLI or environment has to satisfy.

    `None` is stripped, and for a repeated field this is the item type.
    """

    default: Any
    """The schema default, or `None` for a required field."""

    description: str | None
    flag: str | None
    """Long flag, or `None` for a positional."""

    negated: bool
    """The flag is the `--no-*` form of a field defaulting to `True`."""

    metavar: str | None
    """Help placeholder, or `None` for a presence-only boolean flag."""

    short: str | None
    env_name: str
    """Derived for every field; a required field's presence in the environment is
    a warning, which means the name still has to be known."""

    choices: tuple[str, ...] | None
    info: FieldInfo

    @property
    def repeated(self) -> bool:
        return self.kind in (FieldKind.LIST, FieldKind.SET)


@dataclass(frozen=True, slots=True)
class NestedSpec:
    """A field whose type is another model, kept for the definition-time checks."""

    path: str
    name: str
    model: type[BaseModel]
    required: bool
    nullable: bool


@dataclass(frozen=True, slots=True)
class Spec:
    """Everything monoconf knows about a schema, derived once at import."""

    model: type[BaseModel]
    settings: Settings
    fields: tuple[FieldSpec, ...]
    nested: tuple[NestedSpec, ...]

    @property
    def positionals(self) -> tuple[FieldSpec, ...]:
        """Required fields, in declaration order."""
        return tuple(f for f in self.fields if f.required)

    @property
    def options(self) -> tuple[FieldSpec, ...]:
        """Fields with a default, which is every field that takes a flag."""
        return tuple(f for f in self.fields if not f.required)

    def by_path(self) -> dict[str, FieldSpec]:
        return {f.path: f for f in self.fields}


def build_spec(model: type[BaseModel]) -> Spec:
    """Walk a model and derive its `Spec`. Raises `SchemaError` on a type it
    cannot map to a command line."""
    resolved = resolve_settings(model)
    fields: list[FieldSpec] = []
    nested: list[NestedSpec] = []
    for spec in _walk(model, model, resolved, ()):
        if isinstance(spec, NestedSpec):
            nested.append(spec)
        else:
            fields.append(spec)
    return Spec(
        model=model,
        settings=resolved,
        fields=tuple(fields),
        nested=tuple(nested),
    )


def _walk(
    root: type[BaseModel],
    model: type[BaseModel],
    settings: Settings,
    prefix: tuple[str, ...],
) -> Iterator[FieldSpec | NestedSpec]:
    for name, info in model.model_fields.items():
        path = ".".join((*prefix, name))
        declared = info.annotation
        inner, nullable = _split_none(declared)
        if _is_model(inner):
            yield NestedSpec(
                path=path,
                name=name,
                model=inner,
                required=info.is_required(),
                nullable=nullable,
            )
            yield from _walk(root, inner, settings, (*prefix, name))
            continue
        yield _leaf(root, settings, path, name, info, inner, nullable)


def _leaf(
    root: type[BaseModel],
    settings: Settings,
    path: str,
    name: str,
    info: FieldInfo,
    inner: Any,
    nullable: bool,
) -> FieldSpec:
    kind, item = _classify(root, path, inner)
    meta = cli_meta(info)
    required = info.is_required()
    default = None if required else info.get_default(call_default_factory=True)
    negated = kind is FieldKind.BOOL and default is True
    bare = _strip_annotated(item)

    repeated = kind in (FieldKind.LIST, FieldKind.SET)
    flag: str | None = None
    metavar: str | None = None
    if required:
        metavar = metavar_from_name(path, meta.metavar)
    else:
        flag = flag_for(
            _negate(path) if negated else path,
            repeated=repeated,
            override=meta.flag,
        )
        if kind is not FieldKind.BOOL:
            # A repeated flag is singular, and so is its placeholder: --tag TAG.
            metavar = metavar_for(
                _singular(path) if repeated else path, bare, meta.metavar
            )

    return FieldSpec(
        path=path,
        name=name,
        kind=kind,
        required=required,
        nullable=nullable,
        annotation=info.rebuild_annotation(),
        base_type=_with_metadata(item, tuple(info.metadata)),
        default=default,
        description=info.description,
        flag=flag,
        negated=negated,
        metavar=metavar,
        short=meta.short,
        env_name=env_name_for(path, settings.env_prefix),
        choices=_choices(bare),
        info=info,
    )


def _singular(path: str) -> str:
    parts = path.split(".")
    parts[-1] = singularise(parts[-1])
    return ".".join(parts)


def _negate(path: str) -> str:
    """`color` -> `no_color`, so the flag comes out as `--no-color`."""
    parts = path.split(".")
    parts[-1] = f"no_{parts[-1]}"
    return ".".join(parts)


def _classify(root: type[BaseModel], path: str, inner: Any) -> tuple[FieldKind, Any]:
    """Classify a non-model annotation, and return its item type."""
    if inner is bool:
        return FieldKind.BOOL, inner
    origin = get_origin(inner) or inner
    if origin in (list, set):
        args = get_args(inner)
        item = args[0] if args else str
        item_inner, _ = _split_none(item)
        if _is_model(item_inner):
            raise SchemaError(
                f"{_where(root, path)}: a list of models has no command-line form. "
                "Use a nested model with defaults, or keep the collection in a "
                "file-only schema of its own."
            )
        if _classify_container(item_inner) is not None:
            raise SchemaError(
                f"{_where(root, path)}: a list of containers has no command-line "
                "form. A repeated flag takes one value at a time, so the item type "
                "has to be a scalar."
            )
        return (FieldKind.LIST if origin is list else FieldKind.SET), item
    container = _classify_container(inner)
    if container is not None:
        raise SchemaError(
            f"{_where(root, path)}: {container} fields have no command-line form, "
            "because a flag carries one string. Use a nested model, whose fields "
            "each get their own flag."
        )
    return FieldKind.SCALAR, inner


_CONTAINERS: dict[Any, str] = {
    dict: "dict",
    tuple: "tuple",
    frozenset: "frozenset",
}


def _classify_container(tp: Any) -> str | None:
    origin = get_origin(tp) or tp
    return _CONTAINERS.get(origin)


def _is_model(tp: Any) -> bool:
    return isinstance(tp, type) and issubclass(tp, BaseModel)


def _strip_annotated(tp: Any) -> Any:
    while hasattr(tp, "__metadata__"):
        tp = get_args(tp)[0]
    return tp


def _with_metadata(tp: Any, metadata: tuple[Any, ...]) -> Any:
    if not metadata:
        return tp
    return Annotated[(tp, *metadata)]


def _split_none(tp: Any) -> tuple[Any, bool]:
    """Split `X | None` into `X` and nullability, leaving other unions intact."""
    bare = _strip_annotated(tp)
    if get_origin(bare) not in (Union, types.UnionType):
        return bare, False
    args = get_args(bare)
    rest = tuple(arg for arg in args if arg is not type(None))
    if len(rest) == len(args):
        return bare, False
    if len(rest) == 1:
        return _strip_annotated(rest[0]), True
    # Rebuilt rather than re-subscripted: `int | str` is the modern spelling.
    return reduce(or_, rest), True


def _choices(tp: Any) -> tuple[str, ...] | None:
    """Valid values for an `Enum` or `Literal`, for the help text."""
    if get_origin(tp) is Literal:
        return tuple(str(arg) for arg in get_args(tp))
    if isinstance(tp, type) and issubclass(tp, Enum):
        return tuple(str(member.value) for member in tp)
    return None


def _where(model: type[BaseModel], path: str) -> str:
    return f"{model.__name__}.{path}"
