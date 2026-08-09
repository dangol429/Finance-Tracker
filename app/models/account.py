"""The Account model — a place money sits: a checking account, a card, cash.

Owned by one user, and the thing transactions are recorded against.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import AccountType, account_type_enum

if TYPE_CHECKING:
    from app.models.transaction import Transaction
    from app.models.user import User


class Account(Base, TimestampMixin):
    __tablename__ = "accounts"
    __table_args__ = (
        # One user can't have two accounts called "Chase Checking"; two
        # *different* users obviously can. Scoping the uniqueness to the owner
        # is the difference between a sensible rule and a global name grab.
        UniqueConstraint("user_id", "name", name="uq_accounts_user_id_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # The foreign key: a column-level constraint the database enforces. An
    # account row cannot reference a user that doesn't exist, and ondelete
    # CASCADE means deleting the user removes their accounts in the same
    # statement rather than leaving rows pointing at a missing parent.
    # Indexed because "all accounts for this user" is the query this table
    # exists to answer, and FKs are not indexed automatically in PostgreSQL.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    type: Mapped[AccountType] = mapped_column(account_type_enum, nullable=False)

    # Money is Numeric, never Float. Float is binary floating point: 0.1 + 0.2
    # is not 0.3, and those fractions of a cent compound across a ledger.
    # Numeric(12, 2) is exact decimal — up to 10 digits before the point and
    # exactly 2 after — and maps to Python's `Decimal`.
    balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        default=Decimal("0.00"),
        server_default="0.00",
        nullable=False,
    )
    # ISO 4217 code, e.g. "USD". Fixed width because the standard is.
    currency: Mapped[str] = mapped_column(String(3), default="USD", server_default="USD", nullable=False)

    # --- Relationships -----------------------------------------------------
    # The "many" side: a singular `Mapped[User]`, not a list. This is the same
    # relationship as User.accounts viewed from the other end — `back_populates`
    # on both sides is what tells SQLAlchemy they're one link, so
    # `user.accounts.append(acct)` also sets `acct.owner`.
    #
    # Named `owner` rather than `user` because it reads better at call sites
    # (`account.owner.email`); the attribute name is ours to choose, only the
    # `back_populates` string has to match the other side exactly.
    owner: Mapped[User] = relationship(back_populates="accounts")
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Account id={self.id} name={self.name!r} balance={self.balance}>"
