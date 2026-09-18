"""The sources, and the inputs they share.

Each module here exposes the same function:

    def collect(spec: Spec, ctx: Context) -> Layer: ...

A source reads its own inputs and returns the values it actually set, paired with
their origins. It never mutates shared state and never reads another source's
output: the ordering of the layers lives in the resolver alone.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

__all__ = ["Context"]


@dataclass(frozen=True, slots=True)
class Context:
    """Everything a source may read from outside itself.

    Both are explicit rather than read from global state, which is what makes a
    test hermetic: `resolve(argv=[...], env={})` cannot see the real environment.
    """

    argv: tuple[str, ...]
    env: Mapping[str, str]
