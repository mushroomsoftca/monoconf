"""The file layers: how they stack, which table they are read from, and what a
file is not allowed to do.

Every path here is pinned inside `tmp_path`. The platform's own locations are
derived in M7b; nothing in this file depends on them, or on the machine it runs
on.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from monoconf import (
    Config,
    ConfigFileError,
    HelpRequested,
    IgnoredValue,
    Origin,
    SourceError,
    SourceKind,
    UnknownConfigKey,
    UnreadableSource,
    ValidationFailed,
    settings,
)


class Database(BaseModel):
    host: str = "localhost"
    port: int = 5432


class Fields(Config):
    """Serve the widget index."""

    # The paths are the point of every test here, so they are pinned per test by
    # app() below rather than declared once on this class.
    model_config = settings(
        app_name="myapp", prog="myapp", system_config=None, user_config=None
    )

    dataset: str

    port: int = 8080
    tags: list[str] = []
    database: Database = Database()


@dataclass(frozen=True, slots=True)
class Layout:
    """The two discovered locations, and the drop-in directories beside them."""

    system: Path
    user: Path

    @property
    def system_dropins(self) -> Path:
        return self.system.with_suffix(".d")

    @property
    def user_dropins(self) -> Path:
        return self.user.with_suffix(".d")

    def dropin(self, directory: Path, name: str, text: str) -> Path:
        directory.mkdir(exist_ok=True)
        path = directory / name
        path.write_text(text)
        return path


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    system = tmp_path / "etc" / "myapp.toml"
    user = tmp_path / "home" / "myapp.toml"
    system.parent.mkdir()
    user.parent.mkdir()
    return Layout(system=system, user=user)


def app(layout: Layout, **overrides: Any) -> type[Fields]:
    """The schema under test, with this layout's paths pinned into it."""

    class App(Fields):
        model_config = settings(
            app_name="myapp",
            prog="myapp",
            system_config=layout.system,
            user_config=layout.user,
            **overrides,
        )

    return App


def resolve(layout: Layout, *argv: str, **overrides: Any) -> Fields:
    return app(layout, **overrides).resolve(argv=["widgets", *argv], env={})


def test_a_system_file_beats_the_defaults(layout: Layout) -> None:
    layout.system.write_text("port = 9090\n")
    cfg = resolve(layout)
    assert cfg.port == 9090
    assert cfg.origin_of("port") == Origin(SourceKind.SYSTEM_FILE, path=layout.system)
    assert cfg.origin_of("port").describe() == str(layout.system)


def test_the_user_file_beats_the_system_file(layout: Layout) -> None:
    layout.system.write_text("port = 9090\n")
    layout.user.write_text("port = 7070\n")
    cfg = resolve(layout)
    assert cfg.port == 7070
    assert cfg.origin_of("port").source is SourceKind.USER_FILE


def test_a_drop_in_beats_the_file_it_sits_beside(layout: Layout) -> None:
    layout.system.write_text("port = 9090\n")
    layout.dropin(layout.system_dropins, "10-base.toml", "port = 6000\n")
    cfg = resolve(layout)
    assert cfg.port == 6000
    assert cfg.origin_of("port").source is SourceKind.SYSTEM_DROPIN


def test_the_user_file_beats_a_system_drop_in(layout: Layout) -> None:
    # Layer 4 is above layer 3: every system location settles before the user's
    # own file is read, which is what makes a local override reliable.
    layout.dropin(layout.system_dropins, "10-base.toml", "port = 6000\n")
    layout.user.write_text("port = 7070\n")
    cfg = resolve(layout)
    assert cfg.port == 7070


def test_a_user_drop_in_beats_every_other_file(layout: Layout) -> None:
    layout.system.write_text("port = 9090\n")
    layout.dropin(layout.system_dropins, "10-base.toml", "port = 6000\n")
    layout.user.write_text("port = 7070\n")
    layout.dropin(layout.user_dropins, "99-local.toml", "port = 5000\n")
    cfg = resolve(layout)
    assert cfg.port == 5000
    assert cfg.origin_of("port").source is SourceKind.USER_DROPIN


