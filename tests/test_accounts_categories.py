"""Accounts and categories — the two lists the frontend needs to exist.

Added alongside the routers rather than after them. These endpoints were written
because a browser client could not function without them (a newly registered
user owns no account, and `POST /transactions` requires one), which makes them
the first thing a new user touches — and therefore a bad place for an untested
403-vs-404 mistake.

Five tests, matching the shape of the routes: create works and is scoped,
duplicates are refused by the database constraint rather than by a check that
races, the type filter does what the transaction form relies on, and `?q=` —
added in the same pass, for the frontend's search box — matches the way a
person typing in a search box expects.
"""

from datetime import date

from app.models import Account, Category
from app.models.enums import AccountType, TransactionType


def test_accounts_are_created_for_the_caller_and_listed_scoped(
    client, db_session, auth_headers, user, other_user
):
    """Create returns 201 with the row, and the list shows only the caller's.

    The second user's account is created directly so it genuinely exists — the
    same reasoning as the transaction scoping tests. A list endpoint that
    returns one row is not evidence of scoping unless there was a second row it
    could have returned.
    """
    db_session.add(Account(user_id=other_user.id, name="Theirs", type=AccountType.CASH))
    db_session.commit()

    created = client.post(
        "/accounts",
        headers=auth_headers,
        json={"name": "Everyday", "type": "checking"},
    )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "Everyday"
    assert body["type"] == "checking"
    # Defaulted rather than required, and normalised to upper case by the router.
    assert body["currency"] == "USD"
    # No `user_id` in the response: every row this API returns belongs to the
    # caller by construction, so echoing it back is noise that invites clients
    # to start trusting it.
    assert "user_id" not in body

    listed = client.get("/accounts", headers=auth_headers).json()
    assert [account["name"] for account in listed] == ["Everyday"]

    stored = db_session.get(Account, body["id"])
    assert stored.user_id == user.id


def test_duplicate_account_name_is_a_409_but_only_for_the_same_user(
    client, db_session, auth_headers, other_user
):
    """`uq_accounts_user_id_name` scopes uniqueness to the owner.

    Both halves matter. A second "Everyday" for the same user is a 409; the
    *same name* owned by somebody else is fine, and a constraint written without
    `user_id` would turn one user naming an account into a global name grab.
    """
    first = client.post(
        "/accounts", headers=auth_headers, json={"name": "Everyday", "type": "checking"}
    )
    assert first.status_code == 201

    duplicate = client.post(
        "/accounts", headers=auth_headers, json={"name": "Everyday", "type": "savings"}
    )
    assert duplicate.status_code == 409
    assert "already have an account" in duplicate.json()["detail"]

    # The same name, a different owner — allowed.
    db_session.add(
        Account(user_id=other_user.id, name="Everyday", type=AccountType.CHECKING)
    )
    db_session.commit()  # would raise IntegrityError if the constraint were global


def test_categories_can_be_filtered_to_one_side_of_the_ledger(
    client, auth_headers, categories
):
    """`?type=expense` is the query the transaction form actually makes.

    Offering income categories while logging an expense offers a choice the API
    rejects with a 422 (`routers/transactions.py` enforces the pair), so the
    filter is what keeps the form from presenting an impossible option.
    """
    everything = client.get("/categories", headers=auth_headers).json()
    assert {category["name"] for category in everything} == {"Groceries", "Salary"}

    expenses = client.get(
        "/categories", headers=auth_headers, params={"type": "expense"}
    ).json()
    assert [category["name"] for category in expenses] == ["Groceries"]
    assert all(category["type"] == "expense" for category in expenses)


def test_transaction_search_matches_descriptions_case_insensitively(
    client, auth_headers, user, account, make_transaction
):
    """`?q=` — added for the frontend's search box.

    Case-insensitive and a substring match, because that is what a search box
    means to the person typing in it. Searching client-side instead would only
    search the page already loaded, which is a search box that lies.
    """
    make_transaction(
        user, account, "12.00", "expense", date(2026, 3, 1), description="Morning COFFEE"
    )
    make_transaction(
        user, account, "40.00", "expense", date(2026, 3, 2), description="Weekly shop"
    )

    hits = client.get("/transactions", headers=auth_headers, params={"q": "coffee"}).json()

    assert [row["description"] for row in hits] == ["Morning COFFEE"]


def test_categories_are_scoped_to_the_caller(client, db_session, auth_headers, other_user):
    """Someone else's categories are not in your list, and cannot be."""
    db_session.add(
        Category(user_id=other_user.id, name="Their Secret", type=TransactionType.EXPENSE)
    )
    db_session.commit()

    listed = client.get("/categories", headers=auth_headers).json()

    assert all(category["name"] != "Their Secret" for category in listed)
