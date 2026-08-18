"""Request/response shapes for transactions.

Four shapes for one concept, because a transaction is read, written, and patched
under genuinely different rules:

    TransactionCreate  →  what a client may SEND to create   (account_id required)
    TransactionUpdate  →  what a client may SEND to patch    (every field optional)
    TransactionRead    →  what the API SENDS BACK            (+ derived signed_amount)
    Transaction        →  what PostgreSQL STORES             (user_id, timestamps)

The field missing from all three input shapes is the important one: **`user_id`**.
A client never states who a transaction belongs to — the router takes that from
the access token. If `TransactionCreate` had a `user_id` field, then "write a row
into someone else's ledger" would be a JSON edit away, and the only thing
stopping it would be a handler remembering to overwrite the value. Leaving the
field out means the request has no way to express the idea at all.

Validation here is deliberately the kind that needs *no database*: is the amount
positive, does it fit `NUMERIC(12, 2)`, is the date sane. Anything that requires
a query — does this account exist, is this category yours — is a different job
and lives in `app/routers/transactions.py`, because Pydantic runs before the
handler and has no session to ask.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.models.enums import TransactionType

# How far ahead of the server's *UTC* date an `occurred_on` may sit.
#
# Zero tolerance would be wrong. The server clocks in UTC while users are spread
# across offsets as far ahead as +14, so it is routinely "tomorrow" in Auckland
# while it is still today here — and rejecting someone's genuinely-today grocery
# run is a far worse failure than accepting one that is a day early.
FUTURE_DATE_GRACE = timedelta(days=1)


def _reject_far_future(value: date) -> date:
    """Keep `occurred_on` from drifting into the future.

    This is a ledger of money that *moved*, so a date past today is either a
    typo or a feature nobody built yet. The typo is the reason for the check:
    the app's default sort is newest-first, so a fat-fingered `3025-01-04` does
    not merely sit in the data — it pins itself to the top of every page of the
    user's history, forever, until someone finds and fixes the row.

    Genuinely scheduled transactions ("rent leaves on the 1st") are a real
    feature, and the way to add it is a `scheduled` flag plus a job that
    materializes rows when the day arrives — not by loosening this validator
    until future dates are indistinguishable from mistakes.
    """
    latest = datetime.now(UTC).date() + FUTURE_DATE_GRACE
    if value > latest:
        raise ValueError(f"occurred_on cannot be in the future (latest accepted: {latest})")
    return value


def _blank_to_none(value: str | None) -> str | None:
    """Normalize a whitespace-only description to NULL.

    Otherwise `""`, `"   "` and `null` are three ways of saying "no description",
    and every consumer downstream — the UI, a search, an export — has to know all
    three. Collapsing them at the edge means the column has exactly one way to be
    empty, which is the point of normalizing on the way *in* rather than
    defending against it on the way out.
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


# --- Reusable field types --------------------------------------------------
# `Annotated[T, Field(...)]` bundles a type with its constraints into one name.
# Worth doing here because Create and Update declare the same five fields, and
# constraints copy-pasted between two classes are constraints that will
# eventually disagree — the usual shape of that bug being a rule enforced on
# POST and quietly missing on PATCH.

# `gt=0` mirrors the CHECK constraint in the database, and the two are doing
# different jobs: this one produces a 422 with a readable message, that one makes
# a negative amount unrepresentable no matter what wrote the row. Amounts are
# magnitudes here — `type` carries the direction — so a "negative expense" is not
# a refund, it is a row that silently *reduces* a spending total.
#
# `max_digits`/`decimal_places` mirror `NUMERIC(12, 2)` exactly. Note Pydantic
# *rejects* 10.999 rather than rounding it: silently turning a client's number
# into a different number is how a cent goes missing and nobody can say where.
# (Send amounts as JSON strings — `"10.50"` — if you care about the float
# round-trip in between; the type below accepts either.)
Amount = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]

OccurredOn = Annotated[date, AfterValidator(_reject_far_future)]

Description = Annotated[str | None, Field(max_length=255), AfterValidator(_blank_to_none)]

# Foreign keys arrive as ints from JSON, and `gt=0` costs nothing while turning
# `account_id: -1` into a 422 at the edge instead of a pointless round trip to
# PostgreSQL that can only ever return "no such row".
ForeignKeyId = Annotated[int, Field(gt=0)]

# The columns where NULL is a legal value to write, and therefore the only ones
# an explicit `null` in a PATCH body may clear. See `TransactionUpdate`.
#
# Module-level rather than a class attribute on the model: Pydantic v2 claims
# underscore-prefixed class attributes as *private attributes* with their own
# lifecycle, so a plain constant declared inside the class is not the plain
# constant it looks like.
NULLABLE_UPDATE_FIELDS = frozenset({"category_id", "description"})


