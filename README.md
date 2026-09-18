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
Milestones M1–M4 have landed — the schema introspection, the definition-time
checks and the merge core — so `load()` and `resolve()` still raise
`NotImplementedError` until the sources land in M5–M7. The README proper, with
the precedence table and the full rationale, arrives with M9.
