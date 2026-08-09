"""Request/response shapes for users.

This file is where the models/schemas split stops being theory. `User` (the
SQLAlchemy model) has a `hashed_password` column. None of the schemas below
declare that field, so it cannot appear in a response — not because a route
remembers to strip it, but because the response model has no slot for it.
FastAPI serializes *through* the schema, discarding anything the schema doesn't
name. Security by construction rather than by vigilance.

Three shapes for one concept, because the directions genuinely differ:

    UserCreate  →  what a client may SEND      (password, no id)
    UserRead    →  what the API SENDS BACK     (id, no password)
    User        →  what PostgreSQL STORES      (hashed_password, timestamps)

Collapse them into one and you get either a client that can set its own `id`
and `is_active`, or a response that leaks a password hash.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.security import MAX_PASSWORD_BYTES


class UserBase(BaseModel):
    """Fields shared by what we accept and what we return."""

    # `EmailStr` is validation, not documentation: a malformed address is
    # rejected with a 422 before any handler code runs. Worth having on the
    # column that is also the login identifier — a typo'd address is an account
    # nobody can recover.
    email: EmailStr
    full_name: str | None = Field(default=None, max_length=255)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        """Lowercase the address so uniqueness means what users expect.

        `Foo@Example.com` and `foo@example.com` are the same mailbox in
        practice, but different strings to a UNIQUE index — so without this,
        both register successfully and the second person is quietly locked out
        of the account they think they made. Normalizing on the way *in* means
        the database constraint and human intuition agree.

        (Strictly, only the domain is case-insensitive per RFC 5321; the local
        part may be case-sensitive. Every mail provider people actually use
        treats it as insensitive, so folding the whole thing is the pragmatic
        call — and it's the one that prevents the duplicate-account bug.)
        """
        return value.strip().lower()


class UserCreate(UserBase):
    """The body of POST /auth/register."""

    # min_length is the floor, not the ceiling of what matters — length beats
    # complexity rules, which mostly produce `Password1!` and a sticky note.
    #
    # max_length exists for two separate reasons. The hard one: bcrypt ignores
    # everything past 72 bytes (see MAX_PASSWORD_BYTES), so accepting more would
    # be a lie about what's being checked. The other: bcrypt's cost is what
    # protects the hash, and an unbounded field lets someone POST a 10 MB
    # "password" repeatedly to burn CPU.
    password: str = Field(min_length=8, max_length=MAX_PASSWORD_BYTES)

    @field_validator("password")
    @classmethod
    def _within_bcrypt_limit(cls, value: str) -> str:
        """Enforce bcrypt's 72-*byte* limit, which `max_length` can't express.

        `max_length` counts characters. bcrypt truncates bytes. For ASCII those
        are the same number, but "é" is 2 bytes in UTF-8 and an emoji is 4 — so
        a 72-character passphrase can be 288 bytes, sail past `max_length`, and
        get silently cut to a quarter of itself at hashing time.
        """
        if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError(
                f"password must be at most {MAX_PASSWORD_BYTES} bytes when UTF-8 "
                "encoded (non-ASCII characters count as more than one byte)"
            )
        return value


class UserRead(UserBase):
    """What every endpoint returns when it returns a user.

    Note what's absent: no `password`, no `hashed_password`. The hash never
    leaves the database, and this class is the reason.
    """

    id: int
    is_active: bool
    created_at: datetime

    # Lets FastAPI build this from a SQLAlchemy `User` object directly.
    # Without it Pydantic only populates from dicts, and returning an ORM
    # instance would fail — this is what makes `return user` in a route work.
    model_config = ConfigDict(from_attributes=True)
