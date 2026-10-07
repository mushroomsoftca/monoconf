"""The sources, and the inputs they share.

Each module here exposes the same function:

    def collect(spec: Spec, ctx: Context) -> Layer: ...

A source reads its own inputs and returns the values it actually set, paired with
their origins. It never mutates shared state and never reads another source's
output: the ordering of the layers lives in the resolver alone.

A value a source cannot read is not raised on the spot. It is returned as a
`Rejected` in its place, with its origin, and the resolver reports it with the
validation errors, so that one run names every problem rather than the first.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from monoconf.schema import FieldSpec

__all__ = ["Context", "Rejected", "scalar_value"]


@dataclass(frozen=True, slots=True)
class Context:
    """Everything a source may read from outside itself.

    Both are explicit rather than read from global state, which is what makes a
    test hermetic: `resolve(argv=[...], env={})` cannot see the real environment.
    """

    argv: tuple[str, ...]
    env: Mapping[str, str]

    overrides: tuple[str, ...] = ()
    """Files named by `--config-override`, as the operator typed them.

    Pass one of the command line finds these, and the file source reads them:
    they change which files exist before anything else can be resolved. It is
    the resolver that hands them over, so no source is reading another's output.
    """

    show_config: bool = False
    """Pass one found `--show-config`, which makes the positionals optional."""


@dataclass(frozen=True, slots=True)
class Rejected:
    """A value a source read but could not make sense of.

    It stands where the value would have gone, and the resolver takes it out
    before merging: it never reaches pydantic, and the layers beneath decide the
    field as if it had not been set.
    """

    raw: str
    """The value as given, which the error quotes back unless it is a secret."""

    problem: str
    """What is wrong, said of the value: `is not a boolean; use ...`."""


def scalar_value(field: FieldSpec, raw: str) -> str | None:
    """Apply the empty-string rule to a string from the CLI or the environment.

    An empty string clears a field whose annotation admits `None`, and is the empty
    string otherwise: `MYAPP_NAME=` on a `str` field means "", not null. One rule,
    shared by both sources that hand monoconf strings.
    """
    return None if raw == "" and field.nullable else raw
