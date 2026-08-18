"""Transaction routes — the app's first real CRUD surface, and the first one
where *whose data is this?* has to be answered on every single request.

Five endpoints, one resource:

    POST   /transactions        create           201
    GET    /transactions        list + filter    200
    GET    /transactions/{id}   read one         200
    PATCH  /transactions/{id}   partial update   200
    DELETE /transactions/{id}   delete           204

**The rule this module exists to enforce.** Every query below is scoped with
`Transaction.user_id == current_user.id`, and that filter is in the `WHERE`
clause — never an `if transaction.user_id != ...` after the row is loaded. The
difference matters more than it looks:

  - The check cannot be forgotten *later*. A `WHERE` clause that isn't there
    makes the query obviously wrong; a missing `if` five lines down the handler
    looks like nothing at all.
  - It cannot be half-applied. There is no window in which someone else's row is
    sitting in memory, one early `return` away from being serialized.
  - It is the same code path for "no such id" and "not yours", which is what
    makes the 404 story below honest rather than a policy that has to be
    remembered at each call site.

**Why unowned rows return 404 and not 403.** 403 means "this exists and you may
not have it" — which tells the caller the row *exists*. Iterate ids and you have
mapped how many transactions the app holds and, with a little care, when other
users are active. Answering 404 makes another user's transaction and a
transaction that was never created literally indistinguishable from outside.
That is the same reasoning that gives login one error message for "no such
account" and "wrong password" (see `routers/auth.py`) — an endpoint should not
answer questions it wasn't asked.

The one place a different code is right: an account and a category that both
exist and *contradict each other* (filing groceries under "Salary"). Nothing is
hidden there, so it is a 422 — the request is well-formed but its meaning is
impossible.
"""

from collections.abc import Sequence
from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, DbSession
from app.models.account import Account
from app.models.category import Category
from app.models.enums import TransactionType
from app.models.transaction import Transaction
from app.schemas.transaction import (
    TransactionCreate,
    TransactionRead,
    TransactionUpdate,
)

router = APIRouter(prefix="/transactions", tags=["transactions"])


# --- Ownership helpers -----------------------------------------------------
#
# Plain functions, not FastAPI dependencies, and typed `Session` rather than
# `DbSession` to say so: the caller passes the session it already has. A
# dependency resolves from the request before the handler runs, and these need
# arguments the handler computes (an id out of a JSON body, a type that may
# itself be changing in the same patch).


def _get_owned_transaction(db: Session, user_id: int, transaction_id: int) -> Transaction:
    """Fetch one of *this user's* transactions, or raise 404.

    Note there is no `db.get(Transaction, transaction_id)` here, even though a
    primary-key fetch is the cheaper call. `db.get` can only ask "does this id
    exist", which would then need a separate ownership check afterwards — and a
    check that can be written separately is a check that can be omitted
    separately. Folding both conditions into one `WHERE` makes "not found" and
    "not yours" the same event, which is exactly the answer this API wants to
    give.
    """
    transaction = db.scalar(
        select(Transaction).where(
            Transaction.id == transaction_id,
            Transaction.user_id == user_id,
        )
    )
    if transaction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction {transaction_id} not found",
        )
    return transaction


def _require_owned_account(db: Session, user_id: int, account_id: int) -> Account:
    """Resolve an `account_id` from a request body against this user's accounts.

    404 rather than 422 for an id that exists but belongs to someone else, on
    the same reasoning as the module docstring: "not yours" and "not there" have
    to look identical, and a body reference is no less of an oracle than a path
    one. A caller who could tell the two apart by POSTing junk transactions
    would learn how many accounts the app holds.
    """
    account = db.scalar(
        select(Account).where(Account.id == account_id, Account.user_id == user_id)
    )
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Account {account_id} not found",
        )
    return account


def _require_owned_category(
    db: Session,
    user_id: int,
    category_id: int,
    transaction_type: TransactionType,
) -> Category:
    """Resolve a `category_id`, and check it agrees with the transaction's type.

    The second half is the interesting one, and it is the check Pydantic
    structurally cannot do: both values are individually valid, and the problem
    only appears when they are compared — against a row that has to be fetched
    first. Validation that needs a database lives here; validation that needs
    only the payload lives in `schemas/transaction.py`.

    Enforcing it is what makes `Category.type` worth having. The column exists so
    "spending by category" is a join that cannot accidentally sum in a paycheck,
    and a single expense filed under an income category is precisely the row that
    breaks that promise — quietly, in a report, months later.

    422 rather than 404 here: the category was found and it is yours, so there is
    nothing to conceal. The request is well-formed and its meaning is impossible,
    which is what 422 is for.
    """
    category = db.scalar(
        select(Category).where(Category.id == category_id, Category.user_id == user_id)
    )
    if category is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Category {category_id} not found",
        )
    if category.type is not transaction_type:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Category {category_id} is an {category.type.value} category and "
                f"cannot be used on an {transaction_type.value} transaction"
            ),
        )
    return category


