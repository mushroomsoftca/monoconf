"""Layers 2 to 5: the configuration files, and the drop-ins beside them.

Four layers, one source. They share every code path there is -- where a file
lives, how it is read, which table its settings sit in, how its keys are matched
against the schema -- so splitting them into four modules would be four copies of
this one. The order between them lives here; what leaves is a single layer, and
the resolver stays a flat fold over the chain.

Values read from a file are handed on exactly as TOML expressed them. Only the
command line and the environment deal in strings, so a TOML `port = "8080"` is
correctly a type error rather than a number that got away with it.
"""

from __future__ import annotations

import difflib
import os
import sys
import tomllib
import warnings
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from monoconf.errors import (
    ConfigFileError,
    IgnoredValue,
    UnknownConfigKey,
    UnreadableSource,
)
from monoconf.merge import (
    Layer,
    Origin,
    SourceKind,
    Values,
    merge,
    origins_for,
    set_leaf,
)
from monoconf.schema import KeyTree, Spec
from monoconf.settings import ConfigPaths, Settings
from monoconf.sources import Context

__all__ = ["collect"]

# A key inside the schema's own table is monoconf's business, so any plausible
# match is worth suggesting. A key outside it may well belong to another
# application in a shared file, where only a near-certain match is worth a word.
_CLOSE = 0.6
_VERY_CLOSE = 0.8


@dataclass(frozen=True, slots=True)
class _Site:
    """One configuration file, and the drop-in directory beside it."""

    path: Path
    dropins: Path | None


def collect(spec: Spec, ctx: Context) -> Layer:
    """Collect every configuration file, lowest priority first."""
    settings = spec.settings
    tree = spec.tree()
    app = settings.app_name
    system = _sites(
        settings.system_config,
        _system_paths(app, ctx.env),
        dropins=settings.dropins,
    )
    user = _sites(
        settings.user_config,
        _user_paths(app, ctx.env),
        dropins=settings.dropins,
    )
    overrides, named = _overrides(settings, ctx)
    # Every system file, then every system drop-in: a .d/ sits above all of the
    # files at its own level, not immediately above its own.
    return merge(
        [
            *_read_all(spec, tree, _paths(system), SourceKind.SYSTEM_FILE),
            *_read_all(spec, tree, _dropins(system), SourceKind.SYSTEM_DROPIN),
            *_read_all(spec, tree, _paths(user), SourceKind.USER_FILE),
            *_read_all(spec, tree, _dropins(user), SourceKind.USER_DROPIN),
            *_read_all(spec, tree, overrides, SourceKind.OVERRIDE_FILE, named=named),
        ]
    )


def _sites(
    paths: ConfigPaths, derived: tuple[Path, ...], *, dropins: bool
) -> tuple[_Site, ...]:
    """The files one layer covers, in ascending priority order.

    A layer is either pinned to explicit paths, switched off, or left to the
    platform. Pinning is also what makes a test hermetic, which is why it is part
    of the public `settings()` surface rather than a hook.
    """
    if paths is None:
        return ()
    chosen = paths if isinstance(paths, tuple) else derived
    # The drop-in directory is the file's own name with .toml swapped for .d:
    # /etc/myapp.toml -> /etc/myapp.d, %APPDATA%\myapp\config.toml -> config.d.
    return tuple(
        _Site(path, path.with_suffix(".d") if dropins else None) for path in chosen
    )


def _overrides(settings: Settings, ctx: Context) -> tuple[tuple[Path, ...], str | None]:
    """Layer 6: the files named explicitly, in the order they were named.

    The flag replaces the variable rather than stacking on it. A list of files
    is a value like any other, and a value from the command line beats one from
    the environment whole -- monoconf appends to nothing, anywhere.
    """
    if not settings.config_override:
        # A feature that is switched off is switched off, variable included.
        return (), None
    if ctx.overrides:
        return tuple(Path(entry) for entry in ctx.overrides), "--config-override"
    if not settings.env_enabled:
        return (), None
    variable = f"{settings.env_prefix}CONFIG_OVERRIDE"
    # The one place an environment variable feeds a file layer rather than a
    # value, so it is split the way a path list is: ':' here, ';' on Windows.
    entries = [entry for entry in ctx.env.get(variable, "").split(os.pathsep) if entry]
    if not entries:
        return (), None
    return tuple(Path(entry) for entry in entries), variable


def _system_paths(app: str, env: Mapping[str, str]) -> tuple[Path, ...]:
    """Where this platform keeps system-wide configuration, lowest priority first.

    Every input is read from the environment monoconf was handed rather than from
    the process's own, so `resolve(env=...)` decides what discovery can see.
    """
    if sys.platform == "win32":
        return _windows_path(env, "PROGRAMDATA", app)
    # XDG gives earlier entries the higher precedence, and the chain folds lowest
    # first, so the list is walked backwards. /etc comes after all of them: a
    # distribution's default in /etc/xdg has to stay overridable locally.
    return (
        *(Path(directory) / f"{app}.toml" for directory in reversed(_xdg_dirs(env))),
        Path("/etc") / f"{app}.toml",
    )


