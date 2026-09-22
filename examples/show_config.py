"""Where did that value come from? Ask with --show-config.

    python examples/show_config.py --show-config
    MYAPP_DATABASE__HOST=db.internal python examples/show_config.py --show-config

The output is a config file: save it, and it pins the configuration it shows.
"""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from monoconf import Config, Field, settings


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"


class Database(BaseModel):
    host: str = Field("localhost", description="Database host.")
    port: int = Field(5432, description="Database port.")


class AppConfig(Config):
    """Serve the widget index."""

    model_config = settings(app_name="myapp", show_config=True, config_override=True)

    input_path: Path = Field(description="Path to the input file.")

    port: int = Field(8080, description="Port to listen on.")
    log_level: LogLevel = LogLevel.INFO
    tags: list[str] = Field(default_factory=list, description="Tag to apply.")
    database: Database = Database()


if __name__ == "__main__":
    cfg = AppConfig.load()
    print(f"serving {cfg.input_path} on port {cfg.port}")
    print(f"database {cfg.database.host}:{cfg.database.port}")
