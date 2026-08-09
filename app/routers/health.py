"""Health-check routes.

Each feature area gets its own router module (this is the pattern the rest
of the API will follow: transactions.py, auth.py, ...). main.py just wires
them together with include_router().
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict[str, str]:
    """Liveness probe: is the process up? Deliberately touches nothing else."""
    return {"status": "ok"}


@router.get("/health/db")
def db_health_check(db: Session = Depends(get_db)) -> dict[str, str]:
    """Readiness probe: can the app actually reach PostgreSQL?

    Split from `/health` on purpose. Liveness answers "should this container be
    restarted?", readiness answers "should traffic be routed here?" — and a
    dropped database connection is the second, not the first. Collapsing them
    means a brief DB blip gets your healthy app killed.

    `SELECT 1` is the cheapest statement that proves a connection was checked
    out of the pool and a round trip completed.
    """
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        # 503, not 500: the app is fine, its dependency isn't. The distinction
        # is what tells a load balancer to route elsewhere and retry.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from exc
    return {"status": "ok", "database": "connected"}
