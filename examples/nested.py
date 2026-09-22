"""A nested model: one dotted path, spelled three ways.

`database.pool_size` below is

    --database-pool-size 20             on the command line
    MYAPP_DATABASE__POOL_SIZE=20        in the environment
    [database] pool_size = 20           in a config file

The double underscore is the nesting separator, so a single one stays part of
the name. Every nested field needs a default: only a top-level field can be a
positional argument. One level of nesting is the supported case; if you find
yourself wanting two, you probably want a second config file. Try:

    python examples/nested.py nightly --database-pool-size 20
    MYAPP_DATABASE__HOST=db.internal python examples/nested.py nightly
"""

from pydantic import BaseModel

from monoconf import Config, Field, settings


class Database(BaseModel):
    host: str = Field("localhost", description="Database host.")
    port: int = Field(5432, description="Database port.")
    pool_size: int = Field(10, description="Connections to keep open.")


class AppConfig(Config):
    """Run a report against the widget database."""

    model_config = settings(app_name="myapp")

    report: str = Field(description="Which report to run.")

    database: Database = Database()


if __name__ == "__main__":
    cfg = AppConfig.load()
    size = cfg.database.pool_size
    print(f"{cfg.report} on {cfg.database.host} with a pool of {size}")
    print(f"pool size came from {cfg.origin_of('database.pool_size').describe()}")
