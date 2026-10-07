# AGENTS.md

Instructions for coding agents working in this repository. Read this file first:
it is written to be enough on its own for most tasks. `PLAN.md` is the design
authority on *what* monoconf does; this file covers *how* to work on it, and where
in PLAN.md to look.

## Status

- **0.1.0 is complete** on `main`. Milestones M1–M9 have all landed, and the work
  since; `git log` has the history.
- **Not yet published, so still 0.1.0.** Uploading is done by hand, by the
  maintainer, who tags the uploaded commit `v0.1.0`. Until that tag exists,
  everything on `main` is 0.1.0, and `CHANGELOG.md`'s 0.1.0 section describes it as
  it stands. See PLAN.md, amendments *Publishing is by hand* and *0.1.0 is the
  first upload*.
- **What's next** is the maintainer's call. PLAN.md § Deferred names the two
  candidates, per-field `cli=False` and list-append semantics, and both need a
  design decision from the maintainer before any code. Nothing is in flight.

## How to use PLAN.md

It is about 1,200 lines. Don't read it whole.

- The top of the file has a table mapping each body section to the dated
  **Amendments** that override it. Check that table before relying on a body
  section.
- `grep -n '^##' PLAN.md` lists the sections; read only the one you need.
- A new design decision gets a new dated amendment at the end, and a row in that
  table. Don't bury a decision in code or in a commit message.

## What this project is

monoconf is an opinionated universal configuration library for Python 3.11+. A
pydantic v2 model produces a CLI parser, an environment reader, and a layered TOML
file loader, resolved through one fixed precedence order.

It is a **library**, not an application. No entry points, no `[project.scripts]`,
no CLI of its own.

## How it fits together

`Config.resolve()` (`config.py`) does the following:

1. Parse the command line, pass one: `--help`, `--config-override`,
   `--show-config`.
2. Collect every source as sparse `(values, origins)`: defaults, files, env, CLI.
3. Take out any value a source returned as `Rejected`, then `merge()` the sources,
   lowest priority first.
4. Validate once, and report pydantic's errors and the rejected values together.
5. Attach the origins to the result.

`load()` wraps `resolve()`: it prints output or errors, and exits.

| Module | Responsibility |
| --- | --- |
| `config.py` | `Config`, `load`/`resolve`, the resolver's layer order, the `--show-config` model with positionals made optional |
| `settings.py` | `settings()`, `Settings`, `program_name()` |
| `fields.py` | `Field()`, flag, env, metavar and singular derivation |
| `schema.py` | `Spec`/`FieldSpec`, introspection, every definition-time `SchemaError` (pure) |
| `merge.py` | `merge`, `Origin`, `SourceKind`, `leaves`/`set_leaf` (pure) |
| `errors.py` | exceptions, warnings, `render_validation_error` |
| `toml.py` | `dump()`, the `--show-config` TOML emitter (pure) |
| `sources/defaults.py`, `files.py`, `env.py`, `cli.py` | one `collect(spec, ctx)` each. `cli.py` also has both parser passes and `render_help` |

Each of the three snapshotted outputs is rendered in one place: `--help` in
`cli.render_help`, validation errors in `errors.render_validation_error`, and
`--show-config` in `toml.dump`. Other error messages are written where they are
raised.

## Commands

```bash
uv sync --all-extras          # set up the environment
uv run pytest                 # run tests
uv run pytest tests/test_merge.py -x -q   # one file, stop on first failure
uv run pytest --cov=monoconf --cov-report=term-missing
uv run ruff check --fix .     # lint
uv run ruff format .          # format
uv run mypy src tests         # strict type check
uv run pytest --update-snapshots          # rewrite snapshots + README blocks
```

Web sessions run `.claude/hooks/session-start.sh`, which does the `uv sync` for
you.

Before considering any task done: `ruff check`, `ruff format --check`, `mypy`, and
`pytest` must all pass. Do not commit with a failing or skipped test.

## Invariants

These come from `PLAN.md` and are not open for local reinterpretation. If a task
seems to require breaking one, stop and say so rather than working around it.

1. **Requiredness is structural.** No default → required → positional → CLI-only.
   Has a default → optional everywhere. There is no third state.
2. **A keyword argument is never required.**
3. **Source precedence is fixed** and never user-reorderable: defaults → system
   file → system drop-ins → user file → user drop-ins → `--config-override` →
   env → CLI.
4. **`SENTINEL` is load-bearing.** Every optional argparse argument defaults to
   the module-private sentinel, never to the schema default. If argparse's own
   defaults reach the merge layer, precedence breaks silently and the test suite
   is the only thing that will catch it.
5. **Sources are pure.** Each `collect()` reads its inputs and returns
   `(values, origins)`. No source mutates shared state or reads another source's
   output. Ordering lives only in the resolver. A value a source cannot read is
   returned as a `Rejected` in its place, not raised, so that one run reports every
   bad value.
6. **Sparse dicts.** A source contributes a key only if it actually set it. Never
   emit `None` for an absent key.
7. **Validation happens once**, at the end, on the merged dict. Never per layer.
8. **Every leaf has exactly one origin.** Provenance is not optional and not
   behind a flag.
9. **Schema errors fire at import**, via the metaclass hook — never at `load()`.
10. **pydantic is the only runtime dependency.** See below.

## Hard rules

- **Do not add `pydantic-settings`.** If something seems to need it, the design is
  being fought. Raise it instead of adding the dependency.
- **Do not add any runtime dependency** without asking. `pydantic>=2.8` plus stdlib
  is the whole budget. Dev dependencies are fine.
