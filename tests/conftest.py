"""Shared test configuration."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings

# Hypothesis charges the first draw in a fresh process for building the strategies
# themselves, and measures it against a wall-clock budget. Neither that nor a
# per-example deadline says anything about the code under test, and both flake on a
# cold or loaded CI runner -- which is where this suite spends most of its life.
settings.register_profile(
    "monoconf",
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile("monoconf")


SNAPSHOTS = Path(__file__).parent / "snapshots"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-snapshots",
        action="store_true",
        help="Rewrite tests/snapshots/ from the current output instead of comparing.",
    )


Snapshot = Callable[[str, str], None]


@pytest.fixture
def snapshot(request: pytest.FixtureRequest) -> Snapshot:
    """Compare rendered output against `tests/snapshots/<name>.txt`, exactly.

    These strings are the user interface. A change to one has to arrive as a diff
    to a file someone reviews, which `--update-snapshots` produces.
    """
    update = bool(request.config.getoption("--update-snapshots"))

    def check(name: str, text: str) -> None:
        path = SNAPSHOTS / f"{name}.txt"
        if update:
            path.parent.mkdir(exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            return
        if not path.exists():
            pytest.fail(
                f"no snapshot at {path}. Run pytest --update-snapshots to create it, "
                "and review the file it writes."
            )
        # read_text translates newlines, so a checkout with CRLF line endings
        # compares equal.
        assert text == path.read_text(encoding="utf-8")

    return check