# Unknown keys in a request body are rejected rather than ignored, on both input
# shapes below.
#
# Pydantic's default is to drop them, which is the wrong default for a hand-typed
# JSON API: `{"amount": "9.99", "catagory_id": 3}` would be accepted, stored
# uncategorized, and report success. The client is then debugging a field it
# believes it sent. Failing on the typo costs one 422 and finds the bug at the
# only moment anyone is looking.
#
# The other thing it closes: fields the API deliberately does not expose — most
# of all `user_id` — stop being silently-ignored keys and become explicit
# errors, so an attempt to write into someone else's ledger gets an answer that
# says no rather than one that looks like it worked.
STRICT_INPUT = ConfigDict(extra="forbid")


class TransactionCreate(BaseModel):
    """The body of POST /transactions."""

    model_config = STRICT_INPUT

    # Required, and validated against *the current user's* accounts in the
    # router — the schema can only check the shape of the number.
    account_id: ForeignKeyId

    # Optional, mirroring the nullable column: a just-entered or freshly-imported
    # transaction is legitimately uncategorized, and forcing a choice here is how
    # you end up with a "Misc" category that swallows a third of the ledger.
    category_id: ForeignKeyId | None = None

    amount: Amount
    type: TransactionType
    occurred_on: OccurredOn
    description: Description = None


class TransactionUpdate(BaseModel):
    """The body of PATCH /transactions/{id} — every field optional.

    **Why PATCH and not PUT.** PUT means "replace the resource with this", so a
    client that wants to fix a typo in the amount has to send every other field
    back too — and any it forgets get wiped. That is a data-loss bug waiting on
    a forgetful client. PATCH means "change what I mention", which is what
    editing a row actually is.

    That choice creates the one subtlety in this file: with every field optional,
    `null` and *absent* must mean different things.

        {"description": null}   →  clear the description
        {}                      →  leave the description alone

    Pydantic gives both the same attribute value (`None`), so the difference
    lives in `model_fields_set` — which is what `model_dump(exclude_unset=True)`
    reads in the router. Only the genuinely nullable columns may be cleared;
    `_validate_patch` below rejects an explicit `null` for the rest, because
    `NOT NULL` would otherwise turn a client's mistake into a 500.
    """

    model_config = STRICT_INPUT

    account_id: ForeignKeyId | None = None
    category_id: ForeignKeyId | None = None
    amount: Amount | None = None
    type: TransactionType | None = None
    occurred_on: OccurredOn | None = None
    description: Description = None

    @model_validator(mode="after")
    def _validate_patch(self) -> Self:
        """Reject the two malformed patches Pydantic can't express in a type.

        An empty body could defensibly be a no-op 200, but it is nearly always a
        client bug — a serializer that dropped the payload, a form that sent
        nothing. Answering 200 hides that; a 422 names it.
        """
        if not self.model_fields_set:
            raise ValueError("request body must contain at least one field to update")

        nulled = sorted(
            name
            for name in self.model_fields_set
            if name not in NULLABLE_UPDATE_FIELDS and getattr(self, name) is None
        )
        if nulled:
            raise ValueError(
                f"these fields cannot be set to null: {', '.join(nulled)} "
                "(omit a field to leave it unchanged)"
            )
        return self


class TransactionRead(BaseModel):
    """What every transaction endpoint returns.

    Note what is *not* here. No `user_id`: every row this API returns belongs to
    the caller by construction, so echoing their own id back on every item in
    every page is noise that also invites clients to start trusting it.

    And no nested `account` / `category` objects, only their ids. Serializing the
    related rows would be friendlier for a client, and it would also mean each
    item in a 50-row page lazy-loads two more — the N+1 query problem, arriving
    as 101 round trips dressed up as one endpoint. Expanding relations is a real
    feature (`?expand=account`), and it is one that has to be built with
    `selectinload` rather than fallen into by adding a field here.
    """

    id: int
    account_id: int
    category_id: int | None
    amount: Decimal
    type: TransactionType
    occurred_on: date
    description: str | None

    # Read straight off the model's `signed_amount` property — a plain Python
    # attribute as far as Pydantic is concerned, which is exactly why
    # `from_attributes` is the setting that makes it work.
    #
    # Derived values belong on the way *out*, not in a column: storing the signed
    # figure alongside `amount` and `type` means three fields that can disagree,
    # and one day two of them will.
    signed_amount: Decimal

    created_at: datetime
    updated_at: datetime

    # Lets FastAPI build this from a SQLAlchemy `Transaction` object directly,
    # which is what makes `return transaction` in a route work.
    model_config = ConfigDict(from_attributes=True)
