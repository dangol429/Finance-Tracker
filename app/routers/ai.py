"""The AI endpoints — Phase 3's four routes.

    POST /ai/query                natural-language question -> grounded answer
    POST /ai/categorize           suggest categories        (writes nothing)
    POST /ai/categorize/apply     write the accepted ones
    GET  /ai/insights/monthly     a short write-up of one month

**This module is thin on purpose.** Each handler resolves its inputs, calls one
function in `app/ai/`, and returns. The prompts, the tool definitions, the
schema validation and the SQL all live in that package, so the interesting code
is testable without a request and this file stays readable as a map of the
surface. That is the same split `main.py` keeps with the routers: the wiring
layer should be scannable in one screen.

**Every route here is protected**, by asking for `CurrentUser` in its signature
rather than by anything configured elsewhere — the property `core/deps.py`
argues for at length: a route is guarded because of what it asks for, not
because someone remembered to add its URL to a list.

**Every route also asks for `AiClient`**, which is what makes the whole group
answer 503 when the server has no API key, rather than 500 from inside the SDK.
The dependency is resolved before the handler body runs, so an unconfigured
deployment never gets as far as loading a user's transactions.

**Why the split between `POST /ai/categorize` and `POST /ai/categorize/apply`.**
The first asks a model what it thinks; the second changes the user's records.
Keeping them apart means no AI call can modify the ledger as a side effect of
being asked for an opinion, and it means correcting a suggestion is the *same*
operation as accepting one — the client sends whichever `category_id` the user
picked. An endpoint that could only apply what was suggested would quietly make
"accept" easier than "correct", which is how a review UI turns into a rubber
stamp.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.ai.categorize import apply_assignments, suggest_categories
from app.ai.client import AiClient
from app.ai.insights import monthly_insight
from app.ai.query import answer_question
from app.core.deps import CurrentUser, DbSession
from app.schemas.ai import (
    AiQueryCreate,
    AiQueryRead,
    ApplyCategoriesCreate,
    ApplyCategoriesRead,
    CategorizeCreate,
    CategorizeRead,
    MonthlyInsightRead,
)
from app.schemas.transaction import TransactionRead

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post(
    "/query",
    response_model=AiQueryRead,
    summary="Ask a question about your transactions in plain English",
)
def ask(
    payload: AiQueryCreate,
    current_user: CurrentUser,
    db: DbSession,
    client: AiClient,
) -> AiQueryRead:
    """Answer a natural-language question, using tool calls against real SQL.

    **POST, not GET, for a read-only operation.** That is a deliberate break
    with the rest of this API, and there are two reasons. A question is a body,
    not a path — putting free text in a query string means it lands in every
    access log and proxy cache between here and the browser, which for "how much
    did I spend on therapy" is a privacy leak rather than a style question. And
    the call is neither cacheable nor free: a GET is supposed to be safe to
    repeat, and every repeat of this one costs money.

    The answer comes back with `evidence` — every tool call and its exact
    result. See `app/ai/query.py` for why that field is the point of the
    endpoint rather than a debugging aid.
    """
    return answer_question(client, db, current_user, payload.question)


@router.post(
    "/categorize",
    response_model=CategorizeRead,
    summary="Suggest a category for uncategorized transactions",
)
def categorize(
    payload: CategorizeCreate,
    current_user: CurrentUser,
    db: DbSession,
    client: AiClient,
) -> CategorizeRead:
    """Propose a category per transaction. **Nothing is written.**

    Returns suggestions with a confidence score and a one-line reason each, plus
    a `skipped` list naming every transaction that did not get one and why. The
    two lists together account for every transaction considered, which is what
    makes the response readable as a report rather than as a partial answer.

    Send the accepted ones — edited or not — to `/ai/categorize/apply`.
    """
    return suggest_categories(client, db, current_user, payload)


@router.post(
    "/categorize/apply",
    response_model=ApplyCategoriesRead,
    summary="Apply category assignments the user accepted",
)
def apply_categories(
    payload: ApplyCategoriesCreate,
    current_user: CurrentUser,
    db: DbSession,
) -> ApplyCategoriesRead:
    """Write the accepted `(transaction, category)` pairs.

    **Note the signature: no `AiClient`.** This endpoint calls no model and
    needs no API key, so it keeps working on a deployment where the AI features
    are switched off — which matters, because a user who generated suggestions
    before a key expired should still be able to apply them. It is a bulk
    category update that happens to be reachable from an AI screen, and the
    validation it performs is the same set `PATCH /transactions/{id}` runs:
    ownership, existence, and matching `type`.

    All-or-nothing. One invalid pair rejects the whole request rather than
    applying the rest, because a partial write here leaves the user unable to
    tell which of their twenty ticks took effect without re-reading the ledger.
    """
    pairs = [(a.transaction_id, a.category_id) for a in payload.assignments]
    updated = apply_assignments(db, current_user, pairs)

    return ApplyCategoriesRead(
        updated=len(updated),
        transactions=[TransactionRead.model_validate(t) for t in updated],
    )


@router.get(
    "/insights/monthly",
    response_model=MonthlyInsightRead,
    summary="A short AI write-up of one month, with the figures behind it",
)
def monthly_insights(
    current_user: CurrentUser,
    db: DbSession,
    client: AiClient,
    month: Annotated[
        str,
        Query(
            description='The month to summarise, as "YYYY-MM"',
            examples=["2026-06"],
            min_length=7,
            max_length=7,
        ),
    ],
    account_id: Annotated[int | None, Query(gt=0, description="Only this account")] = None,
) -> MonthlyInsightRead:
    """Summarise one month, grounded in the same aggregates `/summary/*` returns.

    **GET here, unlike `/ai/query` above, and the difference is the input.** The
    only thing in this URL is a month and an optional account id — no free text,
    nothing private in a log line — and the request genuinely is a fetch of a
    resource that exists ("June's summary"). A client can cache it, and should:
    a month that has ended will never produce a different answer, and every
    regeneration is a paid call for the same paragraph.

    `month` is required rather than defaulting to the current month. A default
    would make the endpoint's answer depend on the server's clock, so the same
    URL would mean something different tomorrow — and the *current* month is the
    worst possible default anyway, since it is incomplete by definition and a
    summary of a third of a month reads as a summary of all of it.
    """
    return monthly_insight(client, db, current_user, month, account_id)
