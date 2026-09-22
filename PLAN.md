# monoconf — implementation plan

2026-09-17

## Overview

**monoconf** is an opinionated universal configuration library for Python 3.11+. One schema definition — a pydantic v2 model — produces a CLI parser, an environment reader, and a layered TOML file loader, resolved through a single fixed precedence order.

The name is deliberate: one config, one way to declare it, one resolution order. Where other libraries offer knobs, monoconf picks a behaviour and documents it.

### Design axioms

These are load-bearing. Do not add options that soften them.

1. **Requiredness is structural.** A field without a default is required and is a *positional* CLI argument, settable only from the CLI. A field with a default is optional everywhere. There is no third state and no `required=True` escape hatch.
2. **A keyword argument is never required.** This follows from (1): config can always be fully resolved given the positionals alone.
3. **Type hints are the source of truth.** Coercion, validation and help text all derive from the annotation. No parallel schema language.
4. **Precedence is fixed.** The source chain is not user-reorderable.
5. **Every resolved value knows where it came from.** Provenance is tracked for every field, always — not behind a debug flag.
6. **Sparse input still yields a good CLI.** `--help` and `-h` produce standard, readable output with zero maintainer-supplied metadata.

### Dependencies

- `pydantic>=2.8` — the only runtime dependency.
- `tomllib` (stdlib, 3.11+), `argparse`, `os`, `pathlib`, `sys`.

Explicitly **not** using `pydantic-settings`. Its value is env parsing plus a source-ordering hook; monoconf overrides source order, the CLI model, file discovery and merge semantics, so almost nothing of it would remain. The env source is roughly 60 lines written directly.

No TOML dependency is needed. "TOML without a root table" is not a dialect — top-level keys are ordinary TOML and `tomllib` parses them fine. `tomllib` is read-only, which is acceptable because monoconf never writes config.

### Scope

**In:** TOML files, environment variables, CLI, nested models one level deep, custom types, provenance tracking, opt-in `--show-config` and `--config-override`.

**Out for v1:** subcommands, variadic positionals, config writing, formats other than TOML, secret backends, per-field CLI suppression, async loading.

## Public interface

The entire learnable surface is four names: `Config`, `settings`, `Field`, and `load`.

```python
from pathlib import Path
from monoconf import Config, settings, Field

class AppConfig(Config):
    model_config = settings(
        app_name="myapp",
        description="Serve the widget index.",
        epilog="See https://example.com/docs for the full config reference.",
    )

    # No default -> required -> positional, CLI-only
    input_path: Path
    dataset: str

    # Defaulted -> optional everywhere
    output_dir: Path = Path("out/")               # --output-dir
    log_level: LogLevel = LogLevel.INFO            # --log-level
    verbose: bool = False                          # --verbose
    color: bool = True                             # --no-color
    retries: int = Field(3, description="Retry count on transient failure.")
    tags: list[str] = []                           # --tag repeated
    database: DatabaseConfig = DatabaseConfig()    # --database-host, etc.

cfg = AppConfig.load()
```

### `settings(...)`

