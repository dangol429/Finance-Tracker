"""The aggregation endpoints.

These are the tests that require PostgreSQL rather than a stand-in: every
number below is produced by `GROUP BY`, `date_trunc` and `SUM(...) FILTER
(WHERE ...)`, none of which SQLite has. They are also the endpoints where a
scoping bug is hardest to spot by eye — a leaked row in a list has an id
somebody can notice is wrong, while a leaked row in a total is just a number
that is slightly too big.
"""

from datetime import date


def test_monthly_fills_empty_months_with_zeros(
    client, auth_headers, user, account, make_transaction
):
    """January and March have data; February must still appear.

    `GROUP BY` correctly emits no row for a month with no transactions. A chart
    that skips from January to March draws a line implying February didn't
    happen, so the gap is filled in the API — once, rather than by every
    consumer inventing its own answer.

    Note the money is a JSON *string*. That is deliberate and shared with every
    other endpoint here: a JSON number is an IEEE double, and a total a double
    can't hold exactly would come back differing from the database in its last
    decimal — on precisely the figures a user checks against their bank.
    """
    make_transaction(user, account, "3000.00", "income", date(2026, 1, 10))
    make_transaction(user, account, "1500.00", "expense", date(2026, 1, 20))
    make_transaction(user, account, "500.00", "expense", date(2026, 3, 5))

    response = client.get(
        "/summary/monthly",
        headers=auth_headers,
        params={"date_from": "2026-01-01", "date_to": "2026-03-31"},
    )

    assert response.status_code == 200, response.text
    months = response.json()["months"]

    assert [m["month"] for m in months] == ["2026-01", "2026-02", "2026-03"]
    assert months[0] == {
        "month": "2026-01",
        "month_start": "2026-01-01",
        "income": "3000.00",
        "expense": "1500.00",
        "net": "1500.00",
        "transaction_count": 2,
    }
    assert months[1]["net"] == "0.00"
    assert months[1]["transaction_count"] == 0
    assert months[2]["net"] == "-500.00"


def test_by_category_shares_the_total_and_keeps_uncategorized(
    client, auth_headers, user, account, categories, make_transaction
):
    """Slices sum to the total, largest first, with a bucket for no category.

    The uncategorized row is the reason the query behind this is a LEFT JOIN.
    `category_id` is nullable by design — an imported or just-entered
    transaction is legitimately uncategorized — and an inner join would drop
    those rows, leaving slices that add up to less than the total they claim to
    divide.
    """
    make_transaction(
        user, account, "300.00", "expense", date(2026, 2, 1), category=categories["Groceries"]
    )
    make_transaction(
        user, account, "100.00", "expense", date(2026, 2, 2), category=categories["Groceries"]
    )
    make_transaction(user, account, "100.00", "expense", date(2026, 2, 3))
    # Income, and therefore not part of an expense breakdown at all.
    make_transaction(
        user, account, "5000.00", "income", date(2026, 2, 4), category=categories["Salary"]
    )

    response = client.get("/summary/by-category", headers=auth_headers)

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["type"] == "expense"
    assert body["total"] == "500.00"
    assert body["transaction_count"] == 3

    slices = body["categories"]
    assert [s["category_name"] for s in slices] == ["Groceries", "Uncategorized"]
    assert slices[0]["total"] == "400.00"
    assert slices[0]["share"] == "80.00"
    assert slices[0]["average"] == "200.00"
    assert slices[1]["category_id"] is None
    assert slices[1]["share"] == "20.00"


def test_income_vs_expense_distinguishes_undefined_from_zero(
    client, auth_headers, user, account, make_transaction
):
    """Spending with no income at all: `savings_rate` is null, `average` is zero.

    The two halves of that sentence are the test. Percent-of-income is undefined
    with no income, and both candidate lies are bad — `0` reads as "saved
    nothing" (false; there was nothing to save) and `-100` reads as a
    catastrophe. Null makes a gauge render a gap, the honest picture of a
    question with no answer.

    `average` on the same object goes the other way and *is* zero rather than
    null, because "you earned nothing, so your average income is nothing" is a
    true statement where a made-up rate would not be. SQL returns NULL for both;
    the API passes one on and resolves the other, and which is which is the
    judgement worth pinning down.

    Then the ordinary case, for contrast: 400 saved out of 1000 is 40%.
    """
    make_transaction(user, account, "40.00", "expense", date(2026, 4, 1))
    make_transaction(user, account, "60.00", "expense", date(2026, 4, 2))

    response = client.get("/summary/income-vs-expense", headers=auth_headers)

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["savings_rate"] is None
    assert body["net"] == "-100.00"
    assert body["expense"]["total"] == "100.00"
    assert body["expense"]["largest"] == "60.00"
    assert body["expense"]["average"] == "50.00"
    assert body["income"]["total"] == "0.00"
    assert body["income"]["transaction_count"] == 0
    assert body["income"]["average"] == "0.00"

    make_transaction(user, account, "1000.00", "income", date(2026, 4, 3))
    make_transaction(user, account, "500.00", "expense", date(2026, 4, 4))

    with_income = client.get("/summary/income-vs-expense", headers=auth_headers).json()
    assert with_income["income"]["total"] == "1000.00"
    assert with_income["expense"]["total"] == "600.00"
    assert with_income["net"] == "400.00"
    assert with_income["savings_rate"] == "40.00"


def test_aggregates_never_include_another_users_money(
    client, auth_headers, user, other_user, account, other_account, make_transaction
):
    """The scoping test, run against all three endpoints at once.

    The second user's transaction is deliberately enormous and dated inside the
    caller's range, so a missing `WHERE user_id = :me` cannot fail to show up.
    This is the failure the other tests in this file could not catch: every
    assertion above would still pass with a broken scope, because the caller's
    own numbers would be correct — just with someone else's added on.
    """
    make_transaction(user, account, "100.00", "expense", date(2026, 6, 15))
    make_transaction(other_user, other_account, "99999.00", "expense", date(2026, 6, 15))
    make_transaction(other_user, other_account, "88888.00", "income", date(2026, 6, 16))

    monthly = client.get("/summary/monthly", headers=auth_headers).json()
    assert [m["expense"] for m in monthly["months"]] == ["100.00"]
    assert monthly["months"][0]["income"] == "0.00"

    by_category = client.get("/summary/by-category", headers=auth_headers).json()
    assert by_category["total"] == "100.00"

    both_sides = client.get("/summary/income-vs-expense", headers=auth_headers).json()
    assert both_sides["expense"]["total"] == "100.00"
    assert both_sides["income"]["total"] == "0.00"
