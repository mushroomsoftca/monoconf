"""The `Config` base class, and `load()`'s command-line semantics."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import model_validator

from monoconf import Config, ValidationFailed, settings
from monoconf.schema import Spec


class Minimal(Config):
    model_config = settings(app_name="myapp", prog="myapp")

    input_path: Path
    port: int = 8080


def test_spec_is_attached_to_the_class() -> None:
    assert isinstance(Minimal.__monoconf_spec__, Spec)
    assert Minimal.__monoconf_spec__.by_path()["port"].flag == "--port"


def test_the_base_class_itself_has_no_spec() -> None:
    # __pydantic_init_subclass__ fires for subclasses only, so Config stays inert.
    assert "__monoconf_spec__" not in Config.__dict__


def test_load_returns_a_resolved_config() -> None:
    cfg = Minimal.load(argv=["in.csv", "--port", "9090"], env={})
    assert cfg.port == 9090
    assert cfg.origin_of("port").describe() == "--port"


def test_load_prints_help_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as caught:
        Minimal.load(argv=["--help"], env={})
    assert caught.value.code == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("usage: myapp")
    assert captured.err == ""


def test_load_reports_bad_input_on_stderr_and_exits_two(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as caught:
        Minimal.load(argv=["in.csv", "--nope"], env={})
    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "myapp: unrecognized arguments: --nope" in captured.err
    assert "Try 'myapp --help' for usage." in captured.err


def test_load_reports_a_bad_value_with_its_origin(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as caught:
        Minimal.load(argv=["in.csv", "--port", "nine thousand"], env={})
    assert caught.value.code == 2
    err = capsys.readouterr().err
    assert "myapp: invalid configuration" in err
    assert "port:" in err
    assert "from --port" in err


def test_a_whole_model_validator_failure_has_no_single_origin() -> None:
    class Paired(Config):
        model_config = settings(app_name="myapp", prog="myapp")

        low: int = 1
        high: int = 10

        @model_validator(mode="after")
        def check_order(self) -> Paired:
            if self.low > self.high:
                raise ValueError("low must not exceed high")
            return self

    with pytest.raises(ValidationFailed) as caught:
        Paired.resolve(argv=["--low", "20"], env={})
    message = str(caught.value)
    # The failure belongs to the model, not to one field, so there is no origin to
    # name -- and saying so beats naming the wrong one.
    assert "(root): Value error, low must not exceed high" in message
    assert "no value from any source" in message
