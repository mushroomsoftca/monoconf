"""The rendered output, pinned: `--help`, `--show-config` and validation errors.

These strings are what an operator reads, so a change to any of them has to be a
deliberate diff to a file in tests/snapshots/. Run `pytest --update-snapshots` to
rewrite them, and review what it wrote.

The model has no short flags: argparse 3.13 renders an option with both forms
differently from 3.11 and 3.12, and a snapshot has to hold on every row of the CI
matrix. test_help.py covers short flags by fragment.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import pytest
from pydantic import BaseModel

from conftest import Snapshot
from monoconf import Config, Field, settings


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Database(BaseModel):
    host: str = Field("localhost", description="Database host.")
    port: int = Field(5432, description="Database port.")


class Widgets(Config):
    """Serve the widget index."""

    model_config = settings(
        app_name="myapp",
        prog="myapp",
        epilog="See https://example.com/docs for the full config reference.",
        show_config=True,
        config_override=True,
        system_config=None,
        user_config=None,
    )

    input_path: Path = Field(description="Path to the input file.")
    dataset: str = Field(description="Dataset name.")

    output_dir: Path = Field(Path("out/"), description="Output directory.")
    port: int = Field(8080, description="Port to listen on.")
    log_level: LogLevel = LogLevel.INFO
    verbose: bool = Field(False, description="Enable verbose logging.")
    color: bool = Field(True, description="Disable coloured output.")
    retry_codes: list[int] = Field(
        [502, 503], description="HTTP status to retry.", flag="retry-code"
    )
    tags: list[str] = Field(default_factory=list, description="Tag to apply.")
    proxy: str | None = Field(None, description="Proxy URL.")
    database: Database = Database()


@dataclass(frozen=True, slots=True)
class Machine:
    """A system file with a drop-in beside it, and a user file."""

    root: Path

    @property
    def system(self) -> Path:
        return self.root / "etc" / "myapp.toml"

    @property
    def user(self) -> Path:
        return self.root / "home" / "myapp.toml"

    def write(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def app(self) -> type[Widgets]:
        system, user = self.system, self.user

        class OnThisMachine(Widgets):
            model_config = settings(
                app_name="myapp",
                prog="myapp",
                system_config=system,
                user_config=user,
            )

        return OnThisMachine

    def normalise(self, text: str) -> str:
        """The same text on every OS: `<root>` for the temporary directory."""
        return text.replace(str(self.root), "<root>").replace("\\", "/")


@pytest.fixture
def machine(tmp_path: Path) -> Machine:
    machine = Machine(tmp_path)
    machine.write("etc/myapp.toml", "port = 9090\n\n[database]\nhost = 'db.internal'\n")
    machine.write("etc/myapp.d/20-site.toml", "log_level = 'warning'\n")
    machine.write("home/myapp.toml", "tags = ['blue', 'green']\n")
    return machine


def run(
    machine: Machine,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    env: dict[str, str],
) -> tuple[int, str, str]:
    """`load()`, as a process would see it: exit code, stdout, stderr."""
    with pytest.raises(SystemExit) as caught:
        machine.app().load(argv=argv, env=env)
    code = caught.value.code
    assert isinstance(code, int)
    out, err = capsys.readouterr()
    return code, machine.normalise(out), machine.normalise(err)


def test_help(
    machine: Machine, capsys: pytest.CaptureFixture[str], snapshot: Snapshot
) -> None:
    code, out, _ = run(machine, capsys, ["--help"], {"MYAPP_DATABASE__PORT": "6432"})
    assert code == 0
    snapshot("help", out)


def test_show_config(
    machine: Machine, capsys: pytest.CaptureFixture[str], snapshot: Snapshot
) -> None:
    code, out, _ = run(
        machine,
        capsys,
        ["in.csv", "--show-config", "--verbose", "--retry-code", "429"],
        {"MYAPP_DATABASE__PORT": "6432", "MYAPP_PROXY": ""},
    )
    assert code == 0
    snapshot("show_config", out)


def test_validation_errors(
    machine: Machine, capsys: pytest.CaptureFixture[str], snapshot: Snapshot
) -> None:
    machine.write("home/myapp.d/10-bad.toml", "port = 'nine thousand'\n")
    code, _, err = run(
        machine,
        capsys,
        ["in.csv", "widgets", "--retry-code", "429", "--retry-code", "soon"],
        {"MYAPP_LOG_LEVEL": "loud", "MYAPP_DATABASE__PORT": "high"},
    )
    assert code == 2
    snapshot("validation_errors", err)