- **Do not add configuration knobs** beyond the `settings()` table in `PLAN.md`.
  A behaviour that seems to want an option usually wants a documented opinion.
  Flag the tension; don't resolve it by adding a flag.
- **Do not implement deferred features** (subcommands, variadic positionals,
  `cli=False`, non-TOML formats, config writing). See the Deferred table in
  `PLAN.md`.
- **Do not weaken a definition-time error into a runtime warning** to make a test
  pass.

## Repository layout

`src/monoconf/` holds the modules in the table above. The tests are in `tests/`,
and the three examples in `examples/` are `minimal.py`, `full.py` and `nested.py`.

`__init__.py` exports exactly: `Config`, `settings`, `Field`, `SourceKind`,
`Origin`, and the exception types. Adding to that list is an API decision — ask.
There is no `__version__`, and adding one counts as the same decision.

`merge.py`, `schema.py` and `toml.py` have no I/O. Keep it that way; it is why they
can be tested exhaustively.

## Code style

- Python 3.11+ syntax. `X | None`, not `Optional[X]`. `StrEnum`, `slots=True`
  dataclasses, `tomllib`.
- Full type annotations everywhere, including tests. `mypy --strict` must pass.
  No `# type: ignore` without a comment explaining why.
- Ruff defaults plus `I`, `UP`, `B`, `SIM`, `RUF`. Let the formatter decide
  formatting; don't hand-align.
- Docstrings on public API only. Internal functions get a comment when the *why*
  is non-obvious, not a restatement of the signature.
- Error messages are part of the product. Every one names the offending field,
  file or variable, and says what to do about it. Write them as prose a sysadmin
  would read at 3am, not as exception soup.
- British or American spelling: match the surrounding file. Don't mass-convert.

## Testing

- Every test is hermetic. Use `resolve(argv=..., env=...)` and
  `settings(system_config=..., user_config=...)` with `tmp_path`. Never read the
  real `os.environ`, never touch real config paths, never monkeypatch
  `sys.platform` as a substitute for the CI matrix.
- Test behaviour through the public API. Reach into internals only for
  `merge.py`, `schema.py` and `toml.py`, which are the pure core.
- New source, new flag form, new error: add the acceptance test from the Test
  plan section of `PLAN.md` before the implementation.
- Snapshot tests cover `--help`, `--show-config`, and rendered errors. They are
  plain files in `tests/snapshots/`, compared through the `snapshot` fixture in
  `conftest.py`. If a snapshot changes, that is a user-visible change: say so
  explicitly in the commit message rather than silently regenerating it.
- The README's code and output blocks are generated from `examples/`. A block
  preceded by `<!-- source: examples/x.py -->` or
  `<!-- output: [VAR=v] python examples/x.py args -->` is checked by
  `tests/test_readme.py`. `--update-snapshots` regenerates these too; review the
  diff before committing.
- The `--help` snapshot must not use short flags: argparse 3.13 renders them
  differently from 3.11 and 3.12.
- CI runs 3.11–3.14 on Linux, macOS and Windows, plus a `pydantic-floor` job
  pinned to pydantic 2.8. A change that needs newer pydantic raises the floor in
  `pyproject.toml`, and that needs a PLAN.md amendment.
- Coverage floor is 85% in CI; target 90%. `merge.py` and `schema.py` should be
  at 100%.

## Working style

- Prefer small commits that each leave the tree working over one large one.
- Commit messages: imperative mood, first line under 72 chars, body explaining
  *why* when the change isn't obvious. The milestones are finished, so there's no
  `M<n>:` prefix any more.
- A user-visible change gets a line under `## Unreleased` in `CHANGELOG.md`. Before
  `v0.1.0` is tagged there is no released behaviour to change, so it updates the
  0.1.0 section instead, written as what 0.1.0 does rather than what changed.
- Keep the examples working and the README true: both are tested.
- When a design question comes up that `PLAN.md` doesn't answer, write down the
  options and ask. Do not pick one silently and bury it in an implementation —
  the opinionated design is the product, so undocumented opinions are bugs.
- If you discover that something in `PLAN.md` is wrong or unworkable, say so
  plainly and propose the fix. The plan is authoritative, not infallible.

## Things that have bitten people

- `--help` must be detected in pass one of the parser and exit before positional
  validation, or a bare `myapp --help` errors about missing arguments.
- Help output shows *resolved* defaults, not schema defaults. That is why help
  rendering happens after file and env resolution.
- Drop-ins sort by byte-wise filename, not numerically. `9-a.toml` sorts after
  `20-b.toml`. This is deliberate and matches systemd.
- TOML file values are **not** stringified: a TOML datetime reaches pydantic as a
  datetime. They still meet the same lax validation pass everything else does,
  so a TOML `port = "8080"` is coerced rather than refused. Validation happens
  once, on the merged result, and that pass has to be lax for the CLI and the
  environment, which deal only in strings.
- Lists replace wholesale at every layer. Never append.
- A pydantic secret's value is never printed. `--help`, the validation errors and
  `--show-config` each check `FieldSpec.secret`, which also covers a list of
  secrets, whose value is a list rather than a secret. Anything new that shows a
  value has to check it too.
- A path prints with the platform's separator: `\etc\myapp.toml` on Windows. A test
  that expects text containing a path builds it with `str(Path("/etc/myapp.toml"))`,
  never a `/` literal, or it passes on Linux and macOS and fails on Windows.
- A test never needs two clock readings to differ. Windows' clock is coarse enough
  that two calls to `datetime.now()` can return the same instant, so the test
  fails there now and then. To see that a factory runs again, count its calls.
