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
