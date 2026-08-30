"""Category routes — what a transaction was *for*.

    GET  /categories     list the caller's categories    200
    POST /categories     create one                      201

`?type=expense` narrows the list, which is the query the transaction form
actually makes: when logging an expense, offering income categories is offering
a choice the API will reject with a 422. Filtering server-side rather than in
the client keeps that rule in one place.

No DELETE here either, and the reason is different from the accounts one.
Deleting a category is *not* destructive — the FK is `ON DELETE SET NULL`, so
the transactions filed under it survive as uncategorized history — but it is
still a decision with a visible consequence in every past report, and it belongs
with the UI that can explain it.
"""

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.deps import CurrentUser, DbSession
from app.models.category import Category
from app.models.enums import TransactionType
from app.schemas.category import CategoryCreate, CategoryRead

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=list[CategoryRead], summary="List the caller's categories")
def list_categories(
    current_user: CurrentUser,
    db: DbSession,
    type: Annotated[
        TransactionType | None, Query(description="Only income, or only expense")
    ] = None,
) -> Sequence[Category]:
    """The caller's categories, optionally one side of the ledger only.

    As with accounts: the user scope is applied first and unconditionally, and
    the optional filter narrows what it returned. Ordered by name so a dropdown
    doesn't reshuffle between renders.
    """
    stmt = select(Category).where(Category.user_id == current_user.id)
    if type is not None:
        stmt = stmt.where(Category.type == type)
    return db.scalars(stmt.order_by(Category.name)).all()


@router.post(
    "",
    response_model=CategoryRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a category",
)
def create_category(
    payload: CategoryCreate,
    current_user: CurrentUser,
    db: DbSession,
) -> Category:
    """Create a category owned by the caller.

    Note the uniqueness constraint is `(user_id, name)` and does *not* include
    `type` — so one user cannot have both an income "Refunds" and an expense
    "Refunds". That is the existing schema's decision, not this route's, and it
    is defensible: a category name that means two different things depending on
    which side it lands on is a category name that will confuse a report.
    """
    category = Category(
        user_id=current_user.id,
        name=payload.name.strip(),
        type=payload.type,
    )

    db.add(category)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You already have a category named {payload.name.strip()!r}",
        ) from exc

    db.refresh(category)
    return category