def test_the_environment_beats_every_file(layout: Layout) -> None:
    layout.dropin(layout.user_dropins, "99-local.toml", "port = 5000\n")
    cfg = app(layout).resolve(argv=["widgets"], env={"MYAPP_PORT": "9090"})
    assert cfg.port == 9090
    assert cfg.origin_of("port").source is SourceKind.ENV


def test_the_command_line_beats_every_file(layout: Layout) -> None:
    layout.dropin(layout.user_dropins, "99-local.toml", "port = 5000\n")
    assert resolve(layout, "--port", "7070").port == 7070


def test_several_system_paths_apply_in_the_order_given(tmp_path: Path) -> None:
    # POSIX system configuration is a list of locations, not one: XDG_CONFIG_DIRS
    # then /etc. A sequence of paths is ascending priority, last wins.
    lower = tmp_path / "xdg.toml"
    upper = tmp_path / "etc.toml"
    lower.write_text('port = 9090\ntags = ["a"]\n')
    upper.write_text("port = 7070\n")

    class App(Fields):
        model_config = settings(
            app_name="myapp",
            prog="myapp",
            system_config=(lower, upper),
            user_config=None,
        )

    cfg = App.resolve(argv=["widgets"], env={})
    assert (cfg.port, cfg.tags) == (7070, ["a"])
    assert cfg.origin_of("port") == Origin(SourceKind.SYSTEM_FILE, path=upper)
    assert cfg.origin_of("tags") == Origin(SourceKind.SYSTEM_FILE, path=lower)


def test_deep_merge_keeps_what_the_other_layer_set(layout: Layout) -> None:
    # Acceptance 4. The systemd behaviour operators already know: a drop-in adds
    # to the table it names rather than replacing it.
    layout.system.write_text('[database]\nhost = "db.internal"\n')
    dropin = layout.dropin(
        layout.system_dropins, "20-site.toml", "[database]\nport = 6000\n"
    )
    cfg = resolve(layout)
    assert (cfg.database.host, cfg.database.port) == ("db.internal", 6000)
    assert cfg.origin_of("database.host").path == layout.system
    assert cfg.origin_of("database.port").path == dropin


def test_drop_ins_apply_in_filename_order(layout: Layout) -> None:
    # Acceptance 5, first half: the 10-/20-/99- convention works.
    layout.dropin(layout.system_dropins, "10-a.toml", "port = 6000\n")
    later = layout.dropin(layout.system_dropins, "20-b.toml", "port = 7000\n")
    cfg = resolve(layout)
    assert cfg.port == 7000
    assert cfg.origin_of("port").path == later


def test_drop_in_order_is_byte_wise_not_numeric(layout: Layout) -> None:
    # Acceptance 5, second half. '9' sorts after '2', so 9-a wins -- deliberately,
    # and for the same reason systemd does it: the filename is a string.
    layout.dropin(layout.system_dropins, "20-b.toml", "port = 7000\n")
    later = layout.dropin(layout.system_dropins, "9-a.toml", "port = 6000\n")
    cfg = resolve(layout)
    assert cfg.port == 6000
    assert cfg.origin_of("port").path == later


def test_a_list_replaces_rather_than_appends(layout: Layout) -> None:
    # Acceptance 6. A list is a value, not a structure to merge into.
    layout.system.write_text('tags = ["a", "b"]\n')
    layout.dropin(layout.system_dropins, "20-site.toml", 'tags = ["c"]\n')
    assert resolve(layout).tags == ["c"]


def test_a_file_written_in_both_root_forms_is_an_error(layout: Layout) -> None:
    # Acceptance 7. Half the file would silently do nothing, so nothing is read.
    layout.system.write_text('[myapp]\nport = 9090\n\n[database]\nhost = "x"\n')
    with pytest.raises(ConfigFileError) as caught:
        resolve(layout)
    message = str(caught.value)
    assert str(layout.system) in message
    assert "both forms" in message
    assert "[myapp]" in message
    assert "'database'" in message


def test_the_both_forms_error_names_every_ambiguous_key(layout: Layout) -> None:
    layout.system.write_text('port = 9090\ntags = ["a"]\n\n[myapp]\nport = 7070\n')
    with pytest.raises(ConfigFileError, match=r"keys 'port' and 'tags'"):
        resolve(layout)


