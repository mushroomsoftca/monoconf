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

M1–M7 have landed, and the precedence chain is complete: schema defaults, system
files and their drop-ins, user files and theirs, `--config-override`, the
environment, and the command line. `load()` and `resolve()` resolve all eight
layers, `--help` shows the default each flag would actually fall back to on the
machine it is running on, and every resolved value knows which file, variable or
flag it came from. Error rendering and `--show-config` arrive in M8; the README
proper, with the precedence table and the full rationale, arrives with M9.
