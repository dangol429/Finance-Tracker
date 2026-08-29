"""Application entry point.

Creates the FastAPI app and registers routers. Kept thin on purpose —
it wires things together but contains no business logic itself.

Run locally:  uvicorn app.main:app --reload
"""

from fastapi import FastAPI

# Imported for its side effect: registering every ORM model on Base.metadata
# and in the mapper registry. Without it, the first query against a
# relationship fails to resolve its string target ("Account", "Category", ...).
# It does NOT create tables — that's `python -m app.db.init_db`.
import app.models  # noqa: F401
from app.core.config import settings
from app.routers import auth, health, summary, transactions

# `title` shows up in the generated OpenAPI spec and on the /docs page — the
# API documents itself from this object, so metadata set here isn't cosmetic.
# Both values come from `settings`, never from a literal, so behaviour is
# configured in one place (.env) rather than edited into the source.
app = FastAPI(title=settings.app_name, debug=settings.debug)

# Register feature routers. Add new ones here as the API grows.
#
# This one line is why main.py stays short: each router owns its own URLs and
# handlers, so adding `transactions` later means writing a new file and adding a
# line here — not growing this one. Note main.py imports the routers, never the
# reverse; keeping the dependency arrow pointing one way is what prevents
# circular imports as the app grows.
app.include_router(health.router)

# The auth router carries its own `prefix="/auth"` and tags, so registration
# here stays a single line no matter how many endpoints it grows. Note that
# nothing about *protection* is configured at this level — a route is protected
# by asking for `CurrentUser` in its signature, not by being listed here. That's
# the deliberate difference from middleware, where the app-level wiring decides
# which URLs are guarded and forgetting one fails open.
app.include_router(auth.router)

# The prediction two comments up, cashed in: adding the whole transactions CRUD
# surface — five endpoints, all of them protected and per-user scoped — cost one
# new file and this one line. Nothing above changed, and nothing here says these
# routes require a token; that lives in each handler's signature, where it can't
# be left off by an omission in the wiring.
app.include_router(transactions.router)

# The read side of the same data. Nothing here changes either — a new file, a
# new line, and the dashboard has its three endpoints. Worth noting what these
# routes *don't* need that `transactions` did: no ownership helpers, no 404-vs-
# 403 policy, no `limit` ceiling. Aggregates return one row per group, so the
# scope in the WHERE clause is the whole of the access story and the size of the
# answer is bounded by the calendar rather than by the ledger.
app.include_router(summary.router)
