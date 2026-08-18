"""SQLAlchemy ORM models.

Importing every model here is load-bearing, not tidiness. Two things depend
on it:

1. `Base.metadata` only knows about tables whose module has been imported.
   Anything that reads the metadata — `create_all`, Alembic's autogenerate —
   sees an incomplete schema if a model was never imported.
2. Relationships reference each other by string name (`Mapped["Account"]`).
   Those names resolve against the registry when mappers are configured, so
   every class has to be registered before the first query runs.

So: `import app.models` once at startup and the whole schema is live.
"""

from app.db.base import Base
from app.models.account import Account
from app.models.category import Category
from app.models.enums import AccountType, TransactionType
from app.models.transaction import Transaction
from app.models.user import User

__all__ = [
    "Account",
    "AccountType",
    "Base",
    "Category",
    "Transaction",
    "TransactionType",
    "User",
]
