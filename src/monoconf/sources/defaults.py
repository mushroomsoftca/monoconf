"""Layer 1: the schema's own defaults.

pydantic would apply these anyway. They are collected as a layer regardless, so
that every resolved value has an origin and `--help` can show the value a flag
would fall back to.
"""

from __future__ import annotations

from monoconf.merge import Layer, Origin, Origins, SourceKind, Values, set_leaf
from monoconf.schema import Spec
from monoconf.sources import Context

__all__ = ["collect"]


def collect(spec: Spec, ctx: Context) -> Layer:
    """Collect every defaulted field. Reads nothing from `ctx`."""
    values: Values = {}
    origins: Origins = {}
    origin = Origin(SourceKind.DEFAULT)
    for field in spec.fields:
        if field.required:
            continue
        # Re-evaluated per resolution rather than reusing FieldSpec.default, which
        # was computed at import: a default_factory has to report this process.
        default = field.info.get_default(call_default_factory=True)
        set_leaf(values, field.path, default)
        origins[field.path] = origin
    return values, origins