def test_the_root_table_form_is_read(layout: Layout) -> None:
    layout.system.write_text('[myapp]\nport = 9090\n\n[myapp.database]\nhost = "db"\n')
    cfg = resolve(layout)
    assert (cfg.port, cfg.database.host) == (9090, "db")


def test_a_file_may_hold_another_application_as_well(layout: Layout) -> None:
    # This is what the root-table form is for. Another application's table is not
    # this schema's business, and saying so on every load would be noise.
    layout.system.write_text("[myapp]\nport = 9090\n\n[otherapp]\nport = 1\n")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cfg = resolve(layout)
    assert cfg.port == 9090


def test_a_renamed_root_table_reads_a_shared_file(layout: Layout) -> None:
    # The strongest case for the form: a tool reading its own table out of a file
    # that belongs to a wider ecosystem, with sections it has never heard of.
    layout.system.write_text(
        '[project]\nname = "widgets"\n\n'
        '[build-system]\nrequires = ["hatchling"]\n\n'
        "[tool.ruff]\nline-length = 88\n\n"
        "[tool.myapp]\nport = 9090\n"
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cfg = resolve(layout, root_table="tool.myapp")
    assert cfg.port == 9090


def test_a_near_miss_outside_the_root_table_warns(layout: Layout) -> None:
    # The operator's setting is doing nothing, and it is close enough to one of
    # ours that they clearly meant it for us.
    layout.system.write_text("prt = 7070\n\n[myapp]\nport = 9090\n")
    with pytest.warns(UnknownConfigKey, match=r"'prt' is at the top level"):
        cfg = resolve(layout)
    assert cfg.port == 9090


def test_a_file_without_the_root_table_is_read_from_the_top_level(
    layout: Layout,
) -> None:
    layout.system.write_text("port = 9090\n")
    assert resolve(layout).port == 9090


def test_the_root_table_can_be_switched_off(layout: Layout) -> None:
    layout.system.write_text("[myapp]\nport = 9090\n")
    with pytest.warns(UnknownConfigKey, match=r"unknown key 'myapp'"):
        cfg = resolve(layout, root_table=None)
    assert cfg.port == 8080


def test_an_unknown_key_warns_and_is_ignored(layout: Layout) -> None:
    layout.system.write_text('databse = "x"\nport = 9090\n')
    with pytest.warns(UnknownConfigKey) as caught:
        cfg = resolve(layout)
    message = str(caught[0].message)
    assert str(layout.system) in message
    assert "unknown key 'databse'" in message
    assert "did you mean 'database'?" in message
    assert cfg.port == 9090


def test_an_unknown_key_in_a_table_is_named_by_its_path(layout: Layout) -> None:
    layout.system.write_text('[database]\nhostt = "db"\n')
    with pytest.warns(UnknownConfigKey, match=r"unknown key 'database.hostt'"):
        resolve(layout)


def test_strict_unknown_makes_it_an_error(layout: Layout) -> None:
    layout.system.write_text('databse = "x"\n')
    with pytest.raises(ConfigFileError, match=r"unknown key 'databse'"):
        resolve(layout, strict_unknown=True)


def test_a_required_field_set_in_a_file_is_ignored(layout: Layout) -> None:
    # Acceptance 3, the file half: required means positional means CLI-only.
    layout.system.write_text('dataset = "from-file"\n')
    with pytest.warns(IgnoredValue, match=r"'dataset' has no default"):
        cfg = app(layout).resolve(argv=["from-cli"], env={})
    assert cfg.dataset == "from-cli"


def test_a_required_field_set_only_in_a_file_still_fails(layout: Layout) -> None:
    layout.system.write_text('dataset = "from-file"\n')
    with pytest.warns(IgnoredValue), pytest.raises(SourceError, match="required"):
        app(layout).resolve(argv=[], env={})


def test_an_unparseable_file_names_the_path_and_the_position(layout: Layout) -> None:
    layout.system.write_text("port = 9090\nname == 2\n")
    with pytest.raises(ConfigFileError) as caught:
        resolve(layout)
    message = str(caught.value)
    assert message.startswith(f"{layout.system}: ")
    assert "line 2" in message


def test_help_survives_a_file_it_cannot_parse(layout: Layout) -> None:
    # --help is what you reach for when something is already wrong, so a broken
    # file must not be able to withhold it. The defaults fall back to the
    # schema's, and the reason is printed where the operator will see it.
    layout.system.write_text("port = 9090\nname == 2\n")
    with pytest.raises(HelpRequested) as caught:
        resolve(layout, "--help")
    text = caught.value.text
    assert text.startswith("usage: myapp")
    assert "[8080]" in text
    assert f"myapp: {layout.system}: " in text
    assert "line 2" in text
    assert "the defaults shown above are the schema's own" in text


def test_help_survives_a_variable_it_cannot_read(layout: Layout) -> None:
    # The guard is not about files: any source that raises leaves help standing.
    with pytest.raises(HelpRequested) as caught:
        app(layout).resolve(argv=["--help"], env={"MYAPP_TAGS": "[unclosed"})
    assert "is not valid JSON" in caught.value.text


def test_a_broken_file_still_stops_an_ordinary_resolution(layout: Layout) -> None:
    layout.system.write_text("name == 2\n")
    with pytest.raises(ConfigFileError):
        resolve(layout)


def test_a_missing_file_is_not_a_problem(layout: Layout) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert resolve(layout).port == 8080


def test_a_file_that_cannot_be_read_warns_and_is_skipped(layout: Layout) -> None:
    # A directory where a file belongs: unreadable in a way that needs no
    # permissions games, and so behaves the same on every platform and as root.
    layout.system.mkdir()
    layout.user.write_text("port = 7070\n")
    with pytest.warns(UnreadableSource, match=r"cannot be read"):
        cfg = resolve(layout)
    assert cfg.port == 7070


def test_a_drop_in_directory_that_cannot_be_read_warns(layout: Layout) -> None:
    layout.system_dropins.write_text("not a directory\n")
    layout.system.write_text("port = 9090\n")
    with pytest.warns(UnreadableSource, match=r"drop-ins"):
        cfg = resolve(layout)
    assert cfg.port == 9090


def test_a_missing_drop_in_directory_is_not_a_problem(layout: Layout) -> None:
    layout.system.write_text("port = 9090\n")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert resolve(layout).port == 9090


def test_only_toml_files_in_a_drop_in_directory_are_read(layout: Layout) -> None:
    layout.dropin(layout.system_dropins, "10-base.toml", "port = 6000\n")
    layout.dropin(layout.system_dropins, "20-site.conf", "port = nonsense\n")
    layout.dropin(layout.system_dropins, "30-site.toml.rpmsave", "port = nonsense\n")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert resolve(layout).port == 6000


def test_drop_ins_can_be_switched_off(layout: Layout) -> None:
    layout.system.write_text("port = 9090\n")
    layout.dropin(layout.system_dropins, "10-base.toml", "port = 6000\n")
    assert resolve(layout, dropins=False).port == 9090


def test_toml_types_reach_pydantic_as_they_were_written(layout: Layout) -> None:
    # Nothing is stringified on the way in: an integer stays an integer and a
    # datetime stays a datetime. What a value then has to satisfy is pydantic's
    # business, on the merged result, once.
    layout.system.write_text('port = 9090\ntags = ["a", "b"]\n')
    cfg = resolve(layout)
    assert (cfg.port, cfg.tags) == (9090, ["a", "b"])


def test_a_value_pydantic_refuses_names_the_file_it_came_from(
    layout: Layout,
) -> None:
    layout.system.write_text('port = "nine thousand"\n')
    with pytest.raises(ValidationFailed) as caught:
        resolve(layout)
    message = str(caught.value)
    assert "valid integer" in message
    assert str(layout.system) in message


def test_a_table_where_a_value_belongs_is_a_validation_error(layout: Layout) -> None:
    layout.system.write_text("database = 5\n")
    with pytest.raises(ValidationFailed) as caught:
        resolve(layout)
    assert "database" in str(caught.value)
