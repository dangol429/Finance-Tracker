"""Enumerations shared by the models.

These are `str, Enum` subclasses so a member is also a plain string: it
serializes straight to JSON, compares equal to `"expense"`, and still gives
autocomplete plus a typo-proof reference in Python.

Stored via SQLAlchemy's `Enum` type, which on PostgreSQL creates a real
`CREATE TYPE ... AS ENUM` and rejects any other value at the database level.
The check lives in the schema, not just in application code, so a bad value
can't get in through a migration or a manual INSERT.
"""

from enum import Enum

from sqlalchemy import Enum as SAEnum

from app.db.base import Base


class AccountType(str, Enum):
    """What kind of account holds the money."""

    CHECKING = "checking"
    SAVINGS = "savings"
    CREDIT_CARD = "credit_card"
    CASH = "cash"
    INVESTMENT = "investment"


class TransactionType(str, Enum):
    """Direction of money movement.

    Amounts are stored as positive numbers and this field carries the sign
    (see `Transaction.amount`), so "sum of expenses" is a filter rather than a
    convention about which rows happen to be negative.
    """

    INCOME = "income"
    EXPENSE = "expense"


# --- The SQL-side types ----------------------------------------------------
# Attached to `Base.metadata` and reused by every column that needs them,
# rather than each column constructing its own `SAEnum(...)`.
#
# On PostgreSQL an enum is a database object, not just a column modifier, so
# two columns each declaring their own `SAEnum(TransactionType,
# name="transaction_type")` make `create_all` emit `CREATE TYPE
# transaction_type` twice — and the second one fails. Binding the type to the
# metadata once gives both columns the same shared object: created once,
# dropped once, altered in one place when a member is added later.
account_type_enum = SAEnum(
    AccountType,
    name="account_type",
    metadata=Base.metadata,
    # Store the *values* ("credit_card"), not the Python member names
    # ("CREDIT_CARD"), so the data reads sensibly in psql.
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)

transaction_type_enum = SAEnum(
    TransactionType,
    name="transaction_type",
    metadata=Base.metadata,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)
