"""Application entry point.

Creates the FastAPI app and registers routers. Kept thin on purpose —
it wires things together but contains no business logic itself.

Run locally:  uvicorn app.main:app --reload
"""

from fastapi import FastAPI

from app.core.config import settings
from app.routers import health

app = FastAPI(title=settings.app_name, debug=settings.debug)

# Register feature routers. Add new ones here as the API grows.
app.include_router(health.router)
