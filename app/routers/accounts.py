"""Account routes — the places money sits.

    GET  /accounts     list the caller's accounts    200
    POST /accounts     create one                    201

Two endpoints, deliberately. There is no PATCH and no DELETE yet: renaming an
account is a nice-to-have, and deleting one cascades to every transaction
recorded against it (`ON DELETE CASCADE` on the FK), which is a destructive
operation that deserves a confirmation flow and a considered answer to "where
did my history go" — not a route added in passing because the set looked
incomplete.

Ownership works exactly as it does in `routers/transactions.py`: the list is
scoped in the `WHERE` clause, and the `user_id` written on create comes from the
token. There is no `user_id` field on `AccountCreate` for a request to set.
"""

from collections.abc import Sequence

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentUser, DbSession
from app.models.account import Account
from app.schemas.account import AccountCreate, AccountRead

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("", response_model=list[AccountRead], summary="List the caller's accounts")
def list_accounts(current_user: CurrentUser, db: DbSession) -> Sequence[Account]:
    """Every account this user owns, oldest first.

    No `limit` and no pagination, unlike the ledger. That is not an oversight:
    accounts are a hand-maintained list — a person has five, not fifty thousand
    — so the response size is bounded by human patience rather than by history.
    The same reasoning the summary endpoints use for skipping pagination.

    Ordered by `name` so the account dropdown in the UI is stable between
    renders. Without an explicit ORDER BY, PostgreSQL is free to return rows in
    whatever order it finds them, and a select that reshuffles itself on every
    request is a genuinely maddening bug to chase.
    """
    return db.scalars(
        select(Account).where(Account.user_id == current_user.id).order_by(Account.name)
    ).all()


@router.post(
    "",
    response_model=AccountRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
)
def create_account(
    payload: AccountCreate,
    current_user: CurrentUser,
    db: DbSession,
) -> Account:
    """Create an account owned by the caller.

    The 409 comes from `uq_accounts_user_id_name`, which scopes uniqueness to
    the owner — one user can't have two accounts called "Chase Checking", and
    two different users obviously can. Catching the `IntegrityError` rather than
    checking first is the same pattern `routers/auth.py` uses for duplicate
    emails, and it is deliberate: a SELECT-then-INSERT is a race, and the
    constraint is what actually guarantees the rule.
    """
    account = Account(
        user_id=current_user.id,
        name=payload.name.strip(),
        type=payload.type,
        currency=payload.currency.upper(),
    )

    db.add(account)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You already have an account named {payload.name.strip()!r}",
        ) from exc

    db.refresh(account)
    return account
