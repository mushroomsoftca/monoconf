"""The TOML emitter behind `--show-config`, tested against the standard reader.

Whatever it writes, `tomllib` has to read back as the value it was given. The
layout and the comments are pinned by the snapshots; this is about the values.
"""

from __future__ import annotations

import math
import tomllib
from datetime import UTC, date, datetime, time
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import SecretStr

from monoconf import Config, Origin, SourceKind, settings
from monoconf.toml import dump

DEFAULT = Origin(SourceKind.DEFAULT)


class App(Config):
    model_config = settings(app_name="myapp", system_config=None, user_config=None)

    dataset: str


def read_back(value: Any, key: str = "value") -> Any:
    text = dump("myapp", App.__monoconf_spec__, {key: value}, {key: DEFAULT})
    return tomllib.loads(text).get(key)


class Colour(Enum):
    RED = "red"


class Level(IntEnum):
    HIGH = 3


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        (0, 0),
        (-12, -12),
        (1.5, 1.5),
        (1e16, 1e16),
        (1e-5, 1e-5),
        (math.inf, math.inf),
        (-math.inf, -math.inf),
        ("", ""),
        ('quote " and back\\slash', 'quote " and back\\slash'),
        ("tab\tnewline\ncr\rbell\x07del\x7f", "tab\tnewline\ncr\rbell\x07del\x7f"),
        ("ünïcødé ✓", "ünïcødé ✓"),
        (Colour.RED, "red"),
        (Level.HIGH, 3),
        (Path("out/x"), str(Path("out/x"))),
        (UUID(int=1), str(UUID(int=1))),
        (date(2026, 9, 22), "2026-09-22"),
        (time(8, 30), "08:30:00"),
        (datetime(2026, 9, 22, 8, 30, tzinfo=UTC), "2026-09-22T08:30:00+00:00"),
        ([], []),
        (["a", "b"], ["a", "b"]),
        ((1, 2), [1, 2]),
        ({"b", "a", "c"}, ["a", "b", "c"]),
    ],
)
def test_a_value_reads_back_as_itself(value: Any, expected: Any) -> None:
    assert read_back(value) == expected


def test_nan_reads_back_as_nan() -> None:
    assert math.isnan(read_back(math.nan))


def test_none_is_commented_out() -> None:
    text = dump("myapp", App.__monoconf_spec__, {"proxy": None}, {"proxy": DEFAULT})
    assert "# proxy = null  # default" in text
    assert tomllib.loads(text) == {}


def test_a_list_holding_none_is_commented_out() -> None:
    text = dump("myapp", App.__monoconf_spec__, {"v": ["a", None]}, {"v": DEFAULT})
    assert '# v = ["a", null]  # default' in text
    assert tomllib.loads(text) == {}


def test_a_key_that_is_not_bare_is_quoted() -> None:
    assert read_back(1, key="naïve") == 1


@given(st.text())
def test_any_string_reads_back_as_itself(value: str) -> None:
    assert read_back(value) == value


@given(st.floats(allow_nan=False))
def test_any_float_reads_back_as_itself(value: float) -> None:
    assert read_back(value) == value


@given(st.lists(st.integers() | st.text() | st.booleans()))
def test_any_flat_list_reads_back_as_itself(value: list[Any]) -> None:
    assert read_back(value) == value


def test_a_secret_is_masked_and_commented_out() -> None:
    text = dump(
        "myapp", App.__monoconf_spec__, {"pw": SecretStr("hunter2")}, {"pw": DEFAULT}
    )
    assert "hunter2" not in text
    assert '# pw = "**********"  # default; secret, not shown' in text
    assert tomllib.loads(text) == {}


def test_a_list_of_secrets_is_masked_and_commented_out() -> None:
    class Keyed(Config):
        model_config = settings(app_name="myapp", system_config=None, user_config=None)

        keys: list[SecretStr] = []

    keys = [SecretStr("k-one"), SecretStr("k-two")]
    text = dump("myapp", Keyed.__monoconf_spec__, {"keys": keys}, {"keys": DEFAULT})
    assert "k-one" not in text
    # The value is a list, not a secret: only the field knows. Live, the masks
    # would replace both keys when the dump is read back.
    assert '# keys = ["**********", "**********"]' in text
    assert tomllib.loads(text) == {}
