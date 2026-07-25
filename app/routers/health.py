"""Health-check route.

Each feature area gets its own router module (this is the pattern the rest
of the API will follow: transactions.py, auth.py, ...). main.py just wires
them together with include_router().
"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict[str, str]:
    """Liveness probe. Returns 200 if the app is up."""
    return {"status": "ok"}
