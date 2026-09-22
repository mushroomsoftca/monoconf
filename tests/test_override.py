"""Layer 6: the files named on the command line, and the variable that can name
them instead.

Off unless `settings(config_override=True)`. It is the one layer an operator
points at directly, which is why a path that is not there is an error here and a
missing file everywhere else is not.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from monoconf import (
    Config,
    ConfigFileError,
    HelpRequested,
    SourceError,
    SourceKind,
    settings,
)


class Off(Config):
    """Serve the widget index."""

    model_config = settings(
        app_name="myapp", prog="myapp", system_config=None, user_config=None
    )

    port: int = 8080


class App(Off):
    model_config = settings(
        app_name="myapp",
        prog="myapp",
        system_config=None,
        user_config=None,
        config_override=True,
    )


def write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_a_named_file_is_read(tmp_path: Path) -> None:
    path = write(tmp_path / "local.toml", "port = 7070\n")
    cfg = App.resolve(argv=["--config-override", str(path)], env={})
    assert cfg.port == 7070
    assert cfg.origin_of("port").source is SourceKind.OVERRIDE_FILE
    assert cfg.origin_of("port").path == path


def test_the_last_named_file_wins(tmp_path: Path) -> None:
    first = write(tmp_path / "a.toml", "port = 7070\n")
    second = write(tmp_path / "b.toml", "port = 5000\n")
    cfg = App.resolve(
        argv=["--config-override", str(first), "--config-override", str(second)],
        env={},
    )
    assert cfg.port == 5000
    assert cfg.origin_of("port").path == second


def test_it_stacks_on_the_discovered_files_rather_than_replacing_them(
    tmp_path: Path,
) -> None:
    discovered = write(tmp_path / "user.toml", 'port = 7070\ntags = ["a"]\n')
    named = write(tmp_path / "local.toml", "port = 5000\n")

    class Stacked(App):
        model_config = settings(
            app_name="myapp",
            prog="myapp",
            system_config=None,
            user_config=discovered,
            config_override=True,
        )

        tags: list[str] = []

    cfg = Stacked.resolve(argv=["--config-override", str(named)], env={})
    assert cfg.port == 5000
    # The discovered file is still in the chain; it was overridden, not replaced.
    assert cfg.tags == ["a"]
    assert cfg.origin_of("tags").path == discovered


def test_it_sits_above_a_user_drop_in(tmp_path: Path) -> None:
    user = tmp_path / "user.toml"
    user.write_text("port = 7070\n")
    (tmp_path / "user.d").mkdir()
    (tmp_path / "user.d" / "99-local.toml").write_text("port = 6000\n")
    named = write(tmp_path / "named.toml", "port = 5000\n")

    class Above(App):
        model_config = settings(
            app_name="myapp",
            prog="myapp",
            system_config=None,
            user_config=user,
            config_override=True,
        )

    cfg = Above.resolve(argv=["--config-override", str(named)], env={})
    assert cfg.port == 5000


def test_it_sits_below_the_environment(tmp_path: Path) -> None:
    named = write(tmp_path / "local.toml", "port = 5000\n")
    cfg = App.resolve(
        argv=["--config-override", str(named)], env={"MYAPP_PORT": "9090"}
    )
    assert cfg.port == 9090
    assert cfg.origin_of("port").source is SourceKind.ENV


def test_a_path_that_is_not_there_is_an_error(tmp_path: Path) -> None:
    missing = tmp_path / "typo.toml"
    with pytest.raises(ConfigFileError) as caught:
        App.resolve(argv=["--config-override", str(missing)], env={})
    message = str(caught.value)
    assert str(missing) in message
    assert "no such file" in message
    assert "--config-override" in message


def test_a_directory_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigFileError, match=r"is a directory"):
        App.resolve(argv=["--config-override", str(tmp_path)], env={})


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges")
def test_a_named_file_that_cannot_be_read_is_an_error(tmp_path: Path) -> None:
    # A discovered file like this warns and is skipped. A named one cannot be:
    # the operator is entitled to know the file they asked for did not apply.
    loop = tmp_path / "loop.toml"
    loop.symlink_to(loop)
    with pytest.raises(ConfigFileError) as caught:
        App.resolve(argv=["--config-override", str(loop)], env={})
    assert "--config-override" in str(caught.value)


def test_the_flag_does_not_exist_unless_it_is_switched_on(tmp_path: Path) -> None:
    path = write(tmp_path / "local.toml", "port = 7070\n")
    with pytest.raises(SourceError, match=r"unrecognized arguments"):
        Off.resolve(argv=["--config-override", str(path)], env={})


def test_the_variable_names_files_too(tmp_path: Path) -> None:
    first = write(tmp_path / "a.toml", "port = 7070\n")
    second = write(tmp_path / "b.toml", "port = 5000\n")
    value = os.pathsep.join([str(first), str(second)])
    cfg = App.resolve(argv=[], env={"MYAPP_CONFIG_OVERRIDE": value})
    assert cfg.port == 5000
    assert cfg.origin_of("port").path == second
    assert cfg.origin_of("port").source is SourceKind.OVERRIDE_FILE


def test_the_flag_replaces_the_variable(tmp_path: Path) -> None:
    # Not a stack: a list of files is a value, and a value from the command line
    # beats one from the environment whole.
    from_env = write(tmp_path / "env.toml", 'port = 7070\ntags = ["a"]\n')
    from_cli = write(tmp_path / "cli.toml", "port = 5000\n")

    class Tagged(App):
        model_config = settings(
            app_name="myapp",
            prog="myapp",
            system_config=None,
            user_config=None,
            config_override=True,
        )

        tags: list[str] = []

    cfg = Tagged.resolve(
        argv=["--config-override", str(from_cli)],
        env={"MYAPP_CONFIG_OVERRIDE": str(from_env)},
    )
    assert cfg.port == 5000
    assert cfg.tags == []


def test_the_variable_is_ignored_when_the_feature_is_off(tmp_path: Path) -> None:
    path = write(tmp_path / "local.toml", "port = 7070\n")
    cfg = Off.resolve(argv=[], env={"MYAPP_CONFIG_OVERRIDE": str(path)})
    assert cfg.port == 8080


def test_the_variable_is_ignored_when_the_environment_is_off(tmp_path: Path) -> None:
    path = write(tmp_path / "local.toml", "port = 7070\n")

    class NoEnv(App):
        model_config = settings(
            app_name="myapp",
            prog="myapp",
            system_config=None,
            user_config=None,
            config_override=True,
            env_enabled=False,
        )

    cfg = NoEnv.resolve(argv=[], env={"MYAPP_CONFIG_OVERRIDE": str(path)})
    assert cfg.port == 8080


def test_an_empty_variable_names_nothing(tmp_path: Path) -> None:
    assert App.resolve(argv=[], env={"MYAPP_CONFIG_OVERRIDE": ""}).port == 8080


def test_the_flag_appears_in_help() -> None:
    with pytest.raises(HelpRequested) as caught:
        App.resolve(argv=["--help"], env={})
    assert "--config-override PATH" in caught.value.text


def test_the_flag_is_absent_from_help_when_it_is_off() -> None:
    with pytest.raises(HelpRequested) as caught:
        Off.resolve(argv=["--help"], env={})
    assert "--config-override" not in caught.value.text


def test_a_named_file_shapes_the_defaults_help_shows(tmp_path: Path) -> None:
    named = write(tmp_path / "local.toml", "port = 5000\n")
    with pytest.raises(HelpRequested) as caught:
        App.resolve(argv=["--config-override", str(named), "--help"], env={})
    assert "[5000]" in caught.value.text
