"""The `Config` base class.

A subclass is introspected once, at class-definition time: its `Spec` is derived
and its schema validated, so a malformed schema fails on import rather than at
`load()`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Self

from pydantic import BaseModel

from monoconf.schema import Spec, build_spec
from monoconf.settings import settings

__all__ = ["Config"]


class Config(BaseModel):
    """Base class for a monoconf configuration model.

    A field without a default is required, positional, and settable only from the
    command line. A field with a default is optional everywhere. Instances are
    frozen; `settings(frozen=False)` opts out.
    """

    model_config = settings()

    if TYPE_CHECKING:
        __monoconf_spec__: ClassVar[Spec]

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        # Runs after pydantic has finished building the model, so model_fields is
        # populated -- which __init_subclass__ cannot promise.
        super().__pydantic_init_subclass__(**kwargs)
        cls.__monoconf_spec__ = build_spec(cls)

    @classmethod
    def load(
        cls,
        argv: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> Self:
        """Resolve the configuration with CLI semantics.

        Bad input writes to stderr and raises `SystemExit(2)`; `--help` prints and
        exits 0. Use this in `main()`.
        """
        raise NotImplementedError("load() lands with the CLI source (M5).")

    @classmethod
    def resolve(
        cls,
        argv: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> Self:
        """Resolve the configuration with library semantics.

        Raises `MonoconfError` subclasses rather than exiting, and reports `--help`
        as `HelpRequested`. Use this in tests and when embedding.
        """
        raise NotImplementedError("resolve() lands with the CLI source (M5).")
