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
from typing import Annotated, Any, Literal, Union, cast, get_args, get_origin

from pydantic import (
    BaseModel,
    ConfigDict,
    PydanticUserError,
    TypeAdapter,
    ValidationError,
)
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

# monoconf owns these names whether or not the features behind them are enabled,
# so turning one on later cannot break a schema that already worked.
RESERVED_NAMES = frozenset({"help", "config_override", "show_config"})
RESERVED_FLAGS = frozenset({"--help", "--config-override", "--show-config"})


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
    for item in _walk(model, model, resolved, ()):
        if isinstance(item, NestedSpec):
            nested.append(item)
        else:
            fields.append(item)
    spec = Spec(
        model=model,
        settings=resolved,
        fields=tuple(fields),
        nested=tuple(nested),
    )
    validate_spec(spec)
    return spec


def validate_spec(spec: Spec) -> None:
    """Enforce every schema rule, at class-definition time.

    A malformed schema has to fail on import: by `load()` the process is already
    running, and the operator cannot do anything about a maintainer's mistake.
    """
    _check_reserved(spec)
    _check_collisions(spec)
    _check_short_flags(spec)
    _check_requiredness(spec)
    _check_booleans(spec)
    _check_nested(spec)
    _check_string_constructible(spec)


def _check_reserved(spec: Spec) -> None:
    root_table = spec.settings.root_table
    for f in spec.fields:
        top_level = f.path == f.name
        if top_level and f.name in RESERVED_NAMES:
            raise SchemaError(
                f"{_where(spec.model, f.path)}: '{f.name}' is reserved. monoconf "
                "uses --help, --config-override and --show-config, and reserves all "
                "three whether or not they are enabled. Rename the field."
            )
        if f.flag in RESERVED_FLAGS:
            raise SchemaError(
                f"{_where(spec.model, f.path)}: the flag {f.flag} is reserved by "
                "monoconf. Rename the field, or give it a different flag with "
                "Field(flag=...)."
            )
        if top_level and root_table is not None and f.name == root_table:
            raise SchemaError(
                f"{_where(spec.model, f.path)}: a top-level field named "
                f"'{f.name}' cannot be told apart from the root table "
                f"[{root_table}] that config files may be written under. Rename "
                "the field, or pass settings(root_table=...) to name the table "
                "something else."
            )


def _check_collisions(spec: Spec) -> None:
    flags: dict[str, str] = {}
    env_names: dict[str, str] = {}
    for f in spec.fields:
        if f.flag is not None:
            first = flags.setdefault(f.flag, f.path)
            if first != f.path:
                raise SchemaError(
                    f"{_where(spec.model, f.path)}: the flag {f.flag} is already "
                    f"taken by '{first}'. Two fields cannot share a flag; give one "
                    "of them Field(flag=...) or rename it."
                )
        first_env = env_names.setdefault(f.env_name, f.path)
        if first_env != f.path:
            raise SchemaError(
                f"{_where(spec.model, f.path)}: the environment variable "
                f"{f.env_name} is already taken by '{first_env}'. Rename one of "
                "the fields; the variable name is derived from the field path and "
                "cannot be overridden."
            )


def _check_short_flags(spec: Spec) -> None:
    shorts: dict[str, str] = {}
    for f in spec.fields:
        if f.short is None:
            continue
        if len(f.short) != 1 or not f.short.isalnum():
            raise SchemaError(
                f"{_where(spec.model, f.path)}: short={f.short!r} must be a single "
                "alphanumeric character."
            )
        if f.short == "h":
            raise SchemaError(
                f"{_where(spec.model, f.path)}: -h is reserved for --help. Choose "
                "another short flag."
            )
        first = shorts.setdefault(f.short, f.path)
        if first != f.path:
            raise SchemaError(
                f"{_where(spec.model, f.path)}: -{f.short} is already taken by "
                f"'{first}'. Short flags are never derived, so one of the two was "
                "asked for by mistake."
            )
        if f.required:
            raise SchemaError(
                f"{_where(spec.model, f.path)}: short={f.short!r} is meaningless on "
                "a field without a default, which is a positional argument. Give "
                "the field a default, or drop the short flag."
            )


def _check_requiredness(spec: Spec) -> None:
    for f in spec.fields:
        if not f.required:
            continue
        if "." in f.path:
            raise SchemaError(
                f"{_where(spec.model, f.path)}: every field of a nested model needs "
                "a default. A required field becomes a positional argument, and "
                "positionals are flat and ordered, so a nested one has nowhere to "
                f"go -- and nothing else may set it. Give '{f.name}' a default."
            )
        if f.nullable:
            raise SchemaError(
                f"{_where(spec.model, f.path)}: an optional type without a default "
                "is still required, and becomes a positional argument. Did you mean "
                f"'{f.name}: {_type_name(f.base_type)} | None = None'?"
            )
        if f.kind in (FieldKind.LIST, FieldKind.SET):
            raise SchemaError(
                f"{_where(spec.model, f.path)}: a required {f.kind.value} cannot be "
                "a positional argument -- positionals are scalars, one value each. "
                f"Give '{f.name}' a default and set it with a repeated {f.kind.value} "
                "flag instead."
            )


