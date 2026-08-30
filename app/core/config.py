"""Application configuration.

Centralizes all settings in one typed object loaded from environment
variables / the .env file. Anything that needs config imports `settings`
from here rather than reading os.environ directly, so there's a single,
validated source of truth.
"""

from typing import Literal, Self

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The placeholder that ships in .env.example so the app boots on a fresh clone.
# Named as a constant because `_reject_default_secret_in_production` below has to
# compare against the exact same string — two copies of a magic value is how a
# guard like that quietly stops guarding anything.
DEV_SECRET_KEY = "dev-only-insecure-secret-change-me"


class Settings(BaseSettings):
    """Every setting the app has, declared once with a type and a default.

    Subclassing `BaseSettings` means each field is populated from the matching
    environment variable (case-insensitively: `postgres_port` <- `POSTGRES_PORT`)
    and *validated* against its annotation. `postgres_port: int` isn't
    decoration — a non-numeric PORT fails loudly at startup rather than
    surfacing as a confusing connection error later.
    """

    # App metadata
    app_name: str = "Personal Finance Tracker"
    # Drives FastAPI's debug mode AND SQLAlchemy's `echo` (see db/session.py),
    # so flipping one flag in .env turns on SQL logging too.
    debug: bool = False

    # Which deployment stage this process is. Deliberately *not* folded into
    # `debug`: the two answer different questions. `debug` is about verbosity
    # (do I want to see the SQL?), which you might legitimately want on
    # temporarily anywhere. This is about trust — whether real users' data is on
    # the other end — and it's what the secret-key guard below keys off.
    #
    # A `Literal` rather than a `str` so a typo (`producton`) fails loudly at
    # startup. Typed as a plain string it would just silently not match
    # "production", and a guard that quietly stops firing is worse than none.
    environment: Literal["development", "staging", "production"] = "development"

    # --- Database ---
    # Two ways to configure the same thing, on purpose:
    #   - the POSTGRES_* parts are convenient for local development
    #   - a single DATABASE_URL is what hosting platforms (Heroku, Render,
    #     Docker Compose, CI) actually hand you
    # `database_url` is Optional so "unset" is distinguishable from "empty",
    # which is what lets sqlalchemy_url below know whether to use it.
    database_url: str | None = None
    postgres_user: str = "finance"
    postgres_password: str = "change-me"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "finance_tracker"

    # --- Auth / JWT ---
    # The HMAC signing key. This single value is what makes a token
    # unforgeable: anyone can *read* a JWT (the payload is base64, not
    # encrypted), but only someone holding this key can produce a signature the
    # server accepts. Leak it and every account is impersonable; rotate it and
    # every issued token stops validating — which is also the emergency
    # "log everyone out" switch.
    secret_key: str = DEV_SECRET_KEY

    # HS256 = HMAC-SHA256: one shared secret both signs and verifies. Right
    # choice here because the same app does both. The alternative family (RS256)
    # signs with a private key and verifies with a public one — worth it only
    # when a *different* service must verify tokens without being able to mint
    # them.
    jwt_algorithm: str = "HS256"

    # Short by design. A JWT is self-contained: the server checks the signature
    # and expiry and asks the database nothing, which is what makes it fast and
    # also means there's no list to delete from to revoke one early. Lifetime is
    # therefore the *only* bound on a stolen token, so it's minutes, not days.
    # (The usual next step is a long-lived refresh token stored server-side, so
    # revocation has something to revoke — a later milestone.)
    access_token_expire_minutes: int = 30

    # --- CORS ---
    # Which browser origins may call this API.
    #
    # Needed the moment a frontend exists on a different origin, which in
    # development it always does: Vite serves on :5173 and this serves on :8000,
    # and to a browser those are different origins. Without this the browser
    # blocks every request before it is sent, and the failure looks like a
    # network error rather than a policy decision.
    #
    # An explicit list, never `["*"]`. The wildcard cannot be combined with
    # credentialed requests, and it means any page on the internet can call this
    # API with a user's session — the entire attack CORS exists to prevent.
    #
    # Comma-separated in the environment (`CORS_ORIGINS=https://a.com,https://b.com`)
    # because that is what a deployment platform's env-var field can hold;
    # `_split_cors_origins` below turns it into a list.
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Accept a comma-separated string as well as a real list.

        pydantic-settings parses a `list[str]` field from the environment as
        JSON, so `CORS_ORIGINS=https://app.vercel.app` fails to parse and
        `["https://app.vercel.app"]` is what it demands instead. Requiring JSON
        quoting inside a hosting dashboard's text box is a papercut that costs
        someone an afternoon, so a plain comma-separated string is accepted too.
        """
        if isinstance(value, str) and not value.strip().startswith("["):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    # env_file: read a local .env for development. Real environment variables
    #   still win, which is what keeps production secrets out of files.
    # extra="ignore": don't crash on unrelated variables that happen to be in
    #   the environment (PATH, CI runner vars, ...) — only claim the ones declared above.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def _reject_default_secret_in_production(self) -> Self:
        """Refuse to start with the placeholder signing key outside development.

        A default that's convenient in development is a vulnerability in
        production: the placeholder is in this repository, so *anyone* could
        sign a token claiming to be user 1. The usual failure mode is that
        nobody notices, because a bad secret breaks nothing visible — the app
        works perfectly, it just also accepts forged tokens.

        Crashing at import time is the point: the process dies on boot rather
        than serving traffic it shouldn't. Failing *closed* like this is the
        rule for security defaults — the alternative, logging a warning, gets
        scrolled past.
        """
        if self.environment != "development" and self.secret_key == DEV_SECRET_KEY:
            raise ValueError(
                "SECRET_KEY is still the development placeholder. Generate a real "
                'one with:  python -c "import secrets; print(secrets.token_hex(32))"'
            )
        return self

    @property
    def sqlalchemy_url(self) -> str:
        """The connection URL SQLAlchemy will use.

        A property rather than a field so the precedence rule — an explicit
        DATABASE_URL beats the assembled parts — lives in exactly one place.
        If callers assembled this string themselves, every one of them would be
        a chance to get the precedence subtly different.
        """
        if self.database_url:
            return self.database_url
        # "postgresql+psycopg2" = dialect + driver. The dialect tells SQLAlchemy
        # which SQL to generate; the driver is the DBAPI package that talks to
        # the server. Swapping the driver (say, to asyncpg) touches only this line.
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


# Instantiated once at import time and shared — Python caches modules, so every
# `from app.core.config import settings` gets this same object. That means the
# .env file is parsed once per process, and the whole app provably agrees on
# what the configuration is.
settings = Settings()
