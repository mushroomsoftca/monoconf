# monoconf

An opinionated universal configuration library for Python 3.11+. One schema
definition — a pydantic v2 model — produces a CLI parser, an environment reader
and a layered TOML file loader, resolved through a single fixed precedence order.

```python
from pathlib import Path

from monoconf import Config, settings


class AppConfig(Config):
    """Serve the widget index."""

    model_config = settings(app_name="myapp")

    input_path: Path          # no default -> required -> positional, CLI-only
    output_dir: Path = Path("out/")   # --output-dir
    verbose: bool = False             # --verbose


cfg = AppConfig.load()
```

**Pre-release.** The library is being built in the milestone order set out in
[PLAN.md](PLAN.md), which is the design authority: what monoconf does and why it
does it that way. [AGENTS.md](AGENTS.md) covers how to work on the repository.

M1–M8 have landed. `load()` and `resolve()` resolve all eight layers of the
precedence chain: schema defaults, system files and their drop-ins, user files and
theirs, `--config-override`, the environment, and the command line. Every resolved
value knows which file, variable or flag it came from, and that is how monoconf
reports errors. A bad value is reported in plain terms, together with the file,
variable or flag that set it:

```
myapp: invalid configuration

  port: 'nine thousand' is not a valid integer
    from /etc/myapp.d/20-site.toml
```

`--help` shows the default each flag would actually fall back to on the machine
it is running on. With `settings(show_config=True)`, `--show-config` prints the
resolved configuration as TOML, with a comment saying where each value came from.
Saved to a file, that output reproduces the same configuration. The README proper,
with the precedence table and the full rationale, arrives with M9.
