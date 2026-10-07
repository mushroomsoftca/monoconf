# monoconf

An opinionated universal configuration library for Python 3.11+. You write one
pydantic v2 model, and monoconf turns it into a command-line parser, an environment
reader and a layered TOML file loader, all resolved through one fixed precedence
order. Every resolved value can tell you where it came from.

```
pip install monoconf
```

The only runtime dependency is pydantic.

## Thirty seconds

<!-- source: examples/minimal.py -->
```python
"""The smallest thing that works: a class body and a load()."""

from pathlib import Path

from monoconf import Config


class AppConfig(Config):
    """Serve the widget index."""

    input_path: Path
    output_dir: Path = Path("out/")
    verbose: bool = False


if __name__ == "__main__":
    cfg = AppConfig.load()
    print(f"{cfg.input_path} -> {cfg.output_dir} (verbose={cfg.verbose})")
    print(f"output_dir came from {cfg.origin_of('output_dir').describe()}")
```

That class is already a complete command-line interface:

<!-- output: python examples/minimal.py --help -->
```
usage: minimal.py [-h] [--output-dir PATH] [--verbose] INPUT_PATH

Serve the widget index.

positional arguments:
  INPUT_PATH

options:
  -h, --help         show this help message and exit
  --output-dir PATH  [out]
  --verbose
```

It also reads `APP_OUTPUT_DIR` and `APP_VERBOSE` from the environment, and
`output_dir` and `verbose` from `/etc/app.toml`, `~/.config/app.toml` and the
drop-in directories beside them. Nothing was configured to get all of that: every
name comes from the application name, which monoconf infers here from the class
name. The value in brackets is the default that would actually apply on this
machine, after the files and the environment have been read, and not only the one
the schema declares.

`load()` is for `main()`: on bad input it prints a message and exits with status
2, and for `--help` it prints the help and exits with status 0. `resolve()` does
the same work but raises instead, which suits tests and code that embeds monoconf:

```python
cfg = AppConfig.resolve(argv=["in.csv", "--verbose"], env={})
```

Both take `argv` and `env` explicitly. Leave either out, and monoconf reads the real
command line and environment.

## Precedence

Lowest priority first. The order is fixed, and it cannot be changed.

| # | Source | Example |
| --- | --- | --- |
| 1 | Schema defaults | `port: int = 8080` |
| 2 | System config | `/etc/myapp.toml` |
| 3 | System drop-ins, lexicographic | `/etc/myapp.d/*.toml` |
| 4 | User config | `~/.config/myapp.toml` |
| 5 | User drop-ins, lexicographic | `~/.config/myapp.d/*.toml` |
| 6 | `--config-override` files, in argv order | opt-in |
| 7 | Environment | `MYAPP_PORT=9090` |
| 8 | CLI | `--port 9090` |

Merging is done per leaf value. A drop-in that sets `database.port` leaves the
`database.host` from the base file intact, just as systemd's `.d/` directories do.
Validation runs once, on the merged result, so one error message reports every
problem at once. A value that is not set anywhere is simply absent, and never
counts as null.

## Required means positional

This is the rule most likely to surprise you, so here is the reasoning.

| Field | Command line | Files and environment |
| --- | --- | --- |
| No default | positional argument, in declaration order | ignored, with a warning |
| Has a default | `--kebab-case-name` | accepted |

A field without a default is required, and a required value is always given on the
command line. It is never read from a file or the environment. The alternative
fails quietly. If `input_path` could come from `/etc/myapp.toml`, then a missing
argument on one machine becomes a successful run on another that happens to have
the file, with an input nobody typed. A required value is supposed to be one
the caller gives every time, and a positional argument makes that visible in the
usage line.

A keyword argument is therefore never required. There is no "optional in files
but required on the command line" state, and no way to declare one. When a file or
variable sets a required field, monoconf warns `IgnoredValue` rather than silently
accepting it or failing, because an ignored setting is exactly what the operator
needs to hear about.

