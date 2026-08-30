"""Request/response shapes for categories.

The `type` field is the one that matters here. A category belongs to one side of
the ledger, which is what lets the transaction form show only expense categories
when logging an expense — and what `routers/transactions.py` enforces when it
refuses to file groceries under "Salary". Making it required on create is how
that invariant starts being true.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import TransactionType

STRICT_INPUT = ConfigDict(extra="forbid")


class CategoryCreate(BaseModel):
    """The body of POST /categories."""

    model_config = STRICT_INPUT

    name: str = Field(min_length=1, max_length=80)
    type: TransactionType


class CategoryRead(BaseModel):
    """What the categories endpoints return."""

    id: int
    name: str
    type: TransactionType
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
