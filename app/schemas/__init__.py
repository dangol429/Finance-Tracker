"""Pydantic schemas (request/response shapes).

Kept separate from models/ so the API contract is decoupled from the DB layout:
    models/  = SQLAlchemy = how data is STORED
    schemas/ = Pydantic   = what the API ACCEPTS and RETURNS

That split is what lets `User.hashed_password` exist in the database while never
appearing in a response — the response schema simply doesn't declare the field.
Merge the two and every new column becomes a public API change.

Unlike `app/models/__init__.py`, these re-exports are pure convenience: nothing
breaks if a schema isn't listed here, because Pydantic has no registry that
needs populating. Importing every model is load-bearing; importing every schema
is just a shorter import line.
"""

from app.schemas.token import Token
from app.schemas.user import UserBase, UserCreate, UserRead

__all__ = [
    "Token",
    "UserBase",
    "UserCreate",
    "UserRead",
]