def _check_booleans(spec: Spec) -> None:
    for f in spec.fields:
        if f.kind is not FieldKind.BOOL:
            continue
        if f.name.startswith("no_") or f.name.endswith("_no"):
            raise SchemaError(
                f"{_where(spec.model, f.path)}: a boolean field must not be named "
                f"'{f.name}'. monoconf generates the negative flag itself, so this "
                "would produce something like --no-no-cache. Name the field for "
                "what it enables."
            )
        if f.nullable:
            raise SchemaError(
                f"{_where(spec.model, f.path)}: a boolean flag is presence-only, so "
                "it can express two states and this field has three. Use two "
                "separate fields, or an enum whose members name the three cases."
            )


def _check_nested(spec: Spec) -> None:
    # Imported here rather than at module scope: config.py imports this module.
    from monoconf.config import Config

    for n in spec.nested:
        if n.required:
            raise SchemaError(
                f"{_where(spec.model, n.path)}: a nested model needs a default, "
                "because it cannot be a positional argument. Write "
                f"'{n.name}: {n.model.__name__} = {n.model.__name__}()'."
            )
        if n.nullable:
            raise SchemaError(
                f"{_where(spec.model, n.path)}: a nested model must not be "
                "nullable. Its fields each get their own flag, and those flags have "
                f"nowhere to write to when '{n.name}' is None. Drop the '| None'."
            )
        if issubclass(n.model, Config):
            raise SchemaError(
                f"{_where(spec.model, n.path)}: a nested model must be a plain "
                f"BaseModel, not a Config. {n.model.__name__} carries CLI and "
                "settings machinery that means nothing as a child; make it a "
                "BaseModel."
            )


_PROBE = "monoconf-probe"


def _check_string_constructible(spec: Spec) -> None:
    """Reject a type the CLI and environment could never set.

    Every field is settable from the command line, which hands it a string, so a
    type that cannot accept one is unusable -- and much better caught on import
    than on an operator's input.
    """
    config = _pydantic_config(spec.model)
    for f in spec.fields:
        if _accepts_a_string(f.base_type, config):
            continue
        name = _type_name(f.base_type)
        raise SchemaError(
            f"{_where(spec.model, f.path)}: type '{name}' cannot be constructed "
            "from a string, so it cannot be set from the command line or the "
            f"environment.\n\nEither give {name} a single-string constructor, or "
            f"annotate the field:\n\n    {f.name}: Annotated[{name}, "
            f"BeforeValidator({name}.parse)] = ..."
        )


def _accepts_a_string(annotation: Any, config: ConfigDict) -> bool:
    """Whether pydantic would even look at a string for this type.

    "Does validation raise" is the wrong question: `int` rejects the probe string
    with int_parsing while being perfectly string-constructible. What distinguishes
    the two is the error code -- a `*_type` code means the input was of the wrong
    kind, anything else means the type read the string and disliked this one.
    """
    try:
        adapter = TypeAdapter(annotation, config=config)
    except PydanticUserError:
        # A type that carries its own config refuses an outer one, and does not
        # need the model's: it already built once when the model did.
        adapter = TypeAdapter(annotation)
    try:
        adapter.validate_python(_PROBE)
    except ValidationError as exc:
        codes = [error["type"] for error in exc.errors()]
        return not all(_is_type_mismatch(code) for code in codes)
    return True


_PYDANTIC_CONFIG_KEYS = frozenset(ConfigDict.__annotations__)


def _pydantic_config(model: type[BaseModel]) -> ConfigDict:
    """The model's own pydantic config, without monoconf's keys.

    The probe has to validate under the same config the model does, or a field
    relying on `arbitrary_types_allowed` or a strictness setting would be judged
    against different rules than the ones it will actually be validated with.
    """
    raw = cast(dict[str, Any], model.model_config)
    kept = {k: v for k, v in raw.items() if k in _PYDANTIC_CONFIG_KEYS}
    return cast(ConfigDict, kept)


def _is_type_mismatch(code: str) -> bool:
    return code.endswith("_type") or code == "is_instance_of"


def _type_name(annotation: Any) -> str:
    return getattr(annotation, "__name__", None) or str(annotation)


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