def _commit_or_conflict(db: Session) -> None:
    """Commit, turning a foreign-key violation into a 409 instead of a 500.

    The ownership checks above are a race by construction: there is a gap between
    the SELECT that proved an account exists and the INSERT that references it,
    and a `DELETE /accounts/{id}` from the same user's other tab can land in
    between. PostgreSQL's foreign key is what actually guarantees the reference
    is real — the checks above exist to produce a good error message in the
    overwhelmingly common case, not to make the constraint redundant.

    Same rule as the duplicate-email handling in `routers/auth.py`: application
    checks give good errors, database constraints give guarantees, and relying on
    only the first is how a 500 reaches a user.
    """
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The referenced account or category no longer exists",
        ) from exc


# --- Endpoints -------------------------------------------------------------


@router.post(
    "",
    response_model=TransactionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record a transaction",
)
def create_transaction(
    payload: TransactionCreate,
    current_user: CurrentUser,
    db: DbSession,
) -> Transaction:
    """Record a movement of money against one of the caller's own accounts.

    `current_user: CurrentUser` is the entire authentication story for this
    route — FastAPI resolves header → token → signature → `User` row before the
    body runs, so by the time this executes there is a real, active user. The
    `user_id` written below comes from that token and from nowhere else, which
    is why `TransactionCreate` has no `user_id` field to override.
    """
    # Validate the references *before* constructing the row, so a bad
    # `account_id` costs a SELECT rather than a failed INSERT and a rollback.
    _require_owned_account(db, current_user.id, payload.account_id)
    if payload.category_id is not None:
        _require_owned_category(db, current_user.id, payload.category_id, payload.type)

    transaction = Transaction(
        # The denormalized `user_id` the model warns about: it is reachable via
        # `account.user_id`, so the two can disagree, and keeping them in step is
        # named there as the service layer's job. This line is that job — and it
        # is only safe because `_require_owned_account` just proved the account
        # belongs to this same user. Copying `payload.account_id` in without that
        # check is how the denormalization turns into corruption.
        user_id=current_user.id,
        account_id=payload.account_id,
        category_id=payload.category_id,
        amount=payload.amount,
        type=payload.type,
        occurred_on=payload.occurred_on,
        description=payload.description,
    )

    db.add(transaction)
    _commit_or_conflict(db)

    # Populates the server-generated columns (id, created_at, updated_at) from
    # the row that was actually written. Needed because `expire_on_commit=False`
    # (see db/session.py) means the object is not refreshed automatically, and
    # `TransactionRead` requires all three.
    db.refresh(transaction)
    return transaction


