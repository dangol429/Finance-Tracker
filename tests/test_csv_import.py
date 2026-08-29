"""The CSV import endpoint — the happy path and the skip-bad-rows contract.

Three tests, not thirty. The parsers in `routers/csv_import.py` have a lot of
surface (date formats, amount notations, direction synonyms) and exhaustively
testing them is how this stops being a suite you can run and starts being a
project of its own. What these three pin is the *contract*: a mixed file
produces the right rows, a broken row is skipped rather than either imported
wrong or taking the whole upload down with it, and a dry run writes nothing.
"""

import io


def _upload(client, headers, account_id, csv_text, **params):
    return client.post(
        "/transactions/import",
        headers=headers,
        params=params,
        files={"file": ("statement.csv", io.BytesIO(csv_text.encode("utf-8")), "text/csv")},
        data={"account_id": str(account_id)},
    )


def test_import_inserts_the_rows_a_bank_actually_exports(
    client, auth_headers, account, categories
):
    """A statement with the notations real exports use, imported in one batch.

    Every row here is awkward on purpose: a quoted comma inside a description
    (which `line.split(",")` would shift a column), a signed amount, a currency
    symbol with thousands separators wrapped in accounting parentheses, and a
    category named in the wrong case. The point is that the endpoint turns all of
    it into the one shape the model stores — a positive magnitude plus a `type`.
    """
    csv_text = (
        "Transaction Date,Amount,Description,Category\n"
        '2026-03-04,-45.20,"COFFEE, LARGE",groceries\n'
        "2026-03-05,1500.00,March salary,Salary\n"
        '2026-03-06,"($1,234.56)",Rent,\n'
    )

    response = _upload(client, auth_headers, account.id, csv_text)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["total_rows"], body["imported"], body["failed"]) == (3, 3, 0)
    assert body["errors"] == []

    rows = client.get("/transactions", headers=auth_headers).json()
    by_date = {row["occurred_on"]: row for row in rows}

    # Negative in the file, stored as a magnitude with the direction in `type`.
    assert by_date["2026-03-04"]["amount"] == "45.20"
    assert by_date["2026-03-04"]["type"] == "expense"
    # The comma survived, which means the CSV parser handled the quoting.
    assert by_date["2026-03-04"]["description"] == "COFFEE, LARGE"
    # Matched "Groceries" despite being written "groceries".
    assert by_date["2026-03-04"]["category_id"] == categories["Groceries"].id

    assert by_date["2026-03-05"]["type"] == "income"

    assert by_date["2026-03-06"]["amount"] == "1234.56"
    assert by_date["2026-03-06"]["type"] == "expense"
    assert by_date["2026-03-06"]["category_id"] is None


def test_import_dry_run_reports_without_writing(client, auth_headers, account):
    """The preview runs the same code path and stops short of the commit.

    A preview implemented as a separate, simpler validation pass would be a
    preview of a different program — and the rows the two disagreed about would
    be exactly the ones that mattered. So the assertion is a pair: the report
    says two rows would land, and the database still holds nothing.
    """
    csv_text = "date,amount\n2026-03-01,-10.00\n2026-03-02,-20.00\n"

    body = _upload(client, auth_headers, account.id, csv_text, dry_run="true").json()

    assert body["dry_run"] is True
    assert body["imported"] == 2
    assert client.get("/transactions", headers=auth_headers).json() == []


def test_import_skips_bad_rows_and_keeps_the_good_ones(client, auth_headers, account):
    """One bad row is not a bad file — and an ambiguous row is not a row.

    The file has one importable line and three that cannot be read without
    guessing. The response is 200 with a report rather than a 4xx, because
    partial success is the normal outcome of a bulk import and there is no
    status code that means "mostly".

    `04/03/2026` is the case worth stating plainly: it is the 4th of March in
    most of the world and the 3rd of April in the US. Importing it under either
    reading produces a ledger that is *quietly* wrong, so it is refused.
    """
    csv_text = (
        "date,amount,description\n"
        "2026-03-01,-10.00,Fine\n"
        "04/03/2026,-10.00,Ambiguous date\n"
        '2026-03-02,"45,20",European decimal comma\n'
        "2026-03-03,0,No direction\n"
    )

    response = _upload(client, auth_headers, account.id, csv_text)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["total_rows"], body["imported"], body["failed"]) == (4, 1, 3)
    # The invariant the report is built to keep.
    assert body["imported"] + body["failed"] == body["total_rows"]
    assert body["errors_truncated"] is False

    # Row numbers are file line numbers, header included — the number a person
    # needs when they open the file to fix it.
    assert [error["row"] for error in body["errors"]] == [3, 4, 5]
    reasons = " ".join(error["reason"] for error in body["errors"])
    assert "ambiguous" in reasons
    assert "thousands" in reasons
    assert "zero" in reasons

    assert len(client.get("/transactions", headers=auth_headers).json()) == 1
