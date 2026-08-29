"""Fixtures shared by the whole suite.

**These tests run against a real PostgreSQL database, not SQLite.** That costs a
running server — `docker compose up -d db` provides one — and it buys the only
thing a test suite is for, which is finding out whether the code works. SQLite
would be free and would quietly not test:

  - `/summary/*` at all. Those endpoints are built on `date_trunc('month', ...)`
    and `SUM(...) FILTER (WHERE ...)`, which SQLite does not have. The tests
    would error, and the usual fix — skip them — deletes coverage of the most
    intricate code in the project.
  - The `CHECK (amount > 0)` and `ON DELETE SET NULL` behaviour, since SQLite
    enforces foreign keys only when a pragma is set.
  - The `ENUM` types, which are real database objects in PostgreSQL and plain
    strings elsewhere.
  - `INSERT ... RETURNING` batching in the CSV import, which behaves differently
    per dialect (see the note in `routers/csv_import.py`).

A test suite that passes against a database the application will never use is a
suite that reports on a program nobody runs.

**Isolation: one transaction per test, rolled back.** The `db_session` fixture
opens a connection, starts a transaction, and hands the app a `Session` bound to
*that connection*. At the end the outer transaction is rolled back, so the
database is byte-for-byte unchanged and the next test starts from a known empty
state. The alternative — dropping and recreating the schema between tests — is
correct too, and roughly two orders of magnitude slower, which is how suites
become the thing people stop running.

The trick that makes it work is `join_transaction_mode="create_savepoint"`. The
handlers under test call `db.commit()`, and a commit would normally end the
outer transaction and make the writes permanent. With that setting the session's
work happens inside a SAVEPOINT, so its `commit()` releases the savepoint while
the enclosing transaction stays open and rollable-back. The handler's code is
unchanged and unaware; nothing under test is written differently because it is
being tested.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import date
from decimal import Decimal
from functools import cache

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app

# Importing the models by name also registers every mapper on Base.metadata,
# which is what `create_all` below reads. Without some import of this package
# the metadata is empty and the schema is silently created as nothing.
from app.models import Account, Category, Transaction, User
from app.models.enums import AccountType, TransactionType

# Credentials used by every test that needs a logged-in caller. Constants rather
# than literals scattered through the suite so a test reads as "the password",
# not as a magic string that might or might not be the same one.
USER_EMAIL = "alice@example.com"
USER_PASSWORD = "correct-horse-battery"
OTHER_EMAIL = "mallory@example.com"
OTHER_PASSWORD = "another-valid-password"


@cache
def _cached_hash(password: str) -> str:
    """`hash_password`, computed once per distinct password per run.

    bcrypt at cost 12 takes about a quarter of a second *by design* — that cost
    is the security property. Paying it twice in every test to re-derive a hash
    of the same two constant passwords buys nothing: the suite creates the same
    fixture users forty times over and the digests are identical every time.

    Note what this does not do: weaken the algorithm. Nothing lowers the work
    factor, and `/auth/login` still runs a real `verify_password` against a real
    bcrypt hash on every test that logs in — that path is under test, so it pays
    full price. Only the redundant *re-hashing* is cached.
    """
    return hash_password(password)


def _test_database_url() -> str:
    """Where the tests write. Never the development database.

    `TEST_DATABASE_URL` wins if set (CI hands you one); otherwise the app's own
    configured database name with `_test` appended.

    The guard below is not paranoia. This module calls `drop_all`, so pointing
    it at the wrong database destroys real data — and the wrong database is one
    stray `TEST_DATABASE_URL` away. Refusing to start is cheap; the alternative
    is unrecoverable and discovered afterwards.
    """
    configured = os.environ.get("TEST_DATABASE_URL")
    url = make_url(configured) if configured else make_url(settings.sqlalchemy_url).set(
        database=f"{settings.postgres_db}_test"
    )

    if url.database == settings.postgres_db:
        raise RuntimeError(
            f"The test database ({url.database!r}) is the same as the application "
            f"database. These tests drop every table; refusing to run."
        )
    return url.render_as_string(hide_password=False)


def _ensure_database_exists(url: str) -> None:
    """Create the test database if it isn't there yet.

    `CREATE DATABASE` cannot run inside a transaction and cannot be issued
    against the database being created, so this connects to the `postgres`
    maintenance database in AUTOCOMMIT mode — the standard dance, and the reason
    this is five lines rather than one.

    Doing it here rather than in a setup script means `pytest` is the only
    command anyone has to know.
    """
    admin_url = make_url(url).set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    target = make_url(url).database

    try:
        with admin_engine.connect() as conn:
            exists = conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": target})
            if not exists:
                # The name is an identifier, not a bind parameter — those are
                # only for values — so it is interpolated. Safe here because it
                # comes from configuration, not from a request.
                conn.execute(text(f'CREATE DATABASE "{target}"'))
    except OperationalError as exc:
        pytest.exit(
            "Cannot reach PostgreSQL for the test database.\n\n"
            f"  {exc.orig}\n\n"
            "Start one with:  docker compose up -d db\n"
            "Or point the suite elsewhere with TEST_DATABASE_URL.",
            returncode=1,
        )
    finally:
        admin_engine.dispose()


@pytest.fixture(scope="session")
def engine() -> Generator[Engine, None, None]:
    """One engine and one schema for the whole run.

    Session-scoped because creating the tables is the expensive part and it does
    not need repeating — per-test isolation comes from the transaction rollback
    in `db_session`, not from rebuilding the schema.

    `drop_all` runs first as well as last: a previous run killed with Ctrl-C
    leaves tables behind, and starting from whatever they happen to contain is
    how a suite acquires tests that only pass in a particular order.
    """
    url = _test_database_url()
    _ensure_database_exists(url)

    # Pooled, so the per-test `engine.connect()` in `db_session` reuses a live
    # connection instead of opening a TCP socket and re-authenticating each
    # time. Against a containerised Postgres that handshake is a substantial
    # share of a fast test's runtime, and connections returned to the pool are
    # idle — they hold no locks, so `drop_all` at the end is unaffected.
    eng = create_engine(url, future=True)

    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def db_session(engine: Engine) -> Generator[Session, None, None]:
    """A session whose every write is undone when the test ends.

    See the module docstring for why `join_transaction_mode="create_savepoint"`
    is the load-bearing argument: without it, the first `db.commit()` inside a
    handler would end the transaction this fixture is relying on rolling back,
    and tests would start leaking rows into each other.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
        # Mirrors `SessionLocal` in db/session.py, so objects behave in tests
        # the way they behave in the app.
        expire_on_commit=False,
        autoflush=False,
    )

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """The API, wired to the test transaction.

    `dependency_overrides` is FastAPI's seam for exactly this: `get_db` is
    replaced so every handler in the request gets the *same* session this test
    holds, which is what lets a test assert against the database directly after
    calling an endpoint.

    Note what is *not* overridden — nothing to do with authentication. Tests log
    in through `/auth/login` and send real bearer tokens, so the token decoding,
    the user lookup and the `is_active` check are all exercised rather than
    stubbed. Overriding `get_current_user` would make every test faster and
    would stop the suite from ever noticing that authentication broke.
    """
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# --- Seeded data -----------------------------------------------------------
#
# Built through the ORM rather than through the API. Creating a user by POSTing
# to /auth/register would make almost every test depend on registration working,
# so a bug there would fail the entire suite at once and say nothing about where
# it was. Arrange directly, act through the API, assert on the result.


