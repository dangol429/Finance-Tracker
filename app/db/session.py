"""Engine and session management.

One `Engine` per process (it owns the connection pool), and one `Session` per
request (it owns a unit of work). Mixing those lifetimes up is the classic
SQLAlchemy mistake: a long-lived session accumulates stale objects and holds a
connection open; a per-request engine throws away the pool every time.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# Created once at import time and shared by the whole app.
# - pool_pre_ping: cheaply checks a pooled connection is still alive before
#   handing it out, so a connection dropped by Postgres (restart, idle timeout)
#   surfaces as a reconnect instead of a random error mid-request.
# - echo: mirrors DEBUG, so `DEBUG=true` in .env logs the emitted SQL.
engine = create_engine(
    settings.sqlalchemy_url,
    pool_pre_ping=True,
    echo=settings.debug,
    future=True,
)

# Factory for request-scoped sessions.
# expire_on_commit=False keeps attributes readable after commit(); with the
# default (True), every attribute is expired and the next read fires a fresh
# SELECT — which raises if the session is already closed, the usual cause of
# "Instance is not bound to a Session" when returning an object from a route.
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=Session,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a session scoped to one request.

    Usage:  `def route(db: Session = Depends(get_db)): ...`

    The `finally` is the whole point: whether the route returns or raises, the
    session is closed and its connection goes back to the pool. Commits stay
    the caller's job — the dependency shouldn't decide that a half-finished
    request is worth persisting.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
