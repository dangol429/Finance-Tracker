"""The Category model — what a transaction was *for*: Groceries, Rent, Salary.

Scoped to a user rather than global. A shared category table would mean one
person's custom "Side Hustle" category shows up in everyone else's dropdown,
and renaming it would rewrite the labels on strangers' spending reports.
The cost of that choice is duplicated "Groceries" rows across users, which is
a few hundred bytes; the alternative leaks data between accounts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import TransactionType, transaction_type_enum

if TYPE_CHECKING:
    from app.models.transaction import Transaction
    from app.models.user import User


class Category(Base, TimestampMixin):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_categories_user_id_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(80), nullable=False)

    # Whether this category describes money coming in or going out. Lets the UI
    # show only expense categories when logging an expense, and makes
    # "spending by category" a join that can't accidentally sum in a paycheck.
    type: Mapped[TransactionType] = mapped_column(transaction_type_enum, nullable=False)

    # --- Relationships -----------------------------------------------------
    owner: Mapped[User] = relationship(back_populates="categories")

    # Deliberately NOT delete-orphan: deleting a category must not delete the
    # transactions filed under it. The FK on the other side is ON DELETE SET
    # NULL, so those transactions survive as uncategorized history.
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="category",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Category id={self.id} name={self.name!r} type={self.type.value}>"
