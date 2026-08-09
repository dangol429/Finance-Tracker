"""Create the tables from the models.

    python -m app.db.init_db

Deliberately a standalone script, not something `main.py` runs at startup.
Schema changes are a deploy-time decision — having the web app mutate the
database every time a worker boots is how two processes race each other into
a half-created schema.

This is the *development* tool. `create_all` only issues CREATE TABLE for
tables that don't exist; it will not alter an existing table to match a
changed model. The moment a column changes, this stops being enough and
Alembic migrations take over (a later milestone).
"""

import sys

from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError

# Imported for the side effect: registering every model on Base.metadata.
# Without this the metadata is empty and create_all silently does nothing.
import app.models  # noqa: F401
from app.core.config import settings
from app.db.base import Base
from app.db.session import engine


def init_db() -> None:
    """Create any missing tables, then report what's in the database."""
    print(f"Connecting to: {_safe_url()}", flush=True)

    try:
        Base.metadata.create_all(bind=engine)
    except OperationalError as exc:
        # "Postgres isn't running" is the overwhelmingly common failure here,
        # and a 60-line SQLAlchemy traceback buries the one line that says so.
        print(f"\nCould not connect to the database.\n\n  {exc.orig}", file=sys.stderr)
        print(
            "Check that PostgreSQL is running and that DATABASE_URL / the "
            "POSTGRES_* vars in your .env are correct.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    tables = sorted(inspect(engine).get_table_names())
    print(f"Tables now present ({len(tables)}): {', '.join(tables) or '(none)'}")


def _safe_url() -> str:
    """The connection URL with the password masked, so it's safe to print."""
    from sqlalchemy.engine import make_url

    return make_url(settings.sqlalchemy_url).render_as_string(hide_password=True)


if __name__ == "__main__":
    init_db()
