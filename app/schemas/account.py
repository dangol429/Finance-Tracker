"""Request/response shapes for accounts.

Added in the frontend milestone rather than alongside the model, for a reason
worth recording: until there was a browser talking to this API, accounts could
be seeded from a Python shell and nothing needed an endpoint. A web client
cannot do that — a user who has just signed up owns no account, and
`POST /transactions` requires one — so "create an account" stopped being a
convenience and became the first thing a new user must be able to do.

Same rule as `schemas/transaction.py`: no `user_id` on the input shape. The
owner comes from the access token and there is no field through which a request
could say otherwise.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AccountType

STRICT_INPUT = ConfigDict(extra="forbid")


class AccountCreate(BaseModel):
    """The body of POST /accounts."""

    model_config = STRICT_INPUT

    # `min_length=1` after Pydantic strips nothing for us — a name of spaces
    # would satisfy a bare `str` and produce an account labelled with nothing.
    name: str = Field(min_length=1, max_length=120)
    type: AccountType
    # ISO 4217. Fixed width because the standard is, and defaulted so the common
    # case doesn't have to say it.
    currency: str = Field(default="USD", min_length=3, max_length=3)


class AccountRead(BaseModel):
    """What the accounts endpoints return."""

    id: int
    name: str
    type: AccountType
    currency: str

    # Present because it is a real column, and deliberately *not* what the
    # dashboard's "balance" figure is computed from. Nothing in this API
    # maintains it — `routers/transactions.py` says so explicitly, since keeping
    # a running total correct under concurrent writes is a milestone of its own.
    # The frontend derives balance from the aggregation endpoints instead, where
    # the number is computed from the rows that actually exist.
    balance: Decimal

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
