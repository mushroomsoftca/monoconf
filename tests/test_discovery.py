"""Where monoconf looks for configuration when nobody has told it.

Discovery reads its inputs from the environment it was handed, never from the
process's own, so every test here is hermetic despite naming real variables. The
application name is one no machine will have a file for, which is what keeps
`/etc/<app>.toml` -- the one path that is not env-derived -- out of the way.

Path derivation is platform code, so these tests run per platform on the CI
matrix rather than by pretending to be on another one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from monoconf import Config, SourceKind, settings

APP = "monoconf-under-test"

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths")
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows paths")


class App(Config):
    """Serve the widget index."""

    model_config = settings(app_name=APP, prog="myapp")

    port: int = 8080


def resolve(**env: str) -> App:
    return App.resolve(argv=[], env=env)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@posix_only
def test_the_user_file_is_found_under_xdg_config_home(tmp_path: Path) -> None:
    path = write(tmp_path / f"{APP}.toml", "port = 7070\n")
    cfg = resolve(XDG_CONFIG_HOME=str(tmp_path))
    assert cfg.port == 7070
    assert cfg.origin_of("port").source is SourceKind.USER_FILE
    assert cfg.origin_of("port").path == path


@posix_only
def test_user_drop_ins_are_found_beside_the_user_file(tmp_path: Path) -> None:
    write(tmp_path / f"{APP}.toml", "port = 7070\n")
    dropin = write(tmp_path / f"{APP}.d" / "99-local.toml", "port = 5000\n")
    cfg = resolve(XDG_CONFIG_HOME=str(tmp_path))
    assert cfg.port == 5000
    assert cfg.origin_of("port").path == dropin
    assert cfg.origin_of("port").source is SourceKind.USER_DROPIN


@posix_only
def test_without_xdg_config_home_the_user_file_sits_under_home(
    tmp_path: Path,
) -> None:
    write(tmp_path / ".config" / f"{APP}.toml", "port = 7070\n")
    assert resolve(HOME=str(tmp_path)).port == 7070


@posix_only
def test_a_relative_xdg_config_home_is_ignored(tmp_path: Path) -> None:
    # The spec says to ignore it, and it is right to: a configuration path that
    # moves with the working directory is not a configuration path.
    write(tmp_path / ".config" / f"{APP}.toml", "port = 7070\n")
    cfg = resolve(XDG_CONFIG_HOME="relative/config", HOME=str(tmp_path))
    assert cfg.port == 7070


@posix_only
def test_with_nowhere_to_anchor_a_user_path_there_is_no_user_layer() -> None:
    # No HOME, no XDG_CONFIG_HOME: better no layer at all than a guessed one.
    cfg = resolve()
    assert cfg.port == 8080
    assert cfg.origin_of("port").source is SourceKind.DEFAULT


@posix_only
def test_xdg_config_dirs_are_read_with_the_earliest_winning(
    tmp_path: Path,
) -> None:
    # XDG gives earlier entries the higher precedence, which is the opposite of
    # the order the chain folds in.
    first = write(tmp_path / "a" / f"{APP}.toml", "port = 7070\n")
    write(tmp_path / "b" / f"{APP}.toml", "port = 5000\n")
    cfg = resolve(XDG_CONFIG_DIRS=f"{tmp_path / 'a'}:{tmp_path / 'b'}")
    assert cfg.port == 7070
    assert cfg.origin_of("port").path == first
    assert cfg.origin_of("port").source is SourceKind.SYSTEM_FILE


@posix_only
def test_a_relative_entry_in_xdg_config_dirs_is_ignored(tmp_path: Path) -> None:
    write(tmp_path / f"{APP}.toml", "port = 7070\n")
    assert resolve(XDG_CONFIG_DIRS=f"relative:{tmp_path}").port == 7070


@posix_only
def test_system_drop_ins_are_found_beside_a_system_file(tmp_path: Path) -> None:
    write(tmp_path / f"{APP}.toml", "port = 7070\n")
    write(tmp_path / f"{APP}.d" / "20-site.toml", "port = 6000\n")
    cfg = resolve(XDG_CONFIG_DIRS=str(tmp_path))
    assert cfg.port == 6000
    assert cfg.origin_of("port").source is SourceKind.SYSTEM_DROPIN


@posix_only
def test_the_user_layer_beats_the_system_layer(tmp_path: Path) -> None:
    write(tmp_path / "system" / f"{APP}.toml", "port = 7070\n")
    write(tmp_path / "user" / f"{APP}.toml", "port = 5000\n")
    cfg = resolve(
        XDG_CONFIG_DIRS=str(tmp_path / "system"),
        XDG_CONFIG_HOME=str(tmp_path / "user"),
    )
    assert cfg.port == 5000
    assert cfg.origin_of("port").source is SourceKind.USER_FILE


@windows_only
def test_the_user_file_is_found_under_appdata(tmp_path: Path) -> None:
    path = write(tmp_path / APP / "config.toml", "port = 7070\n")
    cfg = resolve(APPDATA=str(tmp_path))
    assert cfg.port == 7070
    assert cfg.origin_of("port").path == path
    assert cfg.origin_of("port").source is SourceKind.USER_FILE


@windows_only
def test_the_system_file_is_found_under_programdata(tmp_path: Path) -> None:
    write(tmp_path / APP / "config.toml", "port = 7070\n")
    cfg = resolve(PROGRAMDATA=str(tmp_path))
    assert cfg.port == 7070
    assert cfg.origin_of("port").source is SourceKind.SYSTEM_FILE


@windows_only
def test_windows_drop_ins_sit_beside_the_file(tmp_path: Path) -> None:
    write(tmp_path / APP / "config.toml", "port = 7070\n")
    write(tmp_path / APP / "config.d" / "99-local.toml", "port = 5000\n")
    assert resolve(APPDATA=str(tmp_path)).port == 5000


@windows_only
def test_without_appdata_there_is_no_user_layer() -> None:
    assert resolve().origin_of("port").source is SourceKind.DEFAULT
