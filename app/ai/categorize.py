"""#17 — Suggesting a category for each transaction, from its description.

    "TESCO STORES 3421 LONDON"     ->  Groceries      confidence 0.94
    "SQ *BLUE BOTTLE COFFEE"       ->  Dining Out     confidence 0.71
    "PAYPAL *TRANSFER"             ->  (no match)     — skipped, not guessed

**Nothing here writes to the ledger.** This module produces suggestions; the
user accepts, corrects or ignores them, and only then does
`POST /ai/categorize/apply` write anything. That separation is the design, not
a limitation of it: a category is a claim about what money was *for*, it feeds
every chart in the app, and a model that silently refiles a year of history
because a merchant name was ambiguous has done damage that is tedious to find
and worse to undo. Suggestions are cheap to reject; writes are not.

**Three defences against non-deterministic output**, in the order they fire:

1. *The category cannot be hallucinated.* The JSON schema handed to the model
   constrains the answer to an `enum` built from this user's own category
   names. An invented category is not something to detect afterwards — it is
   not a value the response can contain.
2. *The type cannot be wrong.* Expense and income transactions are batched
   separately, so an expense batch is only ever offered expense categories.
   `Category.type` exists precisely so "spending by category" cannot sum in a
   paycheck, and this is the bulk path where that invariant would break at
   scale and unnoticed.
3. *Everything is re-checked in Python anyway.* Indices are bounds-checked and
   de-duplicated, names are resolved against a dict built from the database,
   confidence is clamped. A schema is a request, and the layer that treats it
   as a guarantee is the layer that breaks the first time the guarantee slips.

**Batching, and why it is 25.** One API call per transaction would be accurate,
slow and expensive — fifty transactions is fifty round trips and fifty copies of
the same category list. One call for all of them is cheap and degrades: the
longer the list, the more likely the model drifts, drops an index or starts
repeating a category. Twenty-five is small enough to stay reliable and large
enough that the per-call overhead is amortized. It is a tuning constant, and the
honest note is that it was chosen by reasoning rather than measured.

**A failed batch is not a failed request**, mirroring `routers/csv_import.py`:
one unreadable row does not reject the file. A batch whose API call fails is
reported through `skipped` and the rest are still returned — *unless* every
batch failed, which is a systematic problem (bad key, provider down) and is
raised rather than dressed up as fifty individual skips.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import anthropic
from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.client import translate_api_errors, usage_of
from app.core.config import settings
from app.models.category import Category
from app.models.enums import TransactionType
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.ai import (
    CategorizeCreate,
    CategorizeRead,
    CategorySuggestion,
    SkippedTransaction,
)

# See the module docstring. One paid call per batch, so this constant is the
# lever that trades accuracy against cost and latency.
BATCH_SIZE = 25

# The value the model returns when nothing fits. It has to be a member of the
# same `enum` as the real category names — a JSON schema cannot express "one of
# these strings, or null" as cleanly, and a nullable field invites the model to
# treat "no match" as a formatting question rather than a real answer.
#
# "No confident match" is the single most valuable thing this endpoint can say.
# A model pushed to always choose will always choose, and the resulting category
# is worse than no category: an uncategorized transaction is visibly missing,
# while a confidently wrong one is a slice of a pie chart nobody questions.
NO_MATCH = "(no confident match)"

# Amounts are shown to the model as context — "1.80" reads as coffee, "1800.00"
# reads as rent, and the same merchant name can be either. Two decimals, matching
# how every other surface in this app renders money.
_MONEY_FORMAT = "{:.2f}"


def _sentinel_for(names: Sequence[str]) -> str:
    """A no-match marker guaranteed not to collide with a real category name.

    Category names are user-typed, so `NO_MATCH` is *almost* certainly unique —
    and "almost certainly" is the phrase that turns into a support ticket. A
    user who names a category "(no confident match)" would otherwise have every
    declined suggestion silently resolve to it, which is the exact failure this
    whole module is built to prevent, arriving through the back door.

    Three lines to make it impossible rather than unlikely.
    """
    sentinel = NO_MATCH
    existing = set(names)
    while sentinel in existing:
        sentinel += "*"
    return sentinel


# --- What the model is asked to return -------------------------------------


class _RawAssignment(BaseModel):
    """One row of the model's structured response, before validation.

    Separate from `CategorySuggestion` in `schemas/ai.py` on purpose: that one
    describes a *verified* suggestion — a real category id, of the right type,
    for a transaction the user owns. This one describes what arrived. Collapsing
    the two would mean the API's response model doubles as the parser for
    untrusted input, and every field would have to be optional to survive a bad
    response, which would then weaken the contract for every honest caller.
    """

    model_config = ConfigDict(extra="forbid")

    index: int
    category: str
    confidence: float
    reasoning: str = Field(default="")


class _RawResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignments: list[_RawAssignment]


def _output_schema(category_names: Sequence[str], sentinel: str) -> dict[str, Any]:
    """The JSON schema the response is constrained to.

    **The `enum` is the whole defence.** Built per request from the names this
    user actually owns, it makes a hallucinated category unrepresentable rather
    than merely detectable. Note the cost of that: the schema differs per user,
    so nothing about this request is cacheable across users. Worth it — the
    alternative is a free-text field and a fuzzy-matching step, which is a
    guessing layer bolted on top of a guessing layer.

    `additionalProperties: false` and a complete `required` list are not
    optional decoration; the API's structured-output mode requires both.
    """
    return {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {
                "assignments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "index": {
                                "type": "integer",
                                "description": "The transaction's number in the list.",
                            },
                            "category": {
                                "type": "string",
                                "enum": [*category_names, sentinel],
                                "description": (
                                    f"The best-fitting category, or {sentinel!r} if none "
                                    "genuinely fits."
                                ),
                            },
                            "confidence": {
                                "type": "number",
                                # No `minimum`/`maximum`. Structured output
                                # rejects both on a numeric property (400: "For
                                # 'number' type, properties maximum, minimum are
                                # not supported"), so the range is stated in the
                                # description and *enforced* by the clamp in
                                # `_collect`. That clamp was written as
                                # belt-and-braces and turns out to be the only
                                # thing keeping the value in range — which is
                                # the argument for defence #3 in one line.
                                "description": (
                                    "How sure you are, from 0.0 to 1.0. Be honest: an "
                                    "unfamiliar merchant name deserves a low score even if "
                                    "one category is the best of a bad set."
                                ),
                            },
                            "reasoning": {
                                "type": "string",
                                "description": "One short clause explaining the choice.",
                            },
                        },
                        "required": ["index", "category", "confidence", "reasoning"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["assignments"],
            "additionalProperties": False,
        },
    }


SYSTEM_PROMPT = """\
You categorise bank transactions for a personal finance tracker.

