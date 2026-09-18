"""The `Config` base class and the entry points that are still to come."""

from __future__ import annotations

import pytest

from monoconf import Config, settings
from monoconf.schema import Spec


class Minimal(Config):
    model_config = settings(app_name="myapp")

    port: int = 8080


def test_spec_is_attached_to_the_class() -> None:
    assert isinstance(Minimal.__monoconf_spec__, Spec)
    assert Minimal.__monoconf_spec__.by_path()["port"].flag == "--port"


def test_the_base_class_itself_has_no_spec() -> None:
    # __pydantic_init_subclass__ fires for subclasses only, so Config stays inert.
    assert "__monoconf_spec__" not in Config.__dict__


@pytest.mark.parametrize("entry_point", ["load", "resolve"])
def test_entry_points_are_not_wired_up_yet(entry_point: str) -> None:
    with pytest.raises(NotImplementedError, match="M5"):
        getattr(Minimal, entry_point)()
