# Personal Finance Tracker — Backend

FastAPI + PostgreSQL + SQLAlchemy backend for a personal finance tracker.
JWT auth and Docker deployment come in later milestones; **this is milestone 1
(initial setup): a running app with a health-check endpoint.**

## Quick start

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
copy .env.example .env         # Windows  (cp on macOS/Linux)
# edit .env with your real DB credentials

# 4. Run
uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000/health — you should get `{"status": "ok"}`.
Interactive API docs are at http://127.0.0.1:8000/docs.

## Project structure

```
app/
├── main.py          # Entry point: creates the app, wires routers together
├── core/
│   └── config.py    # Typed settings loaded from .env (single source of truth)
├── routers/
│   └── health.py    # HTTP endpoints, grouped by feature
├── models/          # SQLAlchemy ORM classes  (DB layer — empty for now)
└── schemas/         # Pydantic request/response shapes  (API contract — empty for now)
```

## Why it's laid out this way (the interview answer)

The guiding idea is **separation of concerns**: each folder owns one job, so a
change in one layer doesn't ripple through the others. Concretely:

- **`core/config.py` — one place for configuration.** Settings are loaded and
  *validated* once (via `pydantic-settings`) into a `settings` object that the
  rest of the app imports. Nothing reads `os.environ` scattered across the
  codebase, so there's a single, typed source of truth and no surprise about
  where a value came from.

- **`routers/` — the HTTP layer.** Each file is a `APIRouter` for one feature
  area (health today; `transactions`, `auth` later). `main.py` just calls
  `include_router()` on each. This is what keeps `main.py` thin and lets the API
  grow by *adding a file* rather than by growing one giant file.

- **`models/` vs `schemas/` — the deliberately-split pair.** This is the split
  interviewers usually probe:
  - `models/` = **SQLAlchemy** classes — how data is stored in PostgreSQL.
  - `schemas/` = **Pydantic** classes — what the API accepts and returns.

  Keeping them separate means the database shape and the public API contract can
  evolve independently. You can add an internal DB column without exposing it,
  or hide a field like `password_hash` from responses, without one concern
  leaking into the other.

- **`main.py` stays thin.** It creates the app and registers routers — no
  business logic. The entry point should be boring and easy to read.

**What was deliberately *left out* (and why that's the right call here):** no
service/repository layer, no dependency-injection framework, no separate DB
session module yet — a personal project of this size doesn't need those
abstractions, and adding them early is over-engineering. The structure gives
clean seams to introduce them *if* the project grows, without paying for them
now. Being able to say *why you stopped here* is as much the point as the
structure itself.
