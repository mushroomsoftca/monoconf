"""The README's code and output blocks are the examples, verbatim.

A block after `<!-- source: examples/x.py -->` must be that file. A block after
`<!-- output: [VAR=value ...] python examples/x.py [args] -->` must be what that
command prints: stdout when it succeeds, stderr when it fails. Nobody reads a
README that has drifted from the code twice, so the drift fails the build.

`pytest --update-snapshots` rewrites the blocks from the current output, like any
other snapshot; review the diff it leaves.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
README = ROOT / "README.md"

_BLOCK = re.compile(
    r"(<!-- (source|output): (.+?) -->\n```[a-z]*\n)(.*?)(```)", re.DOTALL
)


def expected(kind: str, command: str) -> str:
    if kind == "source":
        return (ROOT / command).read_text(encoding="utf-8")
    words = shlex.split(command)
    env = {"COLUMNS": "88"}
    while "=" in words[0]:
        name, _, value = words.pop(0).partition("=")
        env[name] = value
    assert words[0] == "python", f"README command must start with python: {command}"
    result = subprocess.run(
        [sys.executable, *words[1:]],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        check=False,
    )
    return result.stdout if result.returncode == 0 else result.stderr


def test_the_readme_shows_what_the_examples_do(
    request: pytest.FixtureRequest,
) -> None:
    text = README.read_text(encoding="utf-8")
    blocks = list(_BLOCK.finditer(text))
    assert len(blocks) >= 4, "the README's marked blocks have gone missing"

    stale: list[str] = []

    def replace(match: re.Match[str]) -> str:
        head, kind, command, body, fence = match.groups()
        want = expected(kind, command)
        if body != want:
            stale.append(f"{kind}: {command}")
        return f"{head}{want}{fence}"

    updated = _BLOCK.sub(replace, text)
    if request.config.getoption("--update-snapshots"):
        README.write_text(updated, encoding="utf-8", newline="\n")
        return
    assert not stale, (
        "README blocks no longer match the examples: "
        + "; ".join(stale)
        + ". Run pytest --update-snapshots and review the README diff."
    )
