"""Database plumbing: the declarative Base, the engine, and the session factory.

Deliberately empty of re-exports. Importing from the specific submodule
(`from app.db.base import Base`, `from app.db.session import get_db`) keeps
`base` importable without `session`, so anything that only needs the metadata —
the models, tests, a future Alembic env — never triggers engine construction
and never needs valid database settings to load.
"""
