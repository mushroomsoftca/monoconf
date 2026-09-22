"""The defaults layer, and the one thing it must not do."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel
from pydantic import Field as pydantic_field

from monoconf import Config, Origin, SourceKind, settings
from monoconf.sources import Context, defaults


class Database(BaseModel):
    host: str = "localhost"
    port: int = 5432


class App(Config):
    # The file layers are off: a real /etc/myapp.toml on the machine running
    # these tests is not an input to them.
    model_config = settings(app_name="myapp", system_config=None, user_config=None)

    dataset: str

    output_dir: Path = Path("out/")
    tags: list[str] = []
    database: Database = Database()


CTX = Context(argv=(), env={})


def test_every_defaulted_field_is_collected_and_attributed() -> None:
    values, origins = defaults.collect(App.__monoconf_spec__, CTX)
    assert values == {
        "output_dir": Path("out/"),
        "tags": [],
        "database": {"host": "localhost", "port": 5432},
    }
    assert set(origins) == {"output_dir", "tags", "database.host", "database.port"}
    assert all(origin == Origin(SourceKind.DEFAULT) for origin in origins.values())


def test_required_fields_contribute_nothing() -> None:
    values, origins = defaults.collect(App.__monoconf_spec__, CTX)
    assert "dataset" not in values
    assert "dataset" not in origins


def test_mutable_defaults_are_not_shared_between_resolutions() -> None:
    first = App.resolve(argv=["a"], env={})
    first.tags.append("mutated")
    assert App.resolve(argv=["b"], env={}).tags == []


def test_a_default_factory_is_evaluated_per_resolution() -> None:
    class Stamped(Config):
        model_config = settings(app_name="myapp", system_config=None, user_config=None)

        started: datetime = pydantic_field(default_factory=datetime.now)

    first = Stamped.resolve(argv=[], env={}).started
    second = Stamped.resolve(argv=[], env={}).started
    # Reusing the import-time default would freeze this at whenever the class was
    # defined, which is the wrong answer for anything time- or process-dependent.
    assert second >= first
    assert Stamped.__monoconf_spec__.by_path()["started"].default != second