def _user_paths(app: str, env: Mapping[str, str]) -> tuple[Path, ...]:
    """Where this platform keeps one person's configuration."""
    if sys.platform == "win32":
        return _windows_path(env, "APPDATA", app)
    home = Path(env.get("XDG_CONFIG_HOME", ""))
    if home.is_absolute():
        return (home / f"{app}.toml",)
    # Unset, empty, or relative -- and a relative XDG_CONFIG_HOME is ignored, as
    # the spec requires: a configuration path that moves with the working
    # directory is not a configuration path.
    base = Path(env.get("HOME", ""))
    if not base.is_absolute():
        # Nothing to anchor a user path to. No layer beats a guessed one.
        return ()
    return (base / ".config" / f"{app}.toml",)


def _xdg_dirs(env: Mapping[str, str]) -> tuple[str, ...]:
    raw = env.get("XDG_CONFIG_DIRS", "")
    if not raw:
        # Unset or empty means /etc/xdg, which is the spec's own default.
        return ("/etc/xdg",)
    return tuple(entry for entry in raw.split(":") if Path(entry).is_absolute())


def _windows_path(env: Mapping[str, str], variable: str, app: str) -> tuple[Path, ...]:
    base = Path(env.get(variable, ""))
    if not base.is_absolute():
        return ()
    return (base / app / "config.toml",)


def _paths(sites: Iterable[_Site]) -> Iterator[Path]:
    return (site.path for site in sites)


def _dropins(sites: Iterable[_Site]) -> Iterator[Path]:
    for site in sites:
        if site.dropins is not None:
            yield from _entries(site.dropins)


def _entries(directory: Path) -> list[Path]:
    """The `*.toml` files in a drop-in directory, in the order they apply.

    Sorted by filename rather than by full path, and byte-wise rather than
    numerically, so `9-a.toml` applies after `20-b.toml`. That is what systemd's
    .d/ directories do, and the convention operators already have in their hands.
    """
    try:
        entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
    except FileNotFoundError:
        # A drop-in directory that was never created is the normal case.
        return []
    except OSError as error:
        warnings.warn(
            f"{directory} cannot be read ({error.strerror}), so any drop-ins in it "
            "are ignored. Everything else still applies.",
            UnreadableSource,
            stacklevel=2,
        )
        return []
    # Anything not ending in .toml is ignored in silence: that is what the
    # extension rule is for, and .rpmsave files are not the operator's mistake.
    return [entry for entry in entries if entry.suffix == ".toml" and entry.is_file()]


def _read_all(
    spec: Spec,
    tree: KeyTree,
    paths: Iterable[Path],
    kind: SourceKind,
    *,
    named: str | None = None,
) -> list[Layer]:
    layers = []
    for path in paths:
        layer = _read(spec, tree, path, kind, named=named)
        if layer is not None:
            layers.append(layer)
    return layers


def _read(
    spec: Spec,
    tree: KeyTree,
    path: Path,
    kind: SourceKind,
    *,
    named: str | None = None,
) -> Layer | None:
    """Read one file. `named` means the operator asked for this one by name.

    A discovered file that is missing or unreadable is an ordinary fact about a
    machine. A file someone typed is a promise: if it is not there, the
    configuration they asked for is not the one they would get, and falling back
    to the discovered files would hide a typo behind a working program.
    """
    if named is not None and path.is_dir():
        # Checked rather than left to open(), which raises IsADirectoryError on
        # POSIX and PermissionError on Windows.
        raise ConfigFileError(
            f"{path} is a directory, and {named} names files. Name the files "
            "themselves, or let your shell expand the directory."
        )
    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle, parse_float=float)
    except FileNotFoundError:
        if named is not None:
            raise ConfigFileError(
                f"{path}: no such file. It was named by {named}, and a file named "
                "explicitly has to exist -- a typo must not quietly leave you with "
                "the discovered configuration instead."
            ) from None
        # Most of the chain is absent most of the time, which is the point of
        # having eight layers rather than one.
        return None
    except OSError as error:
        if named is not None:
            raise ConfigFileError(
                f"{path}: {error.strerror}. It was named by {named}, so it is an "
                "error rather than something to work around."
            ) from error
        warnings.warn(
            f"{path} exists but cannot be read ({error.strerror}), so it is "
            "ignored. Everything else still applies.",
            UnreadableSource,
            stacklevel=2,
        )
        return None
    except tomllib.TOMLDecodeError as error:
        # tomllib's message carries the line and column; the file it was reading
        # is the one thing it cannot know.
        raise ConfigFileError(f"{path}: {error}") from error
    values = _values(spec, tree, path, _table(spec, tree, path, document))
    return values, origins_for(values, Origin(kind, path=path))


