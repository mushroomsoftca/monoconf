"""The whole chain, end to end: all eight layers at once, and what provenance
has to be true of any combination of them.

This is the behaviour the library exists for. The layers are exercised through
`resolve()` alone -- real files in a real directory, a real argv, a real
environment mapping -- because the thing worth testing is that the eight of them
agree on an order, not that each of them works in isolation.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel

from monoconf import Config, HelpRequested, Origin, SourceKind, settings
from monoconf.merge import leaves

DEFAULT_PORT = 8080

# One distinguishable value per layer, in the order the chain folds them.
SYSTEM_FILE = 8001
SYSTEM_DROPIN = 8002
USER_FILE = 8003
USER_DROPIN = 8004
OVERRIDE = 8005
ENV = 8006
CLI = 8007


class Database(BaseModel):
    host: str = "localhost"
    port: int = 5432


class Fields(Config):
    """Serve the widget index."""

    # The paths belong to whichever temporary directory a test is using, so they
    # are pinned by the subclass each one builds.
    model_config = settings(
        app_name="myapp", prog="myapp", system_config=None, user_config=None
    )

    port: int = DEFAULT_PORT
    database: Database = Database()


@dataclass(frozen=True, slots=True)
class Chain:
    """Every layer of the chain, laid out in one directory."""

    root: Path
    model: type[Fields]

    @property
    def system(self) -> Path:
        return self.root / "etc" / "myapp.toml"

    @property
    def user(self) -> Path:
        return self.root / "home" / "myapp.toml"

    @property
    def override(self) -> Path:
        return self.root / "named.toml"

    def dropin(self, file: Path) -> Path:
        return file.with_suffix(".d") / "50-set.toml"

    def resolve(self, peeled: int = 0) -> Fields:
        """Resolve with the top `peeled` layers taken away."""
        if peeled >= 4:
            self.dropin(self.user).unlink(missing_ok=True)
        if peeled >= 5:
            self.user.unlink(missing_ok=True)
        if peeled >= 6:
            self.dropin(self.system).unlink(missing_ok=True)
        if peeled >= 7:
            self.system.unlink(missing_ok=True)
        argv = [] if peeled >= 1 else ["--port", str(CLI)]
        env = {} if peeled >= 2 else {"MYAPP_PORT": str(ENV)}
        if peeled < 3:
            argv = ["--config-override", str(self.override), *argv]
        return self.model.resolve(argv=argv, env=env)


@pytest.fixture
def chain(tmp_path: Path) -> Chain:
    system = tmp_path / "etc" / "myapp.toml"
    user = tmp_path / "home" / "myapp.toml"
    for file, value in ((system, SYSTEM_FILE), (user, USER_FILE)):
        file.parent.mkdir()
        file.write_text(f"port = {value}\n")
    for file, value in ((system, SYSTEM_DROPIN), (user, USER_DROPIN)):
        directory = file.with_suffix(".d")
        directory.mkdir()
        (directory / "50-set.toml").write_text(f"port = {value}\n")
    (tmp_path / "named.toml").write_text(f"port = {OVERRIDE}\n")

    class App(Fields):
        model_config = settings(
            app_name="myapp",
            prog="myapp",
            system_config=system,
            user_config=user,
            config_override=True,
        )

    return Chain(root=tmp_path, model=App)


def test_the_chain_peels_one_layer_at_a_time(chain: Chain) -> None:
    # Acceptance 1. Set at all eight layers, the command line wins; take the
    # layers away from the top and each one below takes over in turn.
    expected = [
        (CLI, SourceKind.CLI),
        (ENV, SourceKind.ENV),
        (OVERRIDE, SourceKind.OVERRIDE_FILE),
        (USER_DROPIN, SourceKind.USER_DROPIN),
        (USER_FILE, SourceKind.USER_FILE),
        (SYSTEM_DROPIN, SourceKind.SYSTEM_DROPIN),
        (SYSTEM_FILE, SourceKind.SYSTEM_FILE),
        (DEFAULT_PORT, SourceKind.DEFAULT),
    ]
    for peeled, (port, source) in enumerate(expected):
        cfg = chain.resolve(peeled=peeled)
        assert (cfg.port, cfg.origin_of("port").source) == (port, source)


def test_the_sentinel_holds_across_the_whole_chain(chain: Chain) -> None:
    # Acceptance 2, with files underneath it now. The schema default is 8080 and
    # every layer sets something else; asking for 8080 on the command line has to
    # win anyway, or argparse's own defaults have leaked into the merge.
    cfg = chain.model.resolve(
        argv=["--port", str(DEFAULT_PORT)], env={"MYAPP_PORT": str(ENV)}
    )
    assert cfg.port == DEFAULT_PORT
    assert cfg.origin_of("port") == Origin(SourceKind.CLI, detail="--port")


def test_help_shows_the_default_the_files_resolved(chain: Chain) -> None:
    # Acceptance 9. The bracket in help output is what this flag would fall back
    # to on this machine, which is a fact about the files, not about the schema.
    with pytest.raises(HelpRequested) as caught:
        chain.model.resolve(argv=["--help"], env={})
    assert f"[{USER_DROPIN}]" in caught.value.text


def test_every_layer_can_be_traced_back_to_its_file(chain: Chain) -> None:
    cfg = chain.resolve(peeled=3)
    assert cfg.origin_of("port").path == chain.dropin(chain.user)
    assert cfg.origin_of("port").describe() == str(chain.dropin(chain.user))
    # A value nobody set still has an origin: the schema's own default.
    assert cfg.origin_of("database.host") == Origin(SourceKind.DEFAULT)


# The layers a property test can set, lowest priority first. The file layers all
# share one code path, so one of them stands in for the four.
LAYERS = ("default", "file", "env", "cli")
PATHS = ("port", "database.port")


@st.composite
def assignments(draw: st.DrawFn) -> dict[str, dict[str, int]]:
    """Which layers set which fields, and to what."""
    written = {}
    for layer in LAYERS[1:]:
        fields = draw(st.lists(st.sampled_from(PATHS), unique=True, max_size=2))
        values = {path: draw(st.integers(1, 65535)) for path in fields}
        if values:
            written[layer] = values
    return written


def as_toml(values: dict[str, int]) -> str:
    top = [f"{path} = {value}" for path, value in values.items() if "." not in path]
    nested = [
        f"{path.split('.')[1]} = {value}"
        for path, value in values.items()
        if "." in path
    ]
    table = ["", "[database]", *nested] if nested else []
    return "\n".join([*top, *table]) + "\n"


@given(written=assignments())
def test_every_leaf_has_exactly_one_origin(written: dict[str, dict[str, int]]) -> None:
    # No leaf may resolve without a provenance, and none may have two: whatever
    # the layers do to each other, "where did this come from?" has one answer.
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "myapp.toml"
        path.write_text(as_toml(written.get("file", {})))

        class App(Fields):
            model_config = settings(
                app_name="myapp", prog="myapp", system_config=None, user_config=path
            )

        argv = [
            part
            for field, value in written.get("cli", {}).items()
            for part in (f"--{field.replace('.', '-')}", str(value))
        ]
        env = {
            f"MYAPP_{field.upper().replace('.', '__')}": str(value)
            for field, value in written.get("env", {}).items()
        }
        cfg = App.resolve(argv=argv, env=env)

    resolved = dict(leaves(cfg.model_dump()))
    origins = {path: cfg.origin_of(path) for path in resolved}
    assert set(origins) == set(resolved)
    for field, origin in origins.items():
        # The highest layer that set a field is the one that has to own it.
        expected = next(
            (
                layer
                for layer in reversed(LAYERS[1:])
                if field in written.get(layer, {})
            ),
            "default",
        )
        assert (
            origin.source
            is {
                "default": SourceKind.DEFAULT,
                "file": SourceKind.USER_FILE,
                "env": SourceKind.ENV,
                "cli": SourceKind.CLI,
            }[expected]
        )
