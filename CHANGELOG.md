# Changelog

User-visible changes. The design behind them is in PLAN.md; new design decisions
get a dated amendment there.

## Unreleased

- Every bad value is reported in one run. A variable monoconf cannot read, such as
  `MYAPP_VERBOSE=maybe` or a malformed JSON list, now joins the `ValidationFailed`
  report alongside pydantic's errors, instead of raising `SourceError` and hiding
  the rest. `SourceError` now means only a command line that cannot be parsed.
  `--help` names such a variable beneath the defaults rather than failing.
- A mistyped flag is answered with a suggestion, as an unknown file key already
  was: `unrecognized arguments: --retires 5 (did you mean '--retries'?)`.
- A secret field's value is no longer printed by `--help` or quoted in a validation
  error. Help shows `[**********]` for a set secret and `[""]` for an empty one, and
  a rejected secret is called `the secret`. `--show-config` now also comments out a
  list of secrets, rather than writing the masks as a live key.
- Python 3.14 is tested in CI and declared in the classifiers.
- The licence metadata is the SPDX expression `MIT`, and the sdist no longer ships
  the repository's CI, agent instructions or lockfile.

## 0.1.0

The first release: one pydantic model, one command line, one environment reader,
one layered TOML loader, one precedence order.

- `Config` with `load()` (CLI semantics: print and exit) and `resolve()` (raise
  instead), and a `settings()` table of thirteen options.
- The fixed eight-layer precedence: defaults, system file and drop-ins, user file
  and drop-ins, `--config-override`, environment, command line. POSIX/XDG and
  Windows default paths.
- Required fields are positional and CLI-only; everything else is a flag, a
  variable and a file key. Booleans are presence-only; lists are repeated flags
  and replace wholesale.
- Schema errors at import, including the string-constructibility check.
- Provenance for every value: `cfg.origin_of("database.host")`.
- `--help` showing the defaults resolved on this machine; `--show-config` printing
  the resolved configuration as round-trippable TOML with provenance comments.
- Validation errors rewritten for operators, naming each value's source.
- Warnings: `UnknownConfigKey`, `IgnoredValue`, `UnreadableSource`.
- Requires Python 3.11+ and pydantic 2.8+.
