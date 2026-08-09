"""The User model — the root of every ownership chain in this app.

Every other row (account, category, transaction) traces back to exactly one
user. That is what makes "show me *my* data" a `WHERE user_id = :me` rather
than a trust exercise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    # Imported for type checkers only — at runtime SQLAlchemy resolves the
    # string names ("Account", ...) through the registry on Base. This is how
    # models can reference each other without circular imports.
    from app.models.account import Account
    from app.models.category import Category
    from app.models.transaction import Transaction


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)

    # unique=True creates a unique index, which does double duty: it enforces
    # "one account per email" in the database and makes login lookups by email
    # an index scan instead of a full table scan.
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255))

    # Never the password itself — only a bcrypt hash, written by
    # `app.core.security.hash_password` and never read outside
    # `verify_password`. A database dump therefore leaks work factors, not
    # credentials.
    #
    # 255 is generous: a bcrypt hash is always exactly 60 characters. The slack
    # is deliberate — it leaves room to migrate to a longer format (argon2id
    # runs ~95+) without an ALTER on a table full of rows.
    #
    # No schema in app/schemas/ declares this field, which is what keeps it out
    # of every API response by construction rather than by vigilance.
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    # --- Relationships -----------------------------------------------------
    # A user has many accounts / categories / transactions.
    #
    # Note there is no `user` column here. The foreign key lives on the *child*
    # table (accounts.user_id) — that's what "one-to-many" means physically.
    # `relationship()` adds no column at all; it's a Python-level convenience
    # that turns that foreign key into an attribute you can read and append to.
    #
    # The annotation is what declares the direction: `Mapped[list[Account]]`
    # (a collection) is the "one" side, while `Mapped[User]` over on Account is
    # the "many" side. SQLAlchemy reads the type hint to decide.
    #
    # `back_populates` names the attribute on the other side, so assigning
    # either end updates both in memory — SQLAlchemy treats them as two views of
    # one relationship rather than two independent ones that can silently disagree.
    #
    # cascade="all, delete-orphan": deleting a user deletes their rows, and
    # detaching a child (removing it from the list) deletes it rather than
    # leaving an orphan pointing at nothing.
    #
    # passive_deletes=True: trust the FK's ON DELETE CASCADE to do the delete
    # in one statement instead of SQLAlchemy loading every child row into
    # memory to delete them one by one.
    accounts: Mapped[list[Account]] = relationship(
        back_populates="owner",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    categories: Mapped[list[Category]] = relationship(
        back_populates="owner",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="owner",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"