@router.get(
    "",
    response_model=list[TransactionRead],
    summary="List the current user's transactions",
)
def list_transactions(
    current_user: CurrentUser,
    db: DbSession,
    account_id: Annotated[int | None, Query(gt=0, description="Only this account")] = None,
    category_id: Annotated[int | None, Query(gt=0, description="Only this category")] = None,
    type: Annotated[TransactionType | None, Query(description="income or expense")] = None,
    date_from: Annotated[date | None, Query(description="Inclusive lower bound")] = None,
    date_to: Annotated[date | None, Query(description="Inclusive upper bound")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Sequence[Transaction]:
    """The user's ledger, newest first, filtered and paged.

    **The filters are all optional; the scope is not.** Every other condition
    below is conditional on a query parameter — the `user_id` one is not, and it
    is applied first, before anything a client sent can influence the statement.
    Ownership is not one filter among several here; it is the set that the
    filters narrow.

    **Why `limit` has a default and a ceiling.** An unbounded list endpoint is
    fine right up until one user has 40,000 rows, at which point a single request
    serializes their entire financial history into memory. The default protects
    the caller who forgot to page; the `le=200` protects the server from the
    caller who did it on purpose.
    """
    if date_from is not None and date_to is not None and date_from > date_to:
        # Catchable only here — Pydantic validates each query parameter in
        # isolation, and this is a relationship *between* two of them. Left
        # unchecked it is not an error at all, just a filter that always matches
        # nothing, which reads to a user as "my transactions vanished".
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="date_from must not be after date_to",
        )

    stmt: Select[tuple[Transaction]] = select(Transaction).where(
        Transaction.user_id == current_user.id
    )

    # Note what happens when `account_id` names an account belonging to someone
    # else: this adds a condition that the `user_id` filter has already made
    # unsatisfiable, and the result is an empty list. Deliberately *not* a 404 —
    # an empty page is the one answer that reveals nothing about whether that
    # account exists.
    if account_id is not None:
        stmt = stmt.where(Transaction.account_id == account_id)
    if category_id is not None:
        stmt = stmt.where(Transaction.category_id == category_id)
    if type is not None:
        stmt = stmt.where(Transaction.type == type)
    if date_from is not None:
        stmt = stmt.where(Transaction.occurred_on >= date_from)
    if date_to is not None:
        stmt = stmt.where(Transaction.occurred_on <= date_to)

    # `occurred_on DESC` is the order humans want; `id DESC` is the one that
    # makes paging correct. Without a unique tiebreaker, rows sharing a date have
    # no defined order between two queries, so PostgreSQL is free to return them
    # differently for `offset=0` and `offset=50` — and a row legitimately appears
    # on both pages while another appears on neither. The bug looks like data
    # loss and reproduces roughly never.
    #
    # This sort is why `ix_transactions_user_id_occurred_on` is declared
    # `(user_id, occurred_on)` in that order: the index satisfies the filter and
    # the sort together, and an index is scannable backwards, so DESC costs
    # nothing extra.
    stmt = stmt.order_by(Transaction.occurred_on.desc(), Transaction.id.desc())

    # OFFSET is the honest tool for a page-numbered UI and it degrades on deep
    # pages — the database still walks the skipped rows. The fix when that starts
    # to hurt is keyset pagination ("give me the 50 before this date+id"), which
    # the sort order above is already shaped for.
    return db.scalars(stmt.limit(limit).offset(offset)).all()


@router.get(
    "/{transaction_id}",
    response_model=TransactionRead,
    summary="Get one transaction",
)
def read_transaction(
    transaction_id: int,
    current_user: CurrentUser,
    db: DbSession,
) -> Transaction:
    """Fetch a single transaction by id — 404 unless it is the caller's own."""
    return _get_owned_transaction(db, current_user.id, transaction_id)


@router.patch(
    "/{transaction_id}",
    response_model=TransactionRead,
    summary="Update part of a transaction",
)
def update_transaction(
    transaction_id: int,
    payload: TransactionUpdate,
    current_user: CurrentUser,
    db: DbSession,
) -> Transaction:
    """Change some fields of a transaction, leaving the rest alone.

    The ordering matters: ownership of the row is established *first*, so a
    request for someone else's id gets a 404 without any of the body being
    examined. Validating the payload first would let error messages differ
    between "id you own" and "id you don't", which leaks exactly what the 404
    was protecting.
    """
    transaction = _get_owned_transaction(db, current_user.id, transaction_id)

    # `exclude_unset=True` is what separates "set this to null" from "don't touch
    # this" — both are `None` on the model, and only this dict knows which the
    # client actually sent. Using `model_dump()` here instead would rewrite every
    # unmentioned field with its default and turn a one-field patch into a
    # destructive full replace.
    changes = payload.model_dump(exclude_unset=True)

    # Re-validate against the state the row will have *after* the patch, not the
    # one it has now. The subtle case is a patch that changes only `type`: the
    # category it was already filed under is untouched by this request and may
    # have just become illegal for it, so an expense flipped to income silently
    # keeps its "Groceries" category unless the pair is re-checked together.
    #
    # `.get(key, fallback)` reads correctly here only because `changes` already
    # went through `exclude_unset`: an explicit `{"category_id": null}` is *in*
    # the dict with a `None` value, so it clears the category, while an omitted
    # key is genuinely absent and falls back to the stored one. That distinction
    # is the whole reason the dict is built with `exclude_unset=True`.
    new_type = changes.get("type", transaction.type)
    new_account_id = changes.get("account_id", transaction.account_id)
    new_category_id = changes.get("category_id", transaction.category_id)

    if new_account_id != transaction.account_id:
        # Moving a transaction between accounts is legitimate (it was recorded
        # against the wrong card). Moving it to an account the caller does not
        # own is the whole attack this endpoint has to refuse — and note the
        # denormalized `user_id` stays correct through it, because the target
        # account is proven to belong to the same user.
        _require_owned_account(db, current_user.id, new_account_id)

    if new_category_id is not None and (
        new_category_id != transaction.category_id or new_type is not transaction.type
    ):
        _require_owned_category(db, current_user.id, new_category_id, new_type)

    for field, value in changes.items():
        setattr(transaction, field, value)

    _commit_or_conflict(db)
    # `updated_at` is set by the database (`onupdate=func.now()`), so the value
    # in memory is the old one until the row is read back.
    db.refresh(transaction)
    return transaction


@router.delete(
    "/{transaction_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a transaction",
)
def delete_transaction(
    transaction_id: int,
    current_user: CurrentUser,
    db: DbSession,
) -> None:
    """Delete one of the caller's transactions.

    204 with no body, not 200 with `{"deleted": true}` — the status code already
    says it worked, and inventing a body gives clients something to parse that
    the next endpoint won't have. Note `response_model` is absent here for the
    same reason: declaring one alongside a 204 promises a body that must not be
    sent.

    Repeating the same DELETE returns 404, which is the honest answer to "delete
    the thing at this id" once there is no thing at that id — and it is
    indistinguishable, correctly, from deleting an id that was never yours.
    """
    transaction = _get_owned_transaction(db, current_user.id, transaction_id)
    db.delete(transaction)
    db.commit()
