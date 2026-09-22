"""Every settings() key, on one service.

widgetd is a system service: it reads /etc/widgetd.toml and the drop-ins in
/etc/widgetd.d/, has no per-user file, and refuses a config key it does not
know rather than warning about it. Try:

    python examples/full.py --help
    python examples/full.py --show-config
    WIDGETD_LOG_LEVEL=debug python examples/full.py -p 9090 index.csv
"""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from monoconf import Config, Field, settings


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Database(BaseModel):
    host: str = Field("localhost", description="Database host.")
    port: int = Field(5432, description="Database port.")


class WidgetdConfig(Config):
    """Configuration for widgetd."""

    model_config = settings(
        app_name="widgetd",
        prog="widgetd",
        description="Serve the widget index over HTTP.",
        epilog="Configuration is read from /etc/widgetd.toml and /etc/widgetd.d/.",
        env_prefix="WIDGETD_",
        env_enabled=True,
        root_table="widgetd",
        system_config=Path("/etc/widgetd.toml"),
        user_config=None,  # a system service has no per-user file
        dropins=True,
        config_override=True,
        show_config=True,
        strict_unknown=True,
    )

    input_path: Path = Field(description="The widget index to serve.")

    port: int = Field(8080, short="p", description="Port to listen on.")
    log_level: LogLevel = Field(LogLevel.INFO, description="How much to log.")
    proxy: str | None = Field(None, description="Upstream proxy URL.")
    tags: list[str] = Field(default_factory=list, description="Tag to apply.")
    database: Database = Database()


if __name__ == "__main__":
    cfg = WidgetdConfig.load()
    print(f"serving {cfg.input_path} on port {cfg.port}")
    print(f"database {cfg.database.host}:{cfg.database.port}")
    print(f"log level {cfg.log_level} from {cfg.origin_of('log_level').describe()}")
