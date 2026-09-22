# AGENTS.md

Instructions for coding agents working in this repository. `PLAN.md` is the design
document and the authority on *what* monoconf does; this file covers *how* to work
on it.

## What this project is

monoconf is an opinionated universal configuration library for Python 3.11+. A
pydantic v2 model produces a CLI parser, an environment reader, and a layered TOML
file loader, resolved through one fixed precedence order.

It is a **library**, not an application. No entry points, no `[project.scripts]`,
no CLI of its own.

## Commands

```bash
uv sync --all-extras          # set up the environment
uv run pytest                 # run tests
uv run pytest tests/test_merge.py -x -q   # one file, stop on first failure
uv run pytest --cov=monoconf --cov-report=term-missing
uv run ruff check --fix .     # lint
uv run ruff format .          # format
uv run mypy src tests         # strict type check
```

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
   output. Ordering lives only in the resolver.
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
- **Do not add any runtime dependency** without asking. `pydantic>=2` plus stdlib
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

```
src/monoconf/
  __init__.py     public exports only
  config.py       Config base class, metaclass, load/resolve
  settings.py     settings() and SettingsConfigDict
  fields.py       Field wrapper, flag/env/metavar derivation
  schema.py       introspection + definition-time validation
  merge.py        deep merge, Origin, SourceKind, provenance
  errors.py       exception hierarchy, warnings, error rendering
  toml.py         the TOML emitter behind --show-config
  sources/        defaults.py, files.py, env.py, cli.py
tests/
examples/
```

`__init__.py` exports exactly: `Config`, `settings`, `Field`, `SourceKind`,
`Origin`, and the exception types. Adding to that list is an API decision — ask.

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
  `merge.py` and `schema.py`, which are the pure core.
- New source, new flag form, new error: add the acceptance test from the Test
  plan section of `PLAN.md` before the implementation.
- Snapshot tests cover `--help`, `--show-config`, and rendered errors. If a
  snapshot changes, that is a user-visible change — say so explicitly in the
  commit message rather than silently regenerating it.
- Coverage floor is 85% in CI; target 90%. `merge.py` and `schema.py` should be
  at 100%.

## Working style

- Follow the milestone order in `PLAN.md` (M1–M9). Each milestone ends green:
  tests pass, mypy clean, a working example.
- Prefer small commits that each leave the tree working over one large one.
- Commit messages: imperative mood, first line under 72 chars, body explaining
  *why* when the change isn't obvious. Reference the milestone (`M4: ...`).
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
