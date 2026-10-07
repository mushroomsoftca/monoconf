# Changelog

User-visible changes. The design behind them is in PLAN.md; new design decisions
get a dated amendment there.

## Unreleased

- Nothing yet.

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
- A mistyped flag is answered with a suggestion, as an unknown file key is:
  `unrecognized arguments: --retires 5 (did you mean '--retries'?)`.
- Every bad value is reported in one run, rewritten for operators and naming its
  source: a value pydantic rejects, and a variable monoconf cannot read at all,
  such as `MYAPP_VERBOSE=maybe`, both raise `ValidationFailed`. `SourceError` is a
  command line that cannot be parsed.
- A secret field's value is never printed: `--help` and `--show-config` show its
  mask, and an error calls it `the secret` rather than quoting it.
- Warnings: `UnknownConfigKey`, `IgnoredValue`, `UnreadableSource`.
- Requires Python 3.11+ and pydantic 2.8+. Tested on Python 3.11 to 3.14, on
  Linux, macOS and Windows.