You are given a list of the user's own categories and a numbered list of \
transactions. For each transaction, choose the category that best describes what \
the money was for, judging from the description, the amount and the date.

Return one assignment per transaction, using the index you were given.

Be genuinely honest about confidence. A recognisable merchant in an obvious \
category is high confidence. A bank reference number, a person's name, a \
transfer, or a merchant you do not recognise is not — say so with a low score, \
or choose the no-match option. An uncategorised transaction is a small \
inconvenience; a confidently miscategorised one silently distorts every chart \
the user looks at.
"""


def _render_transactions(batch: Sequence[Transaction]) -> str:
    """The numbered list the model reads.

    Index is position within *this batch*, starting at 1, not the database id.
    Two reasons, and the second is the important one: a small integer is easier
    for a model to carry accurately through a list of twenty-five, and a
    database id echoed back is an id an attacker-influenced description could
    try to talk the model into changing. Positions are resolved against a local
    list this module controls, so the worst a wrong index can do is miss.
    """
    lines = []
    for position, transaction in enumerate(batch, start=1):
        description = (transaction.description or "").strip() or "(no description)"
        lines.append(
            f"{position}. {transaction.occurred_on.isoformat()} | "
            f"{_MONEY_FORMAT.format(transaction.amount)} | {description}"
        )
    return "\n".join(lines)


def _parse_response(response: anthropic.types.Message) -> list[_RawAssignment]:
    """Pull the assignments out of a structured response.

    Structured output guarantees the first text block is valid JSON matching the
    schema — and this still validates it, because "guarantees" is a statement
    about the normal path. A refusal, a `max_tokens` truncation mid-object, or a
    response with no text block at all are all reachable, and each one produces
    a different exception at a different line if the parse is written as the
    one-liner the guarantee invites.
    """
    if response.stop_reason == "refusal":
        raise _BatchFailed("the model declined to categorise this batch")

    text = "\n".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise _BatchFailed("the model returned an empty response")

    try:
        # `json.loads`, never a string match against the serialized form: current
        # models vary in how they escape unicode and forward slashes inside JSON
        # strings, and a category name with an apostrophe is enough to expose it.
        return _RawResponse.model_validate(json.loads(text)).assignments
    except (json.JSONDecodeError, ValidationError) as exc:
        truncated = response.stop_reason == "max_tokens"
        raise _BatchFailed(
            "the model's response was cut off by the token limit"
            if truncated
            else "the model returned a response that did not match the schema"
        ) from exc


class _BatchFailed(Exception):
    """One batch could not be categorised. The other batches still can."""


def _run_batch(
    client: anthropic.Anthropic,
    batch: Sequence[Transaction],
    categories: Sequence[Category],
    sentinel: str,
) -> tuple[anthropic.types.Message, list[_RawAssignment]]:
    """Send one batch and return the raw response plus its parsed assignments."""
    names = [category.name for category in categories]
    listing = "\n".join(f"- {category.name}" for category in categories)
    side = batch[0].type.value

    prompt = (
        f"These are the user's {side} categories:\n{listing}\n\n"
        f"Categorise these {len(batch)} {side} transactions "
        "(date | amount | description):\n"
        f"{_render_transactions(batch)}"
    )

    with translate_api_errors():
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=settings.ai_max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "format": _output_schema(names, sentinel),
                # Low effort, deliberately. This is bulk classification from a
                # short string — the kind of task where extra reasoning buys
                # very little and is paid for on every one of the batches. The
                # endpoints that reason about *which* data to fetch
                # (`query.py`) or write prose (`insights.py`) leave effort at
                # its default. This is the knob to raise first if suggestions
                # come back weak.
                "effort": "low",
            },
        )

    return response, _parse_response(response)


# --- Selecting what to categorise ------------------------------------------


def _load_candidates(
    db: Session, user: User, request: CategorizeCreate
) -> list[Transaction]:
    """The transactions this request is about, scoped to the caller.

    Two modes, and the difference is deliberate. With explicit
    `transaction_ids`, exactly those rows are loaded *including already-categorised
    ones* — that is what makes this endpoint usable for re-categorising a batch
    the user thinks was filed wrong. With no ids, only uncategorised rows are
    selected, because "categorise my transactions" cannot sensibly mean
    "reconsider every decision I have already made".

    Ids belonging to someone else need no check of their own: the user scope has
    already made the condition unsatisfiable, so they simply do not come back.
    Same non-answer, same reasoning, as filtering the ledger by a foreign id.
    """
    stmt = select(Transaction).where(Transaction.user_id == user.id)

    if request.account_id is not None:
        stmt = stmt.where(Transaction.account_id == request.account_id)

    if request.transaction_ids:
        stmt = stmt.where(Transaction.id.in_(request.transaction_ids))
    else:
        stmt = stmt.where(Transaction.category_id.is_(None))

    # Newest first, then a limit: the transactions a user wants categorised are
    # the ones they just imported, not the oldest unresolved rows in their
    # history. `id` breaks ties so a repeated request considers the same set.
    stmt = stmt.order_by(Transaction.occurred_on.desc(), Transaction.id.desc()).limit(
        len(request.transaction_ids) if request.transaction_ids else request.limit
    )

    return list(db.scalars(stmt).all())


def _chunk(items: Sequence[Transaction], size: int) -> list[Sequence[Transaction]]:
    """Split a list into fixed-size batches."""
    return [items[start : start + size] for start in range(0, len(items), size)]


# --- The endpoint's work ---------------------------------------------------


def suggest_categories(
    client: anthropic.Anthropic,
    db: Session,
    user: User,
    request: CategorizeCreate,
) -> CategorizeRead:
    """Propose a category for each candidate transaction. Writes nothing."""
    candidates = _load_candidates(db, user, request)

    categories = list(
        db.scalars(
            select(Category).where(Category.user_id == user.id).order_by(Category.name)
        ).all()
    )

    if not categories:
        # 422 rather than an empty result: the request is well-formed but cannot
        # be satisfied, and the reason is something only the user can fix. An
        # empty `suggestions` list would read as "the AI had no ideas", which
        # sends them looking in exactly the wrong place.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="You have no categories yet. Create some before asking for suggestions.",
        )

    # Grouped by side of the ledger, so each batch is offered only the
    # categories that could legitimately apply to it. This is defence #2 from
    # the module docstring: an expense batch never sees an income category, so
    # "salary" cannot be suggested for a grocery run at all.
    by_type = {
        transaction_type: [t for t in candidates if t.type is transaction_type]
        for transaction_type in TransactionType
    }
    categories_by_type = {
        transaction_type: [c for c in categories if c.type is transaction_type]
        for transaction_type in TransactionType
    }

    suggestions: list[CategorySuggestion] = []
    skipped: list[SkippedTransaction] = []
    responses: list[anthropic.types.Message] = []
    batch_failures: list[HTTPException | _BatchFailed] = []
    batches_attempted = 0

    for transaction_type, transactions in by_type.items():
        if not transactions:
            continue

        side_categories = categories_by_type[transaction_type]
        if not side_categories:
            # Nothing to choose from, so there is nothing to ask. Skipping here
            # rather than sending the batch saves a paid call that could only
            # ever come back empty.
            skipped.extend(
                SkippedTransaction(
                    transaction_id=t.id,
                    description=t.description,
                    reason=f"you have no {transaction_type.value} categories",
                )
                for t in transactions
            )
            continue

        sentinel = _sentinel_for([c.name for c in side_categories])
        by_name = {c.name: c for c in side_categories}

        for batch in _chunk(transactions, BATCH_SIZE):
            batches_attempted += 1
            try:
                response, assignments = _run_batch(client, batch, side_categories, sentinel)
            except (HTTPException, _BatchFailed) as exc:
                # One batch down, the rest continue. See the module docstring:
                # the "every batch failed" case is re-raised below, so a
                # systematic outage still surfaces as an error rather than as a
                # long list of individually-skipped rows.
                batch_failures.append(exc)
                reason = exc.detail if isinstance(exc, HTTPException) else str(exc)
                skipped.extend(
                    SkippedTransaction(
                        transaction_id=t.id,
                        description=t.description,
                        reason=f"the AI request failed: {reason}",
                    )
                    for t in batch
                )
                continue

            responses.append(response)
            _collect(
                batch=batch,
                assignments=assignments,
                by_name=by_name,
                sentinel=sentinel,
                min_confidence=request.min_confidence,
                suggestions=suggestions,
                skipped=skipped,
            )

    if batches_attempted and len(batch_failures) == batches_attempted:
        # Nothing succeeded. This is not a partial result to report, it is a
        # broken dependency — so it gets the status the failure actually had
        # (429 stays a 429, a timeout stays a 504) rather than being flattened
        # into a generic error the client cannot act on.
        first = batch_failures[0]
        if isinstance(first, HTTPException):
            raise first
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(first))

    return CategorizeRead(
        considered=len(candidates),
        # Highest confidence first: the suggestions a user can accept at a glance
        # come before the ones that need thought, which is the order that makes a
        # review screen quick rather than exhausting.
        suggestions=sorted(suggestions, key=lambda s: s.confidence, reverse=True),
        skipped=skipped,
        min_confidence=request.min_confidence,
        model=responses[0].model if responses else settings.anthropic_model,
        usage=usage_of(*responses),
    )


def _collect(
    *,
    batch: Sequence[Transaction],
    assignments: Sequence[_RawAssignment],
    by_name: dict[str, Category],
    sentinel: str,
    min_confidence: float,
    suggestions: list[CategorySuggestion],
    skipped: list[SkippedTransaction],
) -> None:
    """Turn one batch's raw assignments into verified suggestions.

    This is defence #3 — every check here is one the schema was supposed to have
    made unnecessary, performed anyway. The failures it catches are the
    interesting ones, so each lands in `skipped` with a reason rather than being
    dropped: an index that does not exist, an index answered twice, a category
    name that is not one of this user's, a type that does not match. Silently
    discarding those would mean nobody ever finds out the model is drifting.

    Keyword-only arguments because seven positional parameters, four of them
    collections, is a call site nobody can read — and two of them are mutated in
    place, which is worth naming at the call.
    """
    seen: set[int] = set()

    for assignment in assignments:
        position = assignment.index - 1
        if not 0 <= position < len(batch):
            # No transaction to attach a skip record to — the index does not
            # identify one. Nothing to report against, so the row simply falls
            # out and the loop below catches it as "no suggestion returned".
            continue
        if assignment.index in seen:
            continue
        seen.add(assignment.index)

        transaction = batch[position]

        if assignment.category == sentinel:
            skipped.append(
                SkippedTransaction(
                    transaction_id=transaction.id,
                    description=transaction.description,
                    reason="no category was a confident fit",
                )
            )
            continue

        category = by_name.get(assignment.category)
        if category is None:
            skipped.append(
                SkippedTransaction(
                    transaction_id=transaction.id,
                    description=transaction.description,
                    reason=f"suggested a category that does not exist: {assignment.category!r}",
                )
            )
            continue

        if category.type is not transaction.type:
            # Unreachable while batches stay split by type — which is exactly
            # why it is checked. The batching is what makes this impossible, and
            # a check that only fires when someone removes that batching is a
            # check doing its job.
            skipped.append(
                SkippedTransaction(
                    transaction_id=transaction.id,
                    description=transaction.description,
                    reason=(
                        f"suggested {category.name!r}, an {category.type.value} category, "
                        f"for an {transaction.type.value} transaction"
                    ),
                )
            )
            continue

        # Clamped rather than trusted. The schema asks for 0..1 and a model that
        # returns 1.5 would otherwise produce a `CategorySuggestion` that fails
        # its own `le=1.0` validator — a 500 from this app's own response model,
        # which is a confusing way to learn the upstream drifted.
        confidence = min(1.0, max(0.0, assignment.confidence))

        suggestions.append(
            CategorySuggestion(
                transaction_id=transaction.id,
                description=transaction.description,
                amount=transaction.amount,
                type=transaction.type,
                occurred_on=transaction.occurred_on,
                category_id=category.id,
                category_name=category.name,
                confidence=confidence,
                reasoning=assignment.reasoning.strip(),
                recommended=confidence >= min_confidence,
            )
        )

    answered = {batch[a.index - 1].id for a in assignments if 0 <= a.index - 1 < len(batch)}
    skipped.extend(
        SkippedTransaction(
            transaction_id=transaction.id,
            description=transaction.description,
            reason="the model returned no suggestion for this transaction",
        )
        for transaction in batch
        if transaction.id not in answered
    )


# --- Applying what the user accepted ---------------------------------------


def apply_assignments(
    db: Session,
    user: User,
    pairs: Sequence[tuple[int, int]],
) -> list[Transaction]:
    """Write the user's accepted `(transaction_id, category_id)` choices.

    **This function has no idea an AI was involved**, and that is the point. It
    validates ownership and type-compatibility from scratch, exactly as
    `PATCH /transactions/{id}` does, because the pairs arriving here are the
    user's decisions — possibly corrections — and not a model's output to be
    trusted on the strength of having been suggested earlier.

    404 for a transaction that is not the caller's, on the reasoning
    `routers/transactions.py` sets out at length: "not yours" and "not there"
    have to be indistinguishable from outside.

    One commit for the whole set. A per-row commit would leave a half-applied
    review screen after a failure, which the user then has to reconcile by hand
    to find out which of their twenty ticks took effect.
    """
    transaction_ids = {transaction_id for transaction_id, _ in pairs}
    category_ids = {category_id for _, category_id in pairs}

    transactions = {
        t.id: t
        for t in db.scalars(
            select(Transaction).where(
                Transaction.user_id == user.id, Transaction.id.in_(transaction_ids)
            )
        ).all()
    }
    categories = {
        c.id: c
        for c in db.scalars(
            select(Category).where(Category.user_id == user.id, Category.id.in_(category_ids))
        ).all()
    }

    updated: list[Transaction] = []
    for transaction_id, category_id in pairs:
        transaction = transactions.get(transaction_id)
        if transaction is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Transaction {transaction_id} not found",
            )

        category = categories.get(category_id)
        if category is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Category {category_id} not found",
            )

        if category.type is not transaction.type:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Category {category.name!r} is an {category.type.value} category and "
                    f"cannot be used on an {transaction.type.value} transaction"
                ),
            )

        transaction.category_id = category_id
        updated.append(transaction)

    # Nothing has been written until here — every check above ran first, so a
    # single bad pair in a batch of fifty rejects the whole request rather than
    # applying the first forty-nine and reporting an error about the fiftieth.
    db.commit()
    for transaction in updated:
        db.refresh(transaction)

    return updated