Returns a `SettingsConfigDict` (a plain `TypedDict` extending pydantic's `ConfigDict`), so `model_config = settings(...)` stays compatible with pydantic. Every argument is optional.

| Argument | Default | Purpose |
| --- | --- | --- |
| `app_name` | inferred from the module/class name | Basis for file names, env prefix, root table, prog name |
| `prog` | `sys.argv[0]` basename | Program name in help output |
| `description` | class docstring, else empty | Help header |
| `epilog` | `None` | Help footer |
| `env_prefix` | `f"{app_name.upper()}_"` | Env var prefix; `""` disables the prefix |
| `env_enabled` | `True` | Turn the env source off entirely |
| `root_table` | `app_name` | Optional TOML table; see File discovery |
| `system_config` | platform default | `Path`, a sequence of `Path`s, or `None` to disable |
| `user_config` | platform default | `Path`, a sequence of `Path`s, or `None` to disable |
| `dropins` | `True` | Whether to read the `.d/` sibling of each config path |
| `config_override` | `False` | Enable `--config-override` |
| `show_config` | `False` | Enable `--show-config` |
| `strict_unknown` | `False` | Error rather than warn on unknown file keys |

Everything is derivable from `app_name` alone. A maintainer who supplies nothing but a class body still gets a working CLI with sensible paths.

### `Field(...)`

A thin wrapper over `pydantic.Field` adding three CLI-specific keys:

```python
port: int = Field(8080, description="Listen port.", short="p", metavar="PORT")
```

- `short` — opt-in single-character flag. Never auto-derived; collisions are a definition-time error. `-h` is reserved.
- `metavar` — placeholder in help output; defaults to the field name uppercased.
- `flag` — the long flag name, without the leading `--`. The escape hatch for a list
  field whose plural does not singularise regularly; rarely needed otherwise.

`description` is pydantic's own key and doubles as help text, so maintainers who already document their models get CLI help for free.

### `load()` vs `resolve()`

Two entry points, differing only in failure behaviour, sharing one engine.

```python
cfg = AppConfig.load()                      # CLI semantics
cfg = AppConfig.resolve(argv=[...], env={}) # library/test semantics
```

- **`load(argv=None, env=None)`** behaves like a CLI tool: on bad input it writes a message to stderr and raises `SystemExit(2)`; `--help` prints and exits 0. Use in `main()`.
- **`resolve(argv=None, env=None)`** raises `MonoconfError` subclasses instead, and treats `--help` as a `HelpRequested` exception carrying the rendered text. Use in tests and when embedding.

Both accept explicit `argv` and `env` so nothing reads global state implicitly during tests. `env=None` means `os.environ`; `env={}` means an empty environment, not a default.

### Instance surface

The returned object is an ordinary frozen pydantic model, plus:

```python
cfg.port                      # 8080
cfg.__monoconf_origin__       # mapping: dotted field name -> Origin
cfg.origin_of("port")         # Origin(source=SourceKind.FILE, path=..., detail=...)
```

Models are frozen by default (`model_config` sets `frozen=True`) — a resolved config is a fact about this process, not a mutable bag. `settings(frozen=False)` is available for anyone who disagrees.

## Schema rules

All of this is enforced in `__init_subclass__` / a metaclass hook, at class-definition time. A malformed schema must fail on import, never at `load()`.

### Positional vs keyword

| Field | CLI form | Other sources |
| --- | --- | --- |
| No default | positional, in declaration order | rejected |
| Has a default | `--kebab-case-name` | accepted |

Field names convert to flags by replacing `_` with `-`. Nested fields join with `-`: `database.host` → `--database-host`.

Required fields are excluded from file and env sources entirely. If a file or env var supplies a key matching a required field, that is a **warning**, not a silent acceptance and not an error — it tells the operator their setting is being ignored, which is the failure they actually care about.

### Booleans

Presence-only. Never `--verbose true`.

- Default `False` → `--verbose` sets `True`.
- Default `True` → `--no-color` sets `False`. Only the negative form is generated.
- A bool field named `no_*` is a definition-time error (would generate `--no-no-cache`).

In files and env, bools are ordinary values: `verbose = true`, `MYAPP_VERBOSE=1`. Env accepts `1/0`, `true/false`, `yes/no`, `on/off`, case-insensitive; anything else is an error naming the variable and the accepted forms.

### Lists

Repeated flag, singularised where the plural is regular: `tags: list[str]` → `--tag a --tag b`. Irregular plurals fall back to the field name; `Field(flag="...")` overrides.

Lists **replace** wholesale, never append, at every layer. A later source's list is the whole list. This matches how operators expect config files to behave and avoids the "how do I clear this" problem entirely.

Env uses a JSON array if the value starts with `[`, otherwise comma-split: `MYAPP_TAGS='["a,b", "c"]'` or `MYAPP_TAGS=a,b`.

### Nesting

One level is supported and tested. Deeper nesting is not blocked, but the generated flags get unwieldy fast — the docs should say plainly that if you need two levels, you probably want a second config file or a second `Config` class.

```python
class DatabaseConfig(BaseModel):
    host: str = "localhost"
    port: int = 5432

class AppConfig(Config):
    database: DatabaseConfig = DatabaseConfig()
```

- `--database-host`, `--database-port`
- `MYAPP_DATABASE__HOST` — **double underscore** as the nesting separator, unambiguously
- `[database]` table in TOML (or `[myapp.database]` under a root table)

Every field in a nested model must have a default. A required field inside a nested model is a definition-time error: it cannot be a positional (positionals are flat and ordered) and it cannot come from anywhere else.

The nested model must itself be a `BaseModel`, not a `Config` — `Config` carries CLI/settings machinery that is meaningless as a child.

### Definition-time errors

Raise `SchemaError` with the offending class, field and a suggested fix for each of:

1. **Flag collision.** Two fields producing the same flag, e.g. `database: DatabaseConfig` with a `host` field alongside a top-level `database_host: str`. Both want `--database-host`.
2. **Env collision.** Two fields producing the same env var name after prefixing and nesting.
3. **Short-flag collision**, including anything claiming `-h`.
4. **Reserved names.** A field named `help`, `config_override`, or `show_config` (the latter two only when enabled). Error regardless of whether the feature is on, so turning a feature on later can't break a working schema.
5. **Required field in a nested model.**
6. **Required container or model type at top level** — `list`, `dict`, `set`, `tuple`, or a `BaseModel` with no default. Positionals are scalars.
7. **`Optional[X]` with no default.** This is required-but-nullable, which is almost always a mistake. Error with: "`Optional[X]` without a default is still required and becomes a positional. Did you mean `X | None = None`?"
8. **Non-string-constructible required type** — see Errors below.
9. **`*_no` / `no_*` boolean names.**

### Custom types

Any type reachable from the CLI or env is handed a `str`. The contract, stated loudly in the docs:

> A type used in a non-nested field must be constructible from a single string.

Satisfied by anything with a `__init__(self, value: str)`, a pydantic `BeforeValidator`, `__get_pydantic_core_schema__`, an `Enum` with string values, or the stdlib types monoconf special-cases (`Path`, `datetime`, `date`, `time`, `timedelta`, `UUID`, `IPv4Address`, `IPv6Address`, `Decimal`, `re.Pattern`).

At class-definition time, probe each field's type by attempting a string-schema validation against a sentinel. If pydantic reports the type cannot accept a string, raise `SchemaError` immediately with the field name, the type, and a pointer to `Annotated[T, BeforeValidator(...)]`. Catching this on import rather than on a user's malformed input is most of the value.

## Source precedence and merging

### The chain

Lowest priority first. Fixed; not reorderable.

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

### Merge semantics

Each source produces a sparse `dict[str, Any]` containing only keys it actually set. These are folded left to right into a single dict, then handed to pydantic once for validation. Validating once at the end — rather than per layer — means partial nested models merge correctly and a single `ValidationError` reports everything.

**Deep merge per leaf.** A drop-in that sets `database.port` leaves `database.host` from the base file intact. This matches systemd's `.d/` behaviour, which is the convention operators already know.

**Lists replace.** A list value at any layer is the whole list. No append, no reset sentinel.

**Absent is not null.** A source omitting a key must not contribute `None` for it. This is why sources produce sparse dicts rather than full ones — a `None` from the env layer would otherwise clobber a file value.

**Explicit null.** A TOML file cannot express null; TOML has no null type. If a field is `X | None` and the operator wants to clear a lower layer's value, the only route is env (`MYAPP_PROXY=` — empty string means null for optional fields) or CLI (`--proxy ""`). Document this; it is a real limitation of the format, not of monoconf.

### Drop-in ordering

Within a `.d/` directory: files matching `*.toml`, sorted by byte-wise filename comparison (`sorted()` on the name, not the full path). Later files win. Non-`.toml` files are ignored silently — that is what the extension filter is for.

The usual `10-base.toml`, `20-site.toml`, `99-local.toml` convention works and should appear in the README.

A drop-in directory that does not exist is fine. A drop-in directory that exists but is unreadable is a warning, not an error — a permissions problem on `/etc/myapp.d` shouldn't take down a service that has everything it needs from elsewhere.

### `--config-override`

Off by default. When `settings(config_override=True)`:

```
myapp --config-override ./local.toml --config-override ./ci.toml input.csv
```

- Repeatable; later files win among themselves.
- **Files only.** A directory argument is an error. If someone wants a directory merged, they can glob it in their shell.
- **Nonexistent path is a hard error.** Explicitly named means explicitly required; a typo must not silently fall back.
- Sits at layer 6, above all discovered files, below env and CLI. It does not *replace* the discovered files — it stacks on top of them.
- Also readable as `MYAPP_CONFIG_OVERRIDE`, colon-separated (semicolon on Windows), which is the one case where an env var feeds a *file* layer rather than a value.

### Two-pass parsing

`--config-override` changes which files exist before the rest of the config can be resolved, so parsing runs twice:

1. **Pass one:** a bare `ArgumentParser` with `add_help=False` and only the meta-arguments (`--config-override`, `--show-config`, `-h/--help`), using `parse_known_args`. This determines the file set and whether to short-circuit to help.
2. **Pass two:** the full parser, built with resolved values available so `--help` can display effective defaults.

This is also what makes `--help` work before positional validation: help is detected in pass one and exits before anything can complain about missing arguments.

## File discovery and TOML handling

### Default paths

Platform-detected at runtime, overridable per-path via `settings()`.

**POSIX**, honouring the XDG Base Directory spec:

```
system:  $XDG_CONFIG_DIRS split on ':', each <dir>/<app>.toml   (default /etc/xdg)
         then /etc/<app>.toml
user:    ${XDG_CONFIG_HOME:-~/.config}/<app>.toml
```

Each of those has a sibling drop-in directory at the same path with `.d` replacing `.toml`: `/etc/myapp.d/`, `~/.config/myapp.d/`.

`/etc/<app>.toml` is read *after* the `XDG_CONFIG_DIRS` entries, so a distro-packaged default in `/etc/xdg` is overridable by a local `/etc` file. In practice most apps only ever see `/etc`.

**Windows:**

```
system:  %PROGRAMDATA%\<app>\config.toml   + <app>\config.d\
user:    %APPDATA%\<app>\config.toml       + <app>\config.d\
```

Both are overridden by `settings(system_config=..., user_config=...)`, which accept a `Path`, a sequence of `Path`s, or `None` to disable that layer. Passing explicit paths also makes the platform irrelevant, which is how tests should pin behaviour.

### Extension

`.toml` required. An extensionless `/etc/myapp` is not searched. One rule, no fallback chain, no surprises about which file won.

### Root table

A config file may be written either way:

```toml
# top-level form
port = 8080

[database]
host = "db.internal"
```

```toml
# root-table form, for shared/multi-app files
[myapp]
port = 8080

[myapp.database]
host = "db.internal"
```

The root table name defaults to `app_name` and needs no maintainer involvement. Resolution per file:

- Root table present, no other top-level keys → use its contents.
- No root table → use the top level.
- **Both** → `ConfigFileError` naming the file and both forms, with the message: the file has a `[myapp]` table *and* top-level keys; use one or the other. This is your requested throw, and it neatly sidesteps the ambiguity where the schema also has a field named `myapp`.

The check is per file, so a system file may use one form and a user file the other.

### Parsing

`tomllib.load(f, parse_float=float)` in binary mode. Wrap `TOMLDecodeError` into `ConfigFileError` carrying the path plus tomllib's line/column, so the operator gets `/etc/myapp.d/20-site.toml:14: Expected '=' after a key`.

TOML's native types (datetime, date, time, arrays, inline tables) pass through to pydantic unchanged — file values are *not* stringified, where CLI and env values arrive as strings and have nowhere else to be coerced. What happens next is the same for all of them: one lax validation pass over the merged result. A TOML `port = "8080"` is therefore accepted exactly as `MYAPP_PORT=8080` is. See *Lax coercion is the whole of it* under Amendments.

### Unknown keys

Warn and ignore, via `warnings.warn(UnknownConfigKey, ...)` — a real warning category so applications can filter, escalate, or route it to logging.

Message names the file, the key path, and the closest matching field by edit distance when one is within threshold: `/etc/myapp.toml: unknown key 'databse' (did you mean 'database'?)`.

`settings(strict_unknown=True)` escalates to an error for anyone who wants it. Default stays warn, because third-party drop-ins routinely carry keys for newer versions.

## CLI generation

Built on `argparse` — standard formatting, zero dependencies, familiar output — with a custom `HelpFormatter` subclass to handle the parts argparse can't express.

### Field-to-argument mapping

| Schema | argparse call |
| --- | --- |
| `x: T` (required) | `add_argument("x", type=str, metavar="X")` |
| `x: T = d` | `add_argument("--x", type=str, default=SENTINEL)` |
| `x: bool = False` | `add_argument("--x", action="store_true", default=SENTINEL)` |
| `x: bool = True` | `add_argument("--no-x", action="store_false", dest="x", default=SENTINEL)` |
| `x: list[T] = []` | `add_argument("--item", action="append", default=SENTINEL)` |

Every optional argument uses a module-private `SENTINEL` as its argparse default, never the schema default. This is the mechanism by which "the user didn't pass this flag" is distinguished from "the user passed the value that happens to equal the default" — without it, an explicit `--port 8080` would be indistinguishable from an unset flag and would lose to a file setting `port = 9090`. After parsing, drop every `SENTINEL` before merging.

`type=str` throughout. argparse does no conversion; pydantic does all of it in one pass at the end.

### Help output

Standard argparse layout:

```
usage: myapp [-h] [--output-dir OUTPUT_DIR] [--log-level LOG_LEVEL]
             [--verbose] [--no-color] [--retries RETRIES] [--tag TAG]
             INPUT_PATH DATASET

Serve the widget index.

positional arguments:
  INPUT_PATH            Path to the input file.
  DATASET               Dataset name.

options:
  -h, --help            show this help message and exit
  --output-dir PATH     Output directory. [out/]
  --log-level LEVEL     One of: debug, info, warning, error. [info]
  --verbose             Enable verbose logging.
  --no-color            Disable coloured output.
  -p, --retries N       Retry count on transient failure. [3]
  --tag TAG             Tag to apply; repeatable. [none]

See https://example.com/docs for the full config reference.
```

The formatter must:

1. **Show resolved defaults, not schema defaults.** The bracketed value is the value that would be used if the flag were omitted — i.e. after files and env have been applied. A user reading `--port` help on a machine with `/etc/myapp.toml` should see `[9090]`, not `[8080]`. This requires help rendering to happen after file/env resolution, which the two-pass parser already provides.
2. **Enumerate `Enum` and `Literal` choices** in the help text automatically. Free metadata from the type system.
3. **Derive `metavar`** from the type when not given: `Path` → `PATH`, `int` → `N`, `Enum` → `LEVEL`-style from the enum name, else the field name uppercased.
4. **Mark repeatable** list options.
5. **Fall back gracefully** with no `description`: usage line, argument list, done. Still standard, still readable.

### `--show-config`

Opt-in via `settings(show_config=True)`, to keep CLIs clean by default. Prints the fully resolved config as valid TOML with provenance comments, then exits 0:

```toml
# myapp resolved configuration
port = 9090          # /etc/myapp.d/20-site.toml
output_dir = "out/"  # default
verbose = true       # MYAPP_VERBOSE

[database]
host = "db.internal" # ~/.config/myapp.toml
port = 5432          # default
```

The output round-trips: strip the comments and it is a valid config file. This makes it the answer to both "why is this value what it is" and "how do I pin the current state."

It runs *after* full resolution, so it reflects the actual merged result including any flags on the same command line.

### `--version`

Not generated. Maintainers who want it can add it themselves; monoconf has no way to guess a version string and shouldn't pretend to.

### Reserved

`-h` / `--help` always. `--config-override` and `--show-config` are reserved names whether or not enabled, so toggling them on later cannot collide with an existing field.

## Errors, provenance and diagnostics

With eight layers, "why is `port` 9090?" is the support question this library lives or dies on. Provenance is not a feature bolted on later; it is threaded through from the first commit.

### Origin tracking

Every source returns `(values, origins)` rather than a bare dict. The merge walks both in lockstep, so the winning layer for each leaf is recorded as it wins.

```python
class SourceKind(StrEnum):
    DEFAULT = "default"
    SYSTEM_FILE = "system file"
    SYSTEM_DROPIN = "system drop-in"
    USER_FILE = "user file"
    USER_DROPIN = "user drop-in"
    OVERRIDE_FILE = "override file"
    ENV = "environment"
    CLI = "command line"

@dataclass(frozen=True, slots=True)
class Origin:
    source: SourceKind
    path: Path | None = None    # for file sources
    detail: str | None = None   # env var name, or flag e.g. "--port"

    def describe(self) -> str: ...   # "/etc/myapp.d/20-site.toml" | "MYAPP_PORT" | "--port"
```

Stored on the instance as `__monoconf_origin__: dict[str, Origin]`, keyed by dotted path (`"database.host"`), and reachable via `cfg.origin_of("database.host")`.

### Exception hierarchy

```
MonoconfError
├── SchemaError          definition-time; raised at import
├── ConfigFileError      unparseable file, both-root-forms, missing override
├── SourceError          bad env value, bad CLI value
└── ValidationFailed     wraps pydantic's ValidationError with origins attached
```

`SchemaError` is the only one that fires at import. The rest fire during `resolve()`, and `load()` converts all of them to a stderr message plus `SystemExit(2)`.

### Rewriting validation errors

This is the highest-value piece of polish in the library. Pydantic's raw output is unhelpful to an operator who never wrote the model:

```
1 validation error for AppConfig
port
  Input should be a valid integer [type=int_parsing, input_value='nine thousand']
```

monoconf rewrites it using the origin map:

```
myapp: invalid configuration

  port: 'nine thousand' is not a valid integer
    from /etc/myapp.d/20-site.toml

  database.host: field required
    no value from any source

Try 'myapp --help' for usage.
```

Rules for the rewriter:

- Always name the origin. A value that came from a file gets the file path; from env, the variable name; from the CLI, the flag as typed.
- One block per error; never truncate to the first.
- For an enum/literal mismatch, list the valid choices.
- Keep pydantic's original exception as `__cause__` so it's available for anyone who wants the structured detail.

### Custom-type errors

The string-constructibility probe runs at class definition, so the common failure is caught early:

```
SchemaError: AppConfig.endpoint: type 'Endpoint' cannot be constructed
from a string, so it cannot be set from the CLI or environment.

Either give Endpoint a single-string constructor, or annotate the field:

    endpoint: Annotated[Endpoint, BeforeValidator(Endpoint.parse)] = ...
```

### Warnings

All warnings use real `Warning` subclasses so they can be filtered, escalated with `-W error`, or captured in tests with `pytest.warns`:

- `UnknownConfigKey` — key in a file matching no field.
- `IgnoredValue` — a file or env var setting a *required* field, which is CLI-only and therefore ignored.
- `UnreadableSource` — a config path or drop-in directory that exists but cannot be read.

## Package layout and toolchain

### Layout

```
monoconf/
├── pyproject.toml
├── README.md
├── LICENSE
├── src/monoconf/
│   ├── __init__.py       # public exports only: Config, settings, Field,
│   │                     # SourceKind, Origin, and the exception types
│   ├── config.py         # Config base class, metaclass, load/resolve
│   ├── settings.py       # settings() and SettingsConfigDict
│   ├── fields.py         # Field wrapper, flag/env/metavar derivation
│   ├── schema.py         # introspection + all definition-time validation
│   ├── merge.py          # deep merge, Origin, SourceKind, provenance
│   ├── errors.py         # exception hierarchy, warnings, error rendering
│   ├── sources/
│   │   ├── defaults.py
│   │   ├── files.py      # discovery, platform paths, TOML, root table
│   │   ├── env.py
│   │   └── cli.py        # argparse construction, formatter, two-pass
│   └── py.typed
├── tests/
└── examples/
    ├── minimal.py        # five lines, no settings() call
    ├── full.py           # every settings() key exercised
    └── nested.py
```

Each source module exposes one function with the same shape:

```python
def collect(spec: Spec, ctx: Context) -> tuple[dict[str, Any], dict[str, Origin]]:
    ...
```

Uniform signature means the resolver is a short loop over an ordered tuple of collectors, and adding a source later (JSON, a secrets backend) is additive rather than surgical.

### Toolchain

- **uv** for env and lockfile; **hatchling** as build backend, src layout.
- **pytest** with `pytest-cov`. No fixtures that touch the real filesystem outside `tmp_path`, and no test that reads real `os.environ`.
- **ruff** for lint and format, default rules plus `I`, `UP`, `B`, `SIM`, `RUF`.
- **mypy --strict**, and `py.typed` shipped. A config library that isn't itself well-typed isn't credible.
- **GitHub Actions**: matrix over 3.11/3.12/3.13, Ubuntu + Windows + macOS. Windows is non-optional in the matrix because platform path logic is real code, not a footnote.
- **Licence:** MIT.

### `pyproject.toml` essentials

```toml
[project]
name = "monoconf"
requires-python = ">=3.11"
dependencies = ["pydantic>=2.8"]

[project.optional-dependencies]
dev = ["pytest", "pytest-cov", "mypy", "ruff"]
```

No `[project.scripts]` — monoconf is a library, not a tool.

### Documentation deliverables

README carries the whole story; no docs site for v1.

1. Thirty-second example: a class, a `load()`, the resulting `--help`.
2. The precedence table, verbatim from this plan.
3. The positional/keyword rule and *why* it exists — this is the thing that will surprise people, so justify it rather than just stating it.
4. The string-constructibility contract for custom types.
5. Drop-in directory conventions with the `10-`/`20-`/`99-` numbering.
6. A "why not X" section covering `pydantic-settings`, `dynaconf`, `click`, and `argparse` alone. Being opinionated obliges you to say what the opinion is against.

## Build order

Nine milestones. Each ends with tests passing, `mypy --strict` clean, and a working example — no milestone leaves the tree in a state where the public surface is half-built.

**M1 — Skeleton.** `uv init`, src layout, pyproject, ruff/mypy/pytest config, CI matrix, MIT licence, empty `py.typed`. One trivial test so CI is green from the first commit.

**M2 — Schema introspection.** `schema.py`: walk a pydantic model, classify each field as required/optional, scalar/list/nested, derive flag name, env name, dotted path and metavar. No sources yet. Output is a `Spec` object — the thing every later module consumes. Tests assert the derived names for a representative model.

**M3 — Definition-time validation.** All nine `SchemaError` cases from *Schema rules*, including the string-constructibility probe. One test per case asserting both that it raises and that the message names the field. This lands before any source so the invariants can't be quietly broken later.

**M4 — Merge and provenance.** `merge.py`: deep merge of sparse dicts, list-replace, `Origin`/`SourceKind`, origin map produced alongside merged values. Pure functions, no I/O, exhaustively tested. This is the core; get it right here and the sources become thin.

**M5 — Defaults + CLI.** The first end-to-end path: defaults source, argparse construction, two-pass parsing, `SENTINEL` handling, bool flags including `--no-*`, repeated list flags, short flags, `-h/--help`. At the end of M5, `AppConfig.load()` works with CLI and defaults only. Ship the custom `HelpFormatter` here too, with resolved-default display reading only from defaults for now.

**M6 — Environment.** `env.py`: prefix, `__` nesting, bool and list parsing, empty-string-as-null for optional fields, `MYAPP_CONFIG_OVERRIDE`. Slots in below CLI. Precedence tests: env beats defaults, CLI beats env.

**M7 — Files.** The largest milestone. Platform path detection, XDG handling, drop-in enumeration and lexicographic ordering, TOML parsing, root-table resolution including the both-forms error, unknown-key warnings, `--config-override`. Layers 2 through 6 all land together because they share every code path. Full eight-layer precedence test.

**M8 — Error rendering and `--show-config`.** The `ValidationError` rewriter, all warning categories, `--show-config` TOML output with provenance comments. Snapshot tests on rendered output — these are the strings users read, so they deserve regression protection.

**M9 — Docs and release.** README per the deliverables list, three examples, `--help` output captured in the README verbatim, version `0.1.0`, publish to TestPyPI first.

### Notes for the implementing agent

- **Don't reach for `pydantic-settings`.** If something seems to need it, that's a signal the design is being fought, not that a dependency is missing.
- **Don't add configuration knobs beyond the `settings()` table.** If a behaviour seems to want an option, it probably wants a documented opinion instead. Flag it rather than adding the flag.
- **`SENTINEL` is load-bearing.** Any refactor that lets argparse's own defaults reach the merge layer silently breaks precedence. Guard it with a test that asserts `--port 8080` beats a file setting `port = 9090` even though 8080 is also the schema default.
- **Sources stay pure.** Each `collect()` reads its inputs and returns data; no source mutates shared state or reads another source's output. The only ordering lives in the resolver.
- **Test platform paths on all three OSes**, not by mocking `sys.platform` alone. The CI matrix exists for this.

## Test plan

`resolve(argv=..., env=...)` plus `settings(system_config=..., user_config=...)` means every test is hermetic: no global state, no monkeypatching `os.environ`, no real config paths. Files go in `tmp_path`.

### Acceptance tests

The behaviours that define the library. If these pass, it works.

1. **Full chain.** A field set at all eight layers resolves to the CLI value; remove layers one at a time from the top and confirm each lower layer takes over in order.
2. **Sentinel correctness.** With schema default `8080` and a file setting `9090`, `--port 8080` resolves to `8080` with origin `CLI`.
3. **Required is CLI-only.** A required field set in a file and in env is ignored, warns `IgnoredValue`, and still errors if the positional is absent.
4. **Deep merge.** Base file sets `database.host`; drop-in sets `database.port`; both survive.
5. **Drop-in order.** `10-a.toml` and `20-b.toml` setting the same key — `20-b` wins. Rename to `9-a.toml`/`20-b.toml` and confirm byte-wise sort, not numeric.
6. **List replace.** A drop-in's list wholly replaces the base file's.
7. **Both root forms.** A file with `[myapp]` and top-level keys raises `ConfigFileError` naming the file.
8. **Help short-circuit.** `resolve(argv=["--help"])` raises `HelpRequested` with no complaint about missing positionals.
9. **Resolved defaults in help.** With a file setting `port = 9090`, help text shows `[9090]`.
10. **`--config-override`.** Repeated files, later wins; a directory errors; a missing path errors; sits above user drop-ins and below env.
11. **Every `SchemaError`.** One test per case, asserting the message names the offending field.
12. **Round-trip `--show-config`.** Feed its output back as a config file; resolve again; identical values.

### Property tests

Worth `hypothesis` for two things, if it earns its keep:

- **Merge associativity.** Folding layers left to right equals folding any adjacent pair first. Catches ordering bugs in nested merges.
- **Origin totality.** Every leaf in the resolved model has exactly one origin — never zero, never ambiguous.

### Snapshot tests

`--help`, `--show-config`, and rendered error messages for a fixed example model. These strings are the user interface; changing them should require deliberately updating a snapshot.

### Platform tests

Path derivation runs on all three CI platforms. On POSIX, additionally test with `XDG_CONFIG_HOME` set, unset, and set to a relative path (which the spec says to ignore).

### Coverage

Target 90% with a hard floor of 85% in CI. Full coverage of `merge.py` and `schema.py` specifically — they have no I/O and no excuse.

## Deferred

Explicitly out of v1. Listed so the agent doesn't build toward them speculatively — but the architecture shouldn't foreclose them either.

| Feature | Status | Note |
| --- | --- | --- |
| Subcommands | Deferred, possibly permanent | Conflicts with a flat positional schema. Revisit only with a concrete use case. |
| Per-field `cli=False` | Deferred to v0.2 | Wanted for nested models that live in files only. Needs a decision on whether it still permits env. |
| Variadic positionals (`nargs="+"`) | Deferred | `paths: list[Path]` with no default is a `SchemaError` for now. |
| Config file writing | Deferred | Would need `tomli-w`. `--show-config` covers most of the demand. |
| Non-TOML formats | Deferred | The source interface is uniform, so JSON/YAML would be additive. |
| Secret backends | Deferred | Same shape as a new source. |
| `--version` | Not planned | Maintainer's job. |
| Async loading | Not planned | Config resolution is milliseconds of local I/O. |
| Live reload / file watching | Not planned | Different problem; belongs in a separate library. |

The two decisions worth revisiting after real use: **per-field `cli=False`**, which has the clearest motivating case, and **list append semantics**, if operators find replace-only genuinely painful in drop-in-heavy deployments. Both are additive.

## Amendments

2026-09-18

The plan above is the design authority. Reviewing it before implementation turned up a
handful of contradictions, under-specified derivations and genuine gaps. Each is resolved
here rather than left to the implementation, because an undocumented opinion is a bug.
Two outright contradictions were fixed inline in the text above instead: `Field` takes
three CLI keys (`short`, `metavar`, `flag`), and `system_config`/`user_config` accept a
sequence of paths as well as a single one.

### Multi-path layers

POSIX system configuration is several files, not one, so layers 2 and 3 of the chain each
cover a list. Layer 2 is every system file in ascending priority order; layer 3 is every
corresponding `.d/` directory, in that same order. The user layers work the same way.
`SourceKind` is unchanged — the distinguishing detail is the path, which `Origin` already
carries.

### XDG_CONFIG_DIRS ordering

The XDG spec gives *earlier* entries in `$XDG_CONFIG_DIRS` higher precedence. Since the
chain folds lowest priority first, the list is iterated **in reverse**, and
`/etc/<app>.toml` is read after all of them. Relative entries are ignored, as the spec
requires. Test with the variable set, unset, and containing a relative path.

### String-constructibility probe

"Attempt a string validation and error if it raises" does not work: `int` given a probe
string raises `int_parsing` while being perfectly string-constructible. The probe
classifies pydantic's error codes instead.

- A `*_type` code (`model_type`, `dataclass_type`, `is_instance_of`, `model_attributes_type`,
  `dict_type`, `list_type`, `set_type`, `tuple_type`, …) means the type rejects strings
  outright → `SchemaError`.
- A `*_parsing` / `*_from_string` code, or any value error, means the type accepted the
  string and merely disliked this one → fine.

An `Annotated[T, BeforeValidator(...)]` therefore passes automatically: its failure on the
probe string is a value error, not a type error. The stdlib allowlist in *Custom types*
short-circuits ahead of the probe.

The contract applies to **every non-nested field**, not only required ones. With per-field
`cli=False` deferred, every optional field is CLI-settable too, so every one of them must
survive a string.

### Container-typed fields

Error case 6 bans required containers. Optional ones needed a rule too:

- `list[T]` and `set[T]` generate a repeated flag. A set deduplicates while preserving
  first-seen order, so help text and `--show-config` stay stable.
- `dict`, `tuple` and `list[SomeModel]` are definition-time `SchemaError`s. None of them
  has an honest single-string CLI form, and inventing one — a JSON blob in a flag, or a
  field that silently skips the CLI — would either add a second value syntax or ship
  `cli=False` by the back door. The error points at a nested model instead.

### Nullable nested models

`database: DatabaseConfig | None = None` has no sane flag form — `--database-host` would
have to materialise a parent that is `None`. A nested-model field must not be nullable;
it is a definition-time `SchemaError` in v1.

### root_table shadowing a field

A file containing only `[myapp]` is read as the root-table form even when `myapp` is also
a field name, silently reinterpreting the operator's file. The both-forms error does not
catch this. New definition-time check: a top-level field whose name equals `root_table`
is a `SchemaError`.

### HelpRequested and ShowConfigRequested

`resolve()` needs to hand back rendered output without printing or exiting, for `--help`
and equally for `--show-config`. The hierarchy gains:

```
MonoconfError
└── OutputRequested        .text, .exit_code
    ├── HelpRequested
    └── ShowConfigRequested
```

`resolve()` raises them; `load()` writes `.text` to stdout and exits `.exit_code` (0 for
both).

### Empty strings

Stated for `X | None` in *Merge semantics*; generalised here, and shared by the env and
CLI sources:

- An empty string is `None` **only** when the annotation admits `None`.
- Otherwise it is the empty string. `MYAPP_NAME=` on a `str` field means `""`, not null.
- On a list field it is the empty list.

### No flag abbreviation

Both parser passes use `allow_abbrev=False`. argparse's default prefix matching would let
pass two accept `--config-ov` while pass one, which knows only the meta-arguments, does
not — the two passes must agree on what a flag is. It also removes the ambiguity that
generated flags sharing a prefix would otherwise create. One flag, one spelling.

### --show-config output

There is no TOML writer in the dependency budget, so monoconf emits TOML itself: a small
internal module handling scalars, escaped strings, lists, nested tables, and
`Path`/`UUID`/`datetime` rendered as strings. A `None` value is emitted as a comment, TOML
having no null — the same limitation documented under *Explicit null*.

Required fields are positional and CLI-only, so a dump containing them as ordinary keys
would warn `IgnoredValue` when fed back as a config file — on keys monoconf itself wrote.
They are emitted commented instead:

```toml
# input_path = "in.csv"   # positional, CLI-only
```

The dump stays a complete picture of the resolved config, and the round-trip is
warning-free.

### app_name inference

Everything derives from `app_name`, so the inference is pinned rather than left as
"module or class name":

1. The defining module's root package name (`__module__.split(".")[0]`).
2. If that is `__main__`, the class name snake_cased with a trailing `_config` stripped.
3. If that leaves nothing, the class name snake_cased.

A `-` in `app_name` becomes `_` for the env prefix (`my-app` → `MY_APP_`); the root table
keeps the hyphen, which is a legal TOML bare key.

### Flag name from a list field

Singularisation is applied in this order, first match winning:

| Field name ends | Becomes | Example |
| --- | --- | --- |
| `ies`, longer than four characters | `y` | `entries` → `--entry` |
| `ses`, `xes`, `zes`, `ches`, `shes` | drop `es` | `addresses` → `--address` |
| `s`, but not `ss`, `us`, `is` | drop `s` | `tags` → `--tag` |
| anything else | unchanged | `extra` → `--extra`, `status` → `--status` |

Derived flags go through the ordinary collision check. `Field(flag=...)` overrides the
derivation, which is what irregular plurals use.

### Metavar derivation

`Path` → `PATH`, `int` → `N`, `float` → `X`, `Decimal` → `DECIMAL`, `UUID` → `UUID`,
`datetime`/`date`/`time` → `DATETIME`/`DATE`/`TIME`, `timedelta` → `DURATION`, anything
else → the field name in UPPER_SNAKE. Enums and Literals use the field name and list
their choices in the help text.

The help block under *Help output* is illustrative, not normative: its usage line and its
option list disagree about metavars, and its `-p` is attached to a different field than
the one in the `Field` example. The README's captured `--help` must come from real output.

### Unknown environment variables

A prefixed variable matching no field is ignored silently. The prefixed namespace is not
exclusively monoconf's — deployments routinely set `MYAPP_HOME`, `MYAPP_VERSION` and the
like — so warning there produces noise the operator cannot act on. `UnknownConfigKey` and
`strict_unknown` stay file-source behaviours.

`MYAPP_CONFIG_OVERRIDE` is likewise ignored when `settings(config_override=False)`. A
feature that is off is off.

### Nullable booleans

A boolean flag is presence-only, so it expresses two states; `bool | None = None`
asks for three. There is no honest flag form, and inventing one (emitting both
`--color` and `--no-color`) would put a second boolean convention into the CLI. A
nullable boolean is a definition-time `SchemaError`, pointing at two separate
fields or an enum whose members name the three cases.

### What "constructible from a single string" means in practice

*Custom types* lists "anything with a `__init__(self, value: str)`" as satisfying
the contract. On pydantic v2 that is not so: pydantic cannot build a schema for an
arbitrary class at all, and refuses the model outright unless the type is
pydantic-aware. The contract is met by a `BeforeValidator`, a
`__get_pydantic_core_schema__`, an `Enum`, or one of the stdlib types.

`arbitrary_types_allowed=True` does make the model build, but the schema it gets is
an isinstance check, which no string can satisfy — so the probe rejects it, which
is the right answer. The probe runs under the model's own pydantic config for
exactly this reason: a field has to be judged against the rules it will really be
validated with.

The stdlib allowlist in the original text is not implemented separately. The probe
already accepts every type on it, and a second code path agreeing with the first
is a liability rather than a safeguard; the types are covered by name in the tests
instead.

### Clearing a nullable list

An empty value means the empty list on a repeated field, whether it arrives as
`--tag ""` or `MYAPP_TAGS=`, and that is checked before the nullable rule. So a
`list[str] | None` field cannot be set to null from the command line or the
environment; it can only be emptied. Consistency between the two sources is worth
more than the third state, which applications almost always treat as the empty list
anyway.

### Where MYAPP_CONFIG_OVERRIDE lands

The milestone list puts `MYAPP_CONFIG_OVERRIDE` in M6, with the environment source.
It reads a *file* layer that does not exist until M7, so it ships in M7 instead,
alongside `--config-override`, the path validation and the layer it feeds --
where it can be tested against something real. M6 is the value-reading source only.

2026-09-22

Implementing M7 settled five questions the text above leaves open or gets wrong. The
same rule applies as to the amendments above: the opinion is the product, so it is
written down rather than left in the code for someone to infer.

### Lax coercion is the whole of it

*Parsing* claimed a TOML `port = "8080"` was a type error. It is not, and it cannot be
while *Merge semantics* validates once, at the end, on the merged result: the command
line and the environment hand pydantic strings for every field they set, so that single
pass has to run in pydantic's lax mode, and lax mode coerces a file's `"8080"` just as
willingly. Rejecting it would need either a second validation pass for file-sourced
leaves or a type check of monoconf's own, and validating once is worth more than the
extra rejection — a wrong type still fails, in the one place every other error is
reported.

What survives is the narrower and more useful claim: monoconf stringifies nothing on the
way in. A TOML datetime reaches pydantic as a datetime, and an array as an array.

### The root table in a shared file

The both-forms error fires only when the root table is present **and** a top-level key
matches a field or nested model this schema would itself read. The literal rule — root
table plus any other top-level key — rejects every real `pyproject.toml`, which leaves
`root_table="tool.myapp"` unusable in the one file where a dotted root table occurs at
all, and the root-table form exists precisely for files an application is a guest in.

The narrower rule still catches everything the throw was for: a key that would silently
do nothing is exactly a key this schema reads. Everything else outside the root table
belongs to another application and passes in silence, with one exception — a key within
edit distance of one of ours (`prt` beside `port`) warns `UnknownConfigKey`, because
that is an operator's mistake with a dead setting behind it. `strict_unknown` does not
escalate that warning: it governs what this application's own table holds, and a key
outside it was never ours to refuse.

### `--config-override` and its variable

When both name files, the flag replaces `MYAPP_CONFIG_OVERRIDE` rather than stacking on
it. A list of files is a value like any other, lists replace wholesale at every layer,
and the command line beats the environment whole. The variable is read only when the
flag is absent, and it is ignored entirely when either `config_override` or
`env_enabled` is off: a feature that is switched off is switched off.

### `--help` is never withheld

Help output shows resolved defaults, so rendering it means reading the files first — and
a file that cannot be parsed would otherwise take `--help` down with it, which is the
one command an operator reaches for when something is already wrong. So the lower layers
are resolved inside a guard: if a source raises, help is rendered from the schema's own
defaults and the error is printed beneath it, naming the file. Help always answers.

The two-pass structure is unchanged — `--help` is still detected in pass one, before
anything can complain about a missing positional.

### A dotted root table shadowing a field

The definition-time check compared a field's name against the whole `root_table` string,
so `root_table="tool.myapp"` let a field named `tool` through — and a file in root-table
form would then have a `[tool]` table that is both this schema's field and the address
of its settings. The check compares against the root table's first segment instead,
which is the name a file actually puts at the top level.

2026-09-22

M8 settled the questions below before any of it was built: the rendered output is the
user interface, so its rules are written down first.

### `--show-config` does not need the positionals

The question `--show-config` answers is "why is `port` 9090?", and making the operator
invent an input file before they may ask it helps nobody. While `--show-config` is given,
every positional is optional. A positional that was given is dumped commented, as
*--show-config output* above says; one that was not is dumped commented too, marked
`not given`:

```toml
# input_path = "in.csv"   # positional, CLI-only
# dataset                 # positional, CLI-only, not given
```

Validation still happens once. When a positional is missing, that one pass runs against
a model derived from the application's own — its validators included — in which the
missing positionals are `T | None = None`. Everything else is validated exactly as a
normal run would validate it, and fails exactly as loudly.

`--help` beats `--show-config` when both are given: help is the one that is never
withheld.

### What `--show-config` dumps

The validated model, not the merged dict: the dump shows what the application will
actually be handed, coerced and typed, which is also what makes the round-trip exact.
Rendering follows *--show-config output* above, and fixes the remaining details:

- An `Enum` is its value. A `date`, `time` or `datetime` is its ISO 8601 string. A set
  is an array, sorted, because a set has no order and the dump has to be stable.
- Anything else that is not a TOML scalar is `str(value)` — which is the whole
  contract a field type signs up to.
- A pydantic secret type is shown as its mask, commented out. `--show-config` never
  prints a secret, and a dump fed back as a config file must not replace one with a row
  of asterisks.
- `None`, or an array containing it, is commented out, TOML having no null.
- Scalars come before tables at every level, in declaration order. Provenance comments
  are aligned within each table.
- The dump is always in the top-level form, never under the root table. Both are valid
  in a file the application owns, and the top-level form is the one that cannot be
  mistaken for a guest's table in a shared file.

### Rewritten validation errors

- **Where a value came from** is the origin of the longest prefix of the error's location
  that has one: an error at `tags.1` is from wherever `tags` came from, lists being
  values that one layer set whole.
- **The path shown** is the field's dotted path, with a list index as `[1]`. Pydantic
  adds location segments of its own for unions and validator chains; they mean nothing
  to an operator and are dropped.
- **An error with no origin** is either a value no source set (`no value from any
  source`) or a model-level validator's, which is attributed to the model it checked,
  not to a leaf it might have been about.
- The message names the offending value, in pydantic's own lax terms — "is not a valid
  integer" — and falls back to pydantic's message, first letter lowered, for the long
  tail of constraint errors that already read well.

`load()` prints the rendered block as it stands, followed by a blank line and the
`Try 'myapp --help'` line. It already names the program; prefixing it again, as every
other error is prefixed, would say `myapp: myapp:`.

### Snapshots are plain files

Snapshot tests compare against `tests/snapshots/*.txt`, exactly. `pytest
--update-snapshots` rewrites them, so every change to user-visible output arrives as a
diff to a text file, reviewed like any other. No snapshot library: the whole mechanism
is a fixture, and the dev dependency would outweigh it.

The `--help` snapshot uses a model without short flags, because argparse 3.13 renders
an option with both forms differently from 3.11 and 3.12; `test_help.py` still covers
short flags by fragment.

2026-09-22

M9 settled three things about the release itself.

### The pydantic floor is 2.8

The dependency said `pydantic>=2.0`, which nothing tested. Against each minor release,
monoconf's own code runs from 2.0, but the suite first passes whole at 2.8: before it,
pydantic hands a builtin such as `str.strip`, used as a `BeforeValidator`, an argument it
cannot take, and the definition-time probe of that field fails with a `TypeError`
instead of a verdict. A floor the suite does not pass on is not a floor, so it is 2.8,
and a CI job pinned to 2.8 keeps it honest.

### Three examples

`examples/full.py` exercises every `settings()` key and absorbs M8's
`examples/show_config.py`, leaving the three examples *Package layout* names.

### Publishing is by hand

0.1.0 is built and checked in the repository (`uv build`, `twine check`) and uploaded to
TestPyPI by a maintainer. There is no release workflow, and so no publishing credential
anywhere near CI; that can change when there is a second release to automate.
