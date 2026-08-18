"""The Transaction model — one movement of money. The table everything else exists to describe.

Sits at the junction of the other three: it belongs to one user, one account,
and one category.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import TransactionType, transaction_type_enum

if TYPE_CHECKING:
    from app.models.account import Account
    from app.models.category import Category
    from app.models.user import User


class Transaction(Base, TimestampMixin):
    __tablename__ = "transactions"
    __table_args__ = (
        # Amounts are magnitudes; `type` carries the direction. Enforced here
        # so a negative expense — which would quietly *reduce* a spending
        # total instead of adding to it — can't be written at all.
        CheckConstraint("amount > 0", name="ck_transactions_amount_positive"),
        # The app's most common read is "my transactions, newest first".
        # A composite index in that exact order lets Postgres satisfy both the
        # filter and the sort from one index, with no sort step.
        #
        # This also covers plain `WHERE user_id = :me` on its own — a composite
        # index is usable by any query that constrains a *prefix* of its
        # columns. So `user_id` and `occurred_on` below deliberately don't
        # carry `index=True`: a second index on the same leading column would
        # be dead weight that every INSERT still has to maintain.
        Index("ix_transactions_user_id_occurred_on", "user_id", "occurred_on"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # --- Foreign keys ------------------------------------------------------
    # user_id is reachable via account.user_id, so this is technically
    # denormalized. It earns its place: ownership checks and the user's
    # transaction feed are the hot path, and this turns them into a single
    # indexed table scan instead of a join on every request. The price is an
    # invariant to uphold in the service layer — a transaction's user must
    # match its account's user.
    # (Indexed via the composite index declared in __table_args__ above.)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # Nullable, unlike the other two. An imported or just-entered transaction
    # is legitimately uncategorized, and deleting a category shouldn't erase
    # spending history — so this is ON DELETE SET NULL, and those rows fall
    # back to "Uncategorized" rather than disappearing.
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"),
        index=True,
    )

    # --- Data --------------------------------------------------------------
    # Exact decimal, like Account.balance — see the note there on why not Float.
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    type: Mapped[TransactionType] = mapped_column(transaction_type_enum, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))

    # Date, not DateTime: this is the day the money moved, as the bank reports
    # it. Storing a spurious 00:00:00 invites timezone bugs where a purchase
    # lands in the wrong month. `created_at` (from the mixin) separately
    # records when the row was written.
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)

    # --- Relationships -----------------------------------------------------
    owner: Mapped[User] = relationship(back_populates="transactions")
    account: Mapped[Account] = relationship(back_populates="transactions")
    # Optional on both the FK and the type: a transaction may have no category.
    category: Mapped[Category | None] = relationship(back_populates="transactions")

    @property
    def signed_amount(self) -> Decimal:
        """Amount with direction applied: negative for expenses.

        For display and running totals. Note this is a plain Python property —
        it works on a loaded object but cannot be used in a `WHERE`/`ORDER BY`.
        Sum in SQL by filtering on `type` instead.
        """
        if self.type is TransactionType.EXPENSE:
            return -self.amount
        return self.amount

    def __repr__(self) -> str:
        return (
            f"<Transaction id={self.id} {self.type.value} "
            f"amount={self.amount} on={self.occurred_on}>"
        )