Booleans are flags that take no value. A field that defaults to `False` becomes
`--verbose`, and one that defaults to `True` becomes `--no-color`. Lists are
repeated flags, one value each time: `tags: list[str]` becomes `--tag a --tag b`.
A nested model's fields become flags and variables named by their dotted path:
`database.host` is `--database-host` and `MYAPP_DATABASE__HOST`. See
[`examples/nested.py`](https://github.com/mushroomsoftca/monoconf/blob/main/examples/nested.py).

## Custom types take a string

> A type used in a field must be constructible from a single string.

The command line and the environment only have strings to give. monoconf checks
every field's type when the class is defined, so an unusable type fails on import,
not when an operator's input first reaches it:

```
SchemaError: AppConfig.endpoint: type 'Endpoint' cannot be constructed
from a string, so it cannot be set from the command line or the environment.

Either give Endpoint a single-string constructor, or annotate the field:

    endpoint: Annotated[Endpoint, BeforeValidator(Endpoint.parse)] = ...
```

The check passes for an `Enum` with string values, a type annotated with a
pydantic `BeforeValidator`, one that defines `__get_pydantic_core_schema__`, and any
of the standard library types pydantic already reads from a string, such as `Path`,
`datetime`, `UUID`, `Decimal` and `IPv4Address`. A plain class with a string
constructor is not enough on its own, because pydantic cannot build a schema for
it; give it one of those hooks. File values are not turned into strings first: a
TOML datetime reaches your field as a datetime.

## Files and drop-ins

| Platform | System | User |
| --- | --- | --- |
| POSIX | `$XDG_CONFIG_DIRS/<app>.toml` (default `/etc/xdg`), then `/etc/<app>.toml` | `${XDG_CONFIG_HOME:-~/.config}/<app>.toml` |
| Windows | `%PROGRAMDATA%\<app>\config.toml` | `%APPDATA%\<app>\config.toml` |

Every config file has a drop-in directory beside it, with `.d` in place of
`.toml`: `/etc/myapp.d/`, `~/.config/myapp.d/`. Only the `*.toml` files in it are
read. They are sorted by filename, byte by byte, and a later file wins. Use the
systemd numbering convention:

```
/etc/myapp.d/10-base.toml     # shipped by the package
/etc/myapp.d/20-site.toml     # configuration management
/etc/myapp.d/99-local.toml    # the hand edit on this one machine
```

The sort is on bytes, not numbers, so `9-a.toml` sorts *after* `20-b.toml`. Pad
your numbers.

A list in a later file replaces the earlier list rather than adding to it. TOML
has no null, so a file cannot clear a value. To clear a `str | None` setting, use
an empty variable (`MYAPP_PROXY=`) or an empty flag (`--proxy ""`).

A file may put its settings at the top level, or under a table named after the
application (`[myapp]`). The table form is for files shared between applications.
A file that has a `[myapp]` table and also has top-level keys this application
reads is an error, and so is a file that cannot be parsed. Both errors name the
file. A key that matches no field produces a warning, with a suggestion when it is
close to a real field name. With `settings(strict_unknown=True)`, an unknown key is
an error instead.

Every path can be set explicitly, which is also how tests stay hermetic:

```python
model_config = settings(system_config=tmp_path / "myapp.toml", user_config=None)
```

`None` turns a layer off.

## Where did that come from?

With `settings(show_config=True)`, `--show-config` prints the resolved
configuration as TOML, with a comment beside each value saying where it came from:

<!-- output: WIDGETD_DATABASE__HOST=db.internal python examples/full.py --show-config --port 9090 -->
```
# widgetd resolved configuration
# input_path        # positional, CLI-only, not given
port = 9090         # --port
log_level = "info"  # default
# proxy = null      # default
tags = []           # default

[database]
host = "db.internal"  # WIDGETD_DATABASE__HOST
port = 5432           # default
```

The output is a valid config file. If you save it and pass it back with
`--config-override`, you get the same configuration. Required values are commented
out because they are positional, and so are values TOML cannot express, such as
null. Secrets are commented out as well, and shown only as their mask. A field
typed `SecretStr`, `SecretBytes` or `Secret[T]` is never printed anywhere: `--help`
masks it the same way, and an error message calls it "the secret" rather than
quoting it.

The same provenance is available in code. `cfg.origin_of("database.host")`
returns an `Origin`:

- `source` is a `SourceKind`, one per row of the precedence table: `DEFAULT`,
  `SYSTEM_FILE`, `SYSTEM_DROPIN`, `USER_FILE`, `USER_DROPIN`, `OVERRIDE_FILE`,
  `ENV` or `CLI`.
- `path` is the file, for the five file layers, and `None` otherwise.
- `detail` is the variable name, the flag (`--port`, however it was typed) or a
  positional's metavar, and `None` for a default or a file.
- `describe()` is the short answer that `--show-config` and the error messages
  print: the file, the variable, the flag, or `default`.

Only a configuration from `load()` or `resolve()` has provenance. One built
directly, as `AppConfig(input_path=...)`, raises `MonoconfError` when asked.

## Errors

Pydantic's validation errors are written for the model's author. monoconf rewrites
them for the operator: every error is reported, together with where its value came
from:

<!-- output: WIDGETD_PORT=high WIDGETD_LOG_LEVEL=loud python examples/full.py index.csv -->
```
widgetd: invalid configuration

  port: 'high' is not a valid integer
    from WIDGETD_PORT

  log_level: 'loud' is not one of: debug, info, warning, error
    from WIDGETD_LOG_LEVEL

Try 'widgetd --help' for usage.
```

Everything monoconf raises is a subclass of `MonoconfError`:

- `SchemaError` is raised when the class is defined.
- `ConfigFileError` means a file is unreadable, cannot be parsed, or uses both
  forms.
- `SourceError` means a bad variable or a bad argument.
- `ValidationFailed` is raised after merging. Pydantic's original error is
  available as its `__cause__`.
- `OutputRequested` is not a failure. `resolve()` cannot print or exit, so
  `--help` raises `HelpRequested` and `--show-config` raises
  `ShowConfigRequested`. Both subclasses carry the rendered output as `.text` and
  the status `load()` would exit with as `.exit_code`, which is 0.

Because `OutputRequested` is a `MonoconfError` too, code that embeds monoconf and
reports configuration errors should catch it first:

```python
try:
    cfg = AppConfig.resolve()
except OutputRequested as requested:
    print(requested.text)
    raise SystemExit(requested.exit_code) from None
except MonoconfError as error:
    log.error("bad configuration: %s", error)
    raise
```

Warnings are real `Warning` subclasses of `MonoconfWarning`, so `-W error` or
`pytest.warns` handles them like any other warning:

- `UnknownConfigKey`: a file key that matches no field.
- `IgnoredValue`: a file or variable that sets a positional field.
- `UnreadableSource`: a config path that exists but cannot be read.

## Settings

Every argument is optional.
[`examples/full.py`](https://github.com/mushroomsoftca/monoconf/blob/main/examples/full.py)
uses all of them.

| Argument | Default | Purpose |
| --- | --- | --- |
| `app_name` | inferred from the module/class name | Basis for file names, env prefix, root table, prog name |
| `prog` | `sys.argv[0]` basename | Program name in help output |
| `description` | class docstring, else empty | Help header |
| `epilog` | `None` | Help footer |
| `env_prefix` | `f"{app_name.upper()}_"` | Env var prefix; `""` disables the prefix |
| `env_enabled` | `True` | Turn the env source off entirely |
| `root_table` | `app_name` | Optional TOML table |
| `system_config` | platform default | `Path`, a sequence of `Path`s, or `None` to disable |
| `user_config` | platform default | `Path`, a sequence of `Path`s, or `None` to disable |
| `dropins` | `True` | Whether to read the `.d/` sibling of each config path |
| `config_override` | `False` | Enable `--config-override` |
| `show_config` | `False` | Enable `--show-config` |
| `strict_unknown` | `False` | Error rather than warn on unknown file keys |

`Field(...)` is `pydantic.Field` with three additions: `short="p"` for a
one-letter flag, `metavar=`, and `flag=` for a list whose name does not singularise
regularly. `description` doubles as the help text.

## Why not…

**pydantic-settings.** It reads the environment, dotenv files, secrets
directories and, in recent versions, the command line, and it is the obvious
choice if you want those pieces to arrange yourself. It is built to be
configured: you pick your sources, their order and their delimiters. monoconf
removes those choices. The order is fixed, the CLI is part of it, required means
positional, and every value carries its origin. If you want those choices made
for you, use monoconf. If you want to make them yourself, use pydantic-settings.

**dynaconf.** It is broad: many file formats, environments like `[development]`,
secret stores and templating. That breadth is also what makes it hard to answer
"why is `port` 9090?" on a production box. monoconf supports one format and one
order, and it answers that question itself.

**click** (and typer). They are excellent command-line libraries, and
configuration files are left to you. As soon as a flag also needs to come from
`/etc` or an environment variable, you are writing the precedence rules by hand:
reading the files, deciding which layer wins, and keeping track of which values
the operator actually typed. monoconf's parser is generated from the same schema
as the other sources, so there are no rules for you to write.

**argparse alone.** monoconf is argparse underneath, and its help output looks
like any other argparse program's. What argparse cannot do on its own is show the
default that files and the environment have resolved, merge layers, or say where
a value came from.

## Not in 0.1

Subcommands, positional arguments that take several values, fields that cannot
be set from the command line, formats other than TOML, and writing config files
are all deliberately left out. `PLAN.md` explains why for each. monoconf is
pre-1.0: the opinions are stable, and the details may still change.

## Licence

MIT. See [LICENSE](https://github.com/mushroomsoftca/monoconf/blob/main/LICENSE).
[PLAN.md](https://github.com/mushroomsoftca/monoconf/blob/main/PLAN.md) is the design document, and
[AGENTS.md](https://github.com/mushroomsoftca/monoconf/blob/main/AGENTS.md) covers how to work on the repository.