@pytest.fixture
def user(db_session: Session) -> User:
    """The caller. Password is hashed the way the app hashes it, so login works."""
    record = User(email=USER_EMAIL, hashed_password=_cached_hash(USER_PASSWORD))
    db_session.add(record)
    db_session.commit()
    return record


@pytest.fixture
def other_user(db_session: Session) -> User:
    """A second account, existing only to be kept out of the first one's data.

    Most of the interesting assertions in this suite are about what a caller
    *cannot* see, and that needs someone else's rows to actually exist. A test
    that checks ownership against an empty database passes for the wrong reason.
    """
    record = User(email=OTHER_EMAIL, hashed_password=_cached_hash(OTHER_PASSWORD))
    db_session.add(record)
    db_session.commit()
    return record


@pytest.fixture
def auth_headers(client: TestClient, user: User) -> dict[str, str]:
    """A real `Authorization` header, obtained by logging in for real.

    Note the form encoding and the `username` field: `/auth/login` takes an
    OAuth2 password form, not JSON.
    """
    response = client.post(
        "/auth/login",
        data={"username": USER_EMAIL, "password": USER_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def account(db_session: Session, user: User) -> Account:
    """The caller's account. Transactions need one to point at."""
    record = Account(user_id=user.id, name="Chase Checking", type=AccountType.CHECKING)
    db_session.add(record)
    db_session.commit()
    return record


@pytest.fixture
def categories(db_session: Session, user: User) -> dict[str, Category]:
    """One category per side of the ledger, keyed by name for readable tests."""
    groceries = Category(user_id=user.id, name="Groceries", type=TransactionType.EXPENSE)
    salary = Category(user_id=user.id, name="Salary", type=TransactionType.INCOME)
    db_session.add_all([groceries, salary])
    db_session.commit()
    return {"Groceries": groceries, "Salary": salary}


@pytest.fixture
def other_account(db_session: Session, other_user: User) -> Account:
    """The second user's account."""
    record = Account(user_id=other_user.id, name="Their Card", type=AccountType.CREDIT_CARD)
    db_session.add(record)
    db_session.commit()
    return record


@pytest.fixture
def make_transaction(db_session: Session):
    """Factory for transactions, so tests state only what they care about.

    A factory rather than a fixture-per-row: the aggregation tests need five or
    six rows with specific dates and amounts, and spelling each one out as its
    own fixture would bury the shape of the data the test is actually about.
    """

    def _make(
        owner: User,
        acct: Account,
        amount: str,
        type: str,
        occurred_on: date,
        category: Category | None = None,
        description: str | None = None,
    ) -> Transaction:
        record = Transaction(
            user_id=owner.id,
            account_id=acct.id,
            category_id=category.id if category is not None else None,
            amount=Decimal(amount),
            # Coerced rather than passed through, so call sites can stay readable
            # ("expense") while the object still holds a real enum member. Assigning
            # the string works for the INSERT but leaves the in-memory attribute a
            # plain `str` until the row is reloaded — and any code that reaches for
            # `.type.value` on it then fails in tests only.
            type=TransactionType(type),
            occurred_on=occurred_on,
            description=description,
        )
        db_session.add(record)
        db_session.commit()
        return record

    return _make
