"""Transaction CRUD, and the ownership rule that runs through all of it.

Half of these tests are about a second user's data being invisible. That is not
padding — `WHERE user_id = :me` is a single clause that a refactor can drop
without breaking anything visible, and the endpoint keeps working perfectly for
the person testing it by hand. The suite is the only thing that notices.
"""

from datetime import date

from app.models import Transaction


def test_create_records_a_transaction_owned_by_the_token(
    client, db_session, auth_headers, account, categories, other_user
):
    """201, and `user_id` comes from the token rather than from the request.

    The body cannot express ownership — `TransactionCreate` has no `user_id`
    field — so the first act asserts the value the handler chose, read back from
    the database rather than from the response (`TransactionRead` deliberately
    doesn't echo it).

    The second act is the other half of the same property: a body that *tries*
    to say who the row belongs to is refused outright. Without `extra="forbid"`
    on the schema that would be a 201 — Pydantic's default is to drop unknown
    fields, so the request would look like it worked. Failing loudly turns "the
    attempt was ineffective" into "the attempt was refused", and it catches the
    honest version of the same mistake, a client typo like `catagory_id`.
    """
    response = client.post(
        "/transactions",
        headers=auth_headers,
        json={
            "account_id": account.id,
            "category_id": categories["Groceries"].id,
            "amount": "45.20",
            "type": "expense",
            "occurred_on": "2026-03-04",
            "description": "Weekly shop",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    # Money is a JSON string, not a number — an IEEE double can't hold every
    # cent value exactly, and this API refuses to round-trip through one.
    assert body["amount"] == "45.20"
    # Derived on the way out, never stored: expenses come back negative.
    assert body["signed_amount"] == "-45.20"
    assert "user_id" not in body

    stored = db_session.get(Transaction, body["id"])
    assert stored.user_id == account.user_id

    smuggled = client.post(
        "/transactions",
        headers=auth_headers,
        json={
            "account_id": account.id,
            "user_id": other_user.id,
            "amount": "10.00",
            "type": "expense",
            "occurred_on": "2026-03-04",
        },
    )
    assert smuggled.status_code == 422


def test_create_refuses_references_the_caller_may_not_use(
    client, auth_headers, account, other_account, categories
):
    """Two bad references, two deliberately different status codes.

    **Someone else's account is a 404, not a 403.** 403 would confirm the
    account exists, which turns a body field into an enumeration oracle: post
    junk transactions at ascending ids and you learn how many accounts the app
    holds. 404 makes "not yours" and "not there" indistinguishable from outside.

    **A wrong-side category is a 422.** The one place a different code is right:
    the category exists and it *is* the caller's, so there is nothing to
    conceal. The request is well-formed and its meaning is impossible. This is
    also the check Pydantic structurally cannot do — both values are
    individually valid, and the contradiction only appears once a row has been
    fetched to compare against.
    """
    foreign_account = client.post(
        "/transactions",
        headers=auth_headers,
        json={
            "account_id": other_account.id,
            "amount": "10.00",
            "type": "expense",
            "occurred_on": "2026-03-04",
        },
    )
    assert foreign_account.status_code == 404

    wrong_side = client.post(
        "/transactions",
        headers=auth_headers,
        json={
            "account_id": account.id,
            "category_id": categories["Salary"].id,
            "amount": "45.20",
            "type": "expense",
            "occurred_on": "2026-03-04",
        },
    )
    assert wrong_side.status_code == 422
    assert "cannot be used on an expense transaction" in wrong_side.text


def test_list_returns_only_the_callers_rows_newest_first(
    client, auth_headers, user, other_user, account, other_account, make_transaction
):
    """The scope, and the sort.

    Two users each own two transactions, and the caller sees exactly their own.
    Note the second user's rows are dated *between* the caller's, so a broken
    scope shows up in the ordering as well as the count — a test where the other
    user's data sorts to the end can pass by accident.
    """
    make_transaction(user, account, "10.00", "expense", date(2026, 3, 1))
    theirs = make_transaction(other_user, other_account, "999.00", "expense", date(2026, 3, 2))
    make_transaction(user, account, "20.00", "expense", date(2026, 3, 3))
    make_transaction(other_user, other_account, "888.00", "income", date(2026, 3, 4))

    response = client.get("/transactions", headers=auth_headers)

    assert response.status_code == 200
    amounts = [row["amount"] for row in response.json()]
    assert amounts == ["20.00", "10.00"]

    # Asking for one of their rows by id is a 404, not a 403. The row genuinely
    # exists, which is what makes this assertion mean something — against an
    # empty database a 404 proves nothing.
    assert client.get(f"/transactions/{theirs.id}", headers=auth_headers).status_code == 404


def test_list_filters_by_type_and_date_range(
    client, auth_headers, user, account, make_transaction
):
    """The filters narrow the scope; they never widen it.

    Also pins the 422 on a backwards range. Left unchecked that isn't an error
    at all, just a `WHERE` that matches nothing — which reads to a user as
    "my transactions vanished" rather than as a mistake they made.
    """
    make_transaction(user, account, "10.00", "expense", date(2026, 1, 15))
    make_transaction(user, account, "20.00", "expense", date(2026, 2, 15))
    make_transaction(user, account, "3000.00", "income", date(2026, 2, 20))

    february = client.get(
        "/transactions",
        headers=auth_headers,
        params={"type": "expense", "date_from": "2026-02-01", "date_to": "2026-02-28"},
    )
    assert february.status_code == 200
    assert [row["amount"] for row in february.json()] == ["20.00"]

    backwards = client.get(
        "/transactions",
        headers=auth_headers,
        params={"date_from": "2026-03-01", "date_to": "2026-01-01"},
    )
    assert backwards.status_code == 422


def test_patch_changes_only_the_fields_mentioned(
    client, db_session, auth_headers, user, account, categories, make_transaction
):
    """The reason this endpoint is PATCH and not PUT.

    One field is sent; every other field must survive. A `model_dump()` without
    `exclude_unset=True` in the handler would rewrite the unmentioned columns
    with their defaults and silently wipe the description — a data-loss bug that
    looks like a successful request.
    """
    existing = make_transaction(
        user,
        account,
        "45.20",
        "expense",
        date(2026, 3, 4),
        category=categories["Groceries"],
        description="Weekly shop",
    )

    response = client.patch(
        f"/transactions/{existing.id}",
        headers=auth_headers,
        json={"amount": "50.00"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["amount"] == "50.00"
    assert body["description"] == "Weekly shop"
    assert body["category_id"] == categories["Groceries"].id
    assert body["occurred_on"] == "2026-03-04"

    db_session.expire_all()
    assert db_session.get(Transaction, existing.id).description == "Weekly shop"


def test_delete_removes_the_row_and_is_not_repeatable(
    client, auth_headers, user, account, make_transaction
):
    """204 with no body, then 404 — which is the honest answer the second time.

    The repeat 404 is also, correctly, indistinguishable from deleting an id
    that was never yours.
    """
    existing = make_transaction(user, account, "10.00", "expense", date(2026, 3, 1))

    first = client.delete(f"/transactions/{existing.id}", headers=auth_headers)
    assert first.status_code == 204
    assert first.content == b""

    second = client.delete(f"/transactions/{existing.id}", headers=auth_headers)
    assert second.status_code == 404


def test_every_transaction_route_requires_authentication(client, account):
    """No token, no access — checked across the whole surface at once.

    Protection here comes from `CurrentUser` in each handler's signature rather
    than from URL matching in middleware, so a new route is unprotected only if
    someone actively left the parameter off. This test is what notices.
    """
    routes = [
        ("get", "/transactions"),
        ("post", "/transactions"),
        ("get", "/transactions/1"),
        ("patch", "/transactions/1"),
        ("delete", "/transactions/1"),
    ]

    for method, path in routes:
        response = getattr(client, method)(path)
        assert response.status_code == 401, f"{method.upper()} {path} was not protected"
