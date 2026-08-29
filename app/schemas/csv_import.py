"""Response shapes for the CSV import endpoint.

Like `schemas/summary.py`, this file is output-only — but for a different
reason. The summary endpoints take no body because everything a caller can say
fits in a query string. This one takes a body that Pydantic never sees: a
`multipart/form-data` upload, whose interesting half is a stream of bytes.
There is no `ImportCreate` to write because the request *is* the file, and the
file is validated by parsing it (see `routers/csv_import.py`), not by a schema.

**The shape below is a report, not a resource.** That is the decision worth
defending in this file, and it follows from a single fact about bulk import:
partial success is the normal case. A bank statement with 400 rows and 3 bad
ones is not a failed request and it is not a clean one either, so neither of
the two obvious designs works:

  - *Return the created transactions.* A 5,000-row import would serialize 5,000
    objects back to a client that asked "did it work?" — and it still has no
    room to say what happened to the rows that didn't make it.
  - *Fail the whole request on the first bad row.* One malformed line in a
    year of history means nothing imports, and the client learns about the
    rows one at a time, one upload at a time.

So the response is a tally plus a defect list. `imported + failed == total_rows`
holds always, which is what makes the tally checkable rather than decorative,
and every failure carries the row number a human can open in a spreadsheet.

**Why `errors` is capped and `failed` isn't.** A file that is wrong in a
structural way — the wrong export, a shifted column — is wrong in every row, and
a 50,000-row file would otherwise produce a 50,000-entry JSON array describing
the same mistake fifty thousand times. The count stays exact because it is a
counter; the list is a sample, and `errors_truncated` says so out loud rather
than letting a client mistake a truncated list for the whole story.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RowError(BaseModel):
    """One rejected row, and why.

    Deliberately *not* an echo of the whole row. A bank statement line contains
    a payee, a memo and an amount — the raw material of someone's private
    spending — and this object travels further than the row does: into client
    logs, into an error toast, into a screenshot pasted in a support ticket. So
    the report names the field that broke and the value that broke it, which is
    everything needed to fix the file, and nothing else from the line.
    """

    # 1-based and counting the header, so it matches what a spreadsheet shows in
    # its row gutter: header is row 1, the first data row is row 2. That is the
    # number a person needs, and it is worth the off-by-one here to save them
    # doing it in their head over a 400-row file.
    row: int = Field(description="Line number in the uploaded file; the header is row 1")

    # Null when the problem is the row itself rather than one of its cells —
    # a ragged line with the wrong number of fields has no single column to
    # blame.
    field: str | None = Field(description="Which column was rejected, if it was one column")
    value: str | None = Field(description="The offending cell, as it appeared in the file")

    reason: str = Field(description="What was wrong with it, in a form a human can act on")


class ImportSummaryRead(BaseModel):
    """`POST /transactions/import` — what the upload did."""

    filename: str | None = Field(description="As reported by the client; echoed, never trusted")
    account_id: int = Field(description="The account every imported row was filed against")

    # Echoed back rather than assumed, because the difference between a preview
    # and a write is the single most important fact about this response and the
    # client may have set it from a checkbox three screens away. A report that
    # says "imported: 400" without saying whether anything was actually stored
    # is a report that will eventually be misread.
    dry_run: bool = Field(description="True when nothing was written; the file was only checked")

    total_rows: int = Field(description="Data rows read from the file, excluding blank lines")
    imported: int = Field(description="Rows written (or that would be written, when dry_run)")
    failed: int = Field(description="Rows skipped; always equal to total_rows - imported")

    errors: list[RowError] = Field(
        default_factory=list,
        description="One entry per skipped row, oldest first, capped — see errors_truncated",
    )
    errors_truncated: bool = Field(
        default=False,
        description="True when `failed` exceeds the reporting cap and `errors` is a sample",
    )
