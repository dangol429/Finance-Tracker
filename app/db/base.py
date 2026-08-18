"""The declarative base every model inherits from.

Kept in its own module (rather than next to the engine) so importing `Base`
never pulls in a database connection. That separation is what lets tooling —
tests, migrations, `create_all` — import the metadata without opening a socket.
"""

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared parent of all ORM models.

    SQLAlchemy 2.0 style: subclassing `DeclarativeBase` gives every model a
    shared `registry` (how string references like `"User"` in relationships get
    resolved) and a shared `metadata` (the collected table definitions that
    `create_all` / Alembic read).
    """


class TimestampMixin:
    """Adds `created_at` / `updated_at` to a model.

    A mixin rather than a base class because it describes a *capability*, not a
    kind of thing — models opt in by inheriting it alongside `Base`.

    Both defaults are `server_default` / `onupdate` at the SQL level, so rows
    written by a migration or a raw `psql` INSERT get timestamps too. If this
    were a Python-side default, anything that bypassed the ORM would leave NULLs.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
