"""Application configuration.

Centralizes all settings in one typed object loaded from environment
variables / the .env file. Anything that needs config imports `settings`
from here rather than reading os.environ directly, so there's a single,
validated source of truth.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App metadata
    app_name: str = "Personal Finance Tracker"
    debug: bool = False

    # Database — DATABASE_URL wins if provided; otherwise build from parts.
    database_url: str | None = None
    postgres_user: str = "finance"
    postgres_password: str = "change-me"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "finance_tracker"

    # Reads a local .env file; real env vars still override it.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def sqlalchemy_url(self) -> str:
        """The connection URL SQLAlchemy will use."""
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


# Import this singleton anywhere config is needed.
settings = Settings()