def _table(
    spec: Spec, tree: KeyTree, path: Path, document: Mapping[str, Any]
) -> Mapping[str, Any]:
    """The table this file's settings live in: the root table, or the top level.

    Decided per file, so a system file may be written one way and a user file the
    other.
    """
    root = spec.settings.root_table
    if root is None:
        return document
    parts = root.split(".")
    table = _lookup(document, parts)
    if table is None:
        return document
    # The head of the root table's own address is not a setting, and the rest of
    # a shared file belongs to whoever else is in it -- which is what the
    # root-table form exists for. Only a top-level key this schema would itself
    # have read leaves the file meaning two things at once.
    outside = [key for key in document if key != parts[0]]
    claimed = [key for key in outside if key in tree]
    if claimed:
        raise ConfigFileError(
            f"{path} is written in both forms: it has a [{root}] table and "
            f"top-level {_names(claimed)} that this schema reads as well. Use one "
            f"or the other -- move the top-level keys under [{root}], or drop the "
            "table."
        )
    _outside_the_root(tree, path, root, outside)
    return table


def _lookup(
    document: Mapping[str, Any], parts: Sequence[str]
) -> Mapping[str, Any] | None:
    node: Any = document
    for part in parts:
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    # A root table that is not a table is not a root table. The top-level form
    # then applies, and the key is reported as the unknown key it is.
    return node if isinstance(node, Mapping) else None


def _outside_the_root(
    tree: KeyTree, path: Path, root: str, outside: Iterable[str]
) -> None:
    """Warn about a top-level key that looks like a setting in the wrong place.

    A file in root-table form is usually shared, so most of what sits outside the
    table belongs to somebody else and remarking on it would be noise the operator
    cannot act on. A key that all but spells one of this schema's own is the
    exception: that is a mistake, and the setting behind it is doing nothing.

    `strict_unknown` deliberately does not escalate this. It governs what this
    application's own table holds; a key outside that table was never ours.
    """
    for key in outside:
        match = _closest(key, tree, _VERY_CLOSE)
        if match is None:
            continue
        warnings.warn(
            f"{path}: '{key}' is at the top level, but this file's settings are "
            f"read from [{root}], so it is ignored. Did you mean '{match}' inside "
            f"[{root}]?",
            UnknownConfigKey,
            stacklevel=2,
        )


def _values(spec: Spec, tree: KeyTree, path: Path, table: Mapping[str, Any]) -> Values:
    values: Values = {}
    _walk(spec, tree, path, table, "", values)
    return values


def _walk(
    spec: Spec,
    tree: KeyTree,
    path: Path,
    table: Mapping[str, Any],
    prefix: str,
    values: Values,
) -> None:
    """Read one table against the schema's names at the same level."""
    for key, value in table.items():
        dotted = f"{prefix}{key}"
        node = tree.get(key)
        if node is None:
            _unknown(spec, tree, path, dotted, key)
        elif isinstance(node, dict):
            if isinstance(value, dict):
                _walk(spec, node, path, value, f"{dotted}.", values)
            else:
                # A model handed something that is not a table. pydantic says what
                # is wrong with it more precisely than a guess here could.
                set_leaf(values, dotted, value)
        elif node.required:
            warnings.warn(
                f"{path}: '{dotted}' has no default, which makes it a positional "
                "argument and settable only on the command line. The setting is "
                "ignored.",
                IgnoredValue,
                stacklevel=2,
            )
        else:
            set_leaf(values, dotted, value)


def _unknown(spec: Spec, tree: KeyTree, path: Path, dotted: str, key: str) -> None:
    match = _closest(key, tree, _CLOSE)
    hint = f" (did you mean '{match}'?)" if match is not None else ""
    message = f"{path}: unknown key '{dotted}'{hint}"
    if spec.settings.strict_unknown:
        raise ConfigFileError(
            f"{message}. settings(strict_unknown=True) makes an unrecognised key "
            "an error rather than a warning."
        )
    # Warned rather than refused by default, because a drop-in shipped for a newer
    # version of the application routinely carries keys this one has never heard of.
    warnings.warn(f"{message}. It is ignored.", UnknownConfigKey, stacklevel=2)


def _closest(name: str, tree: KeyTree, cutoff: float) -> str | None:
    matches = difflib.get_close_matches(name, tree, n=1, cutoff=cutoff)
    return matches[0] if matches else None


def _names(keys: Sequence[str]) -> str:
    quoted = [f"'{key}'" for key in keys]
    if len(quoted) == 1:
        return f"key {quoted[0]}"
    return f"keys {', '.join(quoted[:-1])} and {quoted[-1]}"
