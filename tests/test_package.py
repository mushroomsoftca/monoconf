"""The package imports, ships type information, and exports what it promises."""

from __future__ import annotations

import importlib.resources

import monoconf
from monoconf import MonoconfError, OutputRequested, SchemaError


def test_exports_are_sorted_and_complete() -> None:
    assert monoconf.__all__ == sorted(monoconf.__all__)
    for name in monoconf.__all__:
        assert hasattr(monoconf, name), name


def test_every_error_derives_from_monoconf_error() -> None:
    assert issubclass(SchemaError, MonoconfError)
    assert issubclass(OutputRequested, MonoconfError)


def test_output_requested_carries_text_and_exit_code() -> None:
    exc = OutputRequested("usage: myapp ...")
    assert exc.text == "usage: myapp ..."
    assert exc.exit_code == 0


def test_py_typed_is_shipped() -> None:
    assert importlib.resources.files("monoconf").joinpath("py.typed").is_file()
