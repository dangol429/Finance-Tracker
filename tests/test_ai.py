"""Tests for the three AI endpoints.

**No network, no API key, no cost.** Every test here runs against a scripted
stand-in for the Anthropic client, installed through
`app.dependency_overrides[get_anthropic_client]` — the same seam `conftest.py`
uses to hand the app its test database session. That is the payoff of making the
client a FastAPI dependency rather than a module-level singleton: the double
goes in through the front door, and the application code cannot tell it from the
real thing.

**What that means these tests can and cannot prove.** They cannot tell you
whether the model gives good answers — that is a question about a model, not
about this code, and it needs an eval suite and a budget rather than a test
suite. What they prove is everything *around* the model, which is where the bugs
that matter live:

  - the tools run scoped SQL, so one user's question can never total another
    user's spending;
  - a malformed tool call is handed back as a correctable error rather than
    crashing the request;
  - a hallucinated category is caught and reported instead of written;
  - `POST /ai/categorize` writes nothing at all;
  - the facts returned with a monthly insight are the same numbers
    `/summary/*` would return;
  - an empty month never reaches the model.

Every one of those is a property the code is responsible for and the model is
not, which is exactly the line a test suite should be drawn along.

**The scripts are deliberately explicit.** Each test states the exact sequence
of responses the model would give, including the wrong ones. A double that
always returns something plausible tests the happy path twice; the interesting
cases here are the ones where the script returns something the code has to
refuse.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

import anthropic
import httpx2
import pytest
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ai.client import get_anthropic_client
from app.ai.query import MAX_TOOL_TURNS
from app.core.config import settings
from app.main import app
from app.models import Account, Category, Transaction, User

# --- The double ------------------------------------------------------------


def text_message(
    text: str,
    *,
    stop_reason: str = "end_turn",
    model: str = "claude-opus-5",
) -> Message:
    """A finished assistant turn carrying one text block."""
    return Message(
        id="msg_test",
        type="message",
        role="assistant",
        model=model,
        content=[TextBlock(type="text", text=text)],
        stop_reason=stop_reason,
        stop_sequence=None,
        usage=Usage(input_tokens=100, output_tokens=20),
    )


def json_message(payload: Any, **kwargs: Any) -> Message:
    """A structured-output response: one text block holding JSON."""
    return text_message(json.dumps(payload), **kwargs)


def tool_use_message(name: str, tool_input: dict[str, Any], *, call_id: str = "toolu_1") -> Message:
    """An assistant turn that asks for one tool call."""
    return Message(
        id="msg_test",
        type="message",
        role="assistant",
        model="claude-opus-5",
        content=[ToolUseBlock(type="tool_use", id=call_id, name=name, input=tool_input)],
        stop_reason="tool_use",
        stop_sequence=None,
        usage=Usage(input_tokens=100, output_tokens=20),
    )


class FakeMessages:
    """Serves a fixed script of responses and records every request.

    Recording the requests is half the point. Several of the properties worth
    testing are about what this app *sends* — that an expense batch is only ever
    offered expense categories, that the final turn of the query loop withholds
    the tools, that an error tool result is fed back — and none of those is
    visible in the response body.
    """

    def __init__(self, script: list[Message | Exception]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Message:
        # `messages` is copied, not stored by reference. The agentic loop in
        # `app/ai/query.py` appends to one list across turns, so recording the
        # object itself would mean every recorded call shares it — and an
        # assertion about what the *second* request contained would silently be
        # reading the state after the fourth. A recording double that keeps a
        # live reference records the future, not the call.
        recorded = dict(kwargs)
        if "messages" in recorded:
            recorded["messages"] = list(recorded["messages"])
        self.calls.append(recorded)

        if not self.script:
            # Loudly, rather than returning a default. A test whose code made
            # more API calls than it scripted has found something, and a
            # forgiving double would hide it.
            raise AssertionError(
                f"the code made {len(self.calls)} API calls but the script had "
                f"{len(self.calls) - 1}"
            )
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class FakeAnthropic:
    def __init__(self, script: list[Message | Exception]) -> None:
        self.messages = FakeMessages(script)


@pytest.fixture
def fake_ai(client: TestClient):
    """Install a scripted client for the `/ai` routes.

    Yields a function so each test writes its own script inline, next to the
    assertions about it. Depends on `client` so the override lands on an app the
    test is actually about to call, and is removed afterwards — `client` clears
    the whole override dict at teardown, and this pop makes the cleanup correct
    even if a test uses the fixture without the TestClient.
    """
    installed: list[FakeAnthropic] = []

    def install(*script: Message | Exception) -> FakeAnthropic:
        fake = FakeAnthropic(list(script))
        app.dependency_overrides[get_anthropic_client] = lambda: fake
        installed.append(fake)
        return fake

    yield install
    app.dependency_overrides.pop(get_anthropic_client, None)


# --- Seeded data -----------------------------------------------------------


@pytest.fixture
def june(
    make_transaction,
    user: User,
    account: Account,
    categories: dict[str, Category],
) -> None:
    """A small, exactly-known June: 100.00 of groceries and 3000.00 of salary.

    Every figure asserted below is derivable from these four rows by hand, which
    is what makes a failure readable — a wrong total names the bug rather than
    sending you to a fixture to work out what the answer should have been.
    """
    make_transaction(
        user, account, "40.00", "expense", date(2026, 6, 5), categories["Groceries"], "Tesco"
    )
    make_transaction(
        user, account, "60.00", "expense", date(2026, 6, 20), categories["Groceries"], "Sainsbury"
    )
    make_transaction(
        user, account, "3000.00", "income", date(2026, 6, 1), categories["Salary"], "Payroll"
    )
    # May, so the previous-month comparison in the insights tests is non-empty.
    make_transaction(
        user, account, "25.00", "expense", date(2026, 5, 11), categories["Groceries"], "Corner shop"
    )


# --- Structured-output schema constraints ----------------------------------


def _walk(node: Any):
    """Yield every nested object in a JSON Schema."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def test_structured_output_schemas_avoid_unsupported_keywords() -> None:
    """The JSON Schema accepted by structured output is a *subset* of the spec.

    Two keywords that look perfectly valid are rejected with a 400 at request
    time — `minItems` above 1 on an array, and `minimum`/`maximum` on a number.
    Neither failure is reachable from a stubbed test, because a double will
    happily accept a schema the API refuses; both were found by calling the real
    API, and both are the kind of thing a later edit would reintroduce without
    a second thought.

    So this test pins them. It asserts on the shape of the request rather than
    on a response, which is the only way a constraint enforced by a remote
    service can be checked offline — and it is worth the slight awkwardness,
    because the alternative is rediscovering a 400 in production.

    The ranges these keywords would have expressed are enforced where they can
    be: `_collect` clamps confidence, and `monthly_insight` truncates the
    highlight list.
    """
    from app.ai.categorize import _output_schema
    from app.ai.insights import OUTPUT_SCHEMA

    schemas = [
        OUTPUT_SCHEMA,
        _output_schema(["Groceries", "Dining Out"], "(no confident match)"),
    ]

    for schema in schemas:
        for node in _walk(schema):
            assert "minimum" not in node, f"'minimum' is rejected by the API: {node}"
            assert "maximum" not in node, f"'maximum' is rejected by the API: {node}"
            assert node.get("minItems", 0) <= 1, f"minItems > 1 is rejected by the API: {node}"


# ===========================================================================
# #16  POST /ai/query
# ===========================================================================


def test_query_answers_from_the_tool_result(
    client: TestClient, auth_headers: dict[str, str], june: None, fake_ai
) -> None:
    """The happy path: the model asks for a total, SQL computes it, evidence carries it."""
    fake = fake_ai(
        tool_use_message(
            "summarize_transactions",
            {"date_from": "2026-06-01", "date_to": "2026-06-30", "type": "expense"},
        ),
        text_message("You spent 100.00 on expenses in June, across 2 transactions."),
    )

    response = client.post(
        "/ai/query", headers=auth_headers, json={"question": "How much did I spend in June?"}
    )

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["answer"].startswith("You spent 100.00")
    assert body["model"] == "claude-opus-5"
    # Usage is summed across both calls, not read off the last one.
    assert body["usage"]["input_tokens"] == 200

    # The evidence is the point of the endpoint: the number in the sentence has
    # a query behind it, and that query's result is in the response.
    assert len(body["evidence"]) == 1
    step = body["evidence"][0]
    assert step["tool"] == "summarize_transactions"
    assert step["arguments"]["date_from"] == "2026-06-01"
    assert step["result"]["total"] == "100.00"
    assert step["result"]["transaction_count"] == 2

    # Two API calls: one that asked for the tool, one that wrote the answer.
    assert len(fake.messages.calls) == 2


def test_query_tools_cannot_see_another_users_transactions(
    client: TestClient,
    auth_headers: dict[str, str],
    june: None,
    other_user: User,
    other_account: Account,
    make_transaction,
    fake_ai,
) -> None:
    """The scope is in the executor, not in the prompt.

    The scripted model asks for *every* expense in a decade with no account
    filter — the widest query the tool schema can express. The other user's
    5000.00 still does not appear, because `_criteria` starts from the caller's
    id before any model-supplied filter is applied.
    """
    make_transaction(
        other_user, other_account, "5000.00", "expense", date(2026, 6, 15), None, "Not mine"
    )

    fake_ai(
        tool_use_message(
            "summarize_transactions",
            {"date_from": "2020-01-01", "date_to": "2030-12-31", "type": "expense"},
        ),
        text_message("125.00 in total."),
    )

    response = client.post(
        "/ai/query", headers=auth_headers, json={"question": "Everything I have ever spent?"}
    )

    assert response.status_code == 200, response.text
    result = response.json()["evidence"][0]["result"]
    # 40 + 60 + 25 from the `june` fixture. Not 5125.00.
    assert result["total"] == "125.00"
    assert result["transaction_count"] == 3


def test_query_hands_a_bad_tool_call_back_for_correction(
    client: TestClient, auth_headers: dict[str, str], june: None, fake_ai
) -> None:
    """A malformed tool call is a conversation turn, not a 500.

    The first scripted call has an unparseable date. The request must survive it,
    the model must be told what was wrong, and the corrected call's result is
    what ends up in the evidence — with the failed attempt deliberately absent,
    since nothing was queried.
    """
    fake = fake_ai(
        tool_use_message(
            "summarize_transactions",
            {"date_from": "June", "date_to": "2026-06-30"},
            call_id="toolu_bad",
        ),
        tool_use_message(
            "summarize_transactions",
            {"date_from": "2026-06-01", "date_to": "2026-06-30", "type": "expense"},
            call_id="toolu_good",
        ),
        text_message("You spent 100.00 in June."),
    )

    response = client.post(
        "/ai/query", headers=auth_headers, json={"question": "Spending in June?"}
    )

    assert response.status_code == 200, response.text
    assert len(response.json()["evidence"]) == 1

    # The second request carried the error back, flagged so the model treats it
    # as something to fix rather than as a result to report.
    second_call_messages = fake.messages.calls[1]["messages"]
    tool_result = second_call_messages[-1]["content"][0]
    assert tool_result["tool_use_id"] == "toolu_bad"
    assert tool_result["is_error"] is True
    assert "date_from" in tool_result["content"]


def test_query_stops_calling_tools_at_the_turn_limit(
    client: TestClient, auth_headers: dict[str, str], june: None, fake_ai
) -> None:
    """A model that never stops asking is stopped, and made to answer.

    The script asks for a tool on every single turn. The loop must cap the paid
    calls at `MAX_TOOL_TURNS + 1` and — the part that makes the last turn
    terminal — must withhold the tools on it, so the model has nothing to ask
    with and answers from what it already gathered.
    """
    fake = fake_ai(
        *[
            tool_use_message(
                "summarize_transactions",
                {"date_from": "2026-06-01", "date_to": "2026-06-30"},
                call_id=f"toolu_{i}",
            )
            for i in range(MAX_TOOL_TURNS)
        ],
        text_message("Roughly 100.00, from what I gathered."),
    )

    response = client.post(
        "/ai/query", headers=auth_headers, json={"question": "Spending in June?"}
    )

    assert response.status_code == 200, response.text
    assert len(fake.messages.calls) == MAX_TOOL_TURNS + 1

    assert "tools" in fake.messages.calls[0]
    assert "tools" not in fake.messages.calls[-1]


def test_query_includes_the_users_own_categories_in_the_prompt(
    client: TestClient, auth_headers: dict[str, str], june: None, fake_ai
) -> None:
    """"Food" is only resolvable because the real category names are in context."""
    fake = fake_ai(text_message("I can only answer questions about your transactions."))

    client.post("/ai/query", headers=auth_headers, json={"question": "What is a good ISA?"})

    system = fake.messages.calls[0]["system"]
    assert "Groceries (expense)" in system
    assert "Salary (income)" in system
    assert "Chase Checking" in system


def test_query_reports_a_truncated_answer_rather_than_presenting_half_of_one(
    client: TestClient, auth_headers: dict[str, str], june: None, fake_ai
) -> None:
    fake_ai(text_message("You spent 100.00 on gro", stop_reason="max_tokens"))

    response = client.post(
        "/ai/query", headers=auth_headers, json={"question": "Spending in June?"}
    )

    assert response.status_code == 200, response.text
    assert "cut short" in response.json()["answer"]


def test_query_turns_a_provider_rate_limit_into_a_429(
    client: TestClient, auth_headers: dict[str, str], june: None, fake_ai
) -> None:
    """Upstream failures keep their meaning instead of flattening into a 500.

    A 500 says "this app is broken", which sends whoever is on call looking in
    the wrong place. 429 says "wait and retry", which is both true and
    actionable.
    """
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    fake_ai(
        anthropic.RateLimitError(
            "rate limited", response=httpx2.Response(429, request=request), body=None
        )
    )

    response = client.post(
        "/ai/query", headers=auth_headers, json={"question": "Spending in June?"}
    )

    assert response.status_code == 429
    assert "rate limiting" in response.json()["detail"]


def test_query_requires_authentication(client: TestClient) -> None:
    assert client.post("/ai/query", json={"question": "How much?"}).status_code == 401


def test_query_rejects_an_empty_question(
    client: TestClient, auth_headers: dict[str, str], fake_ai
) -> None:
    """Validated before the paid call, not after it."""
    fake = fake_ai()
    assert client.post("/ai/query", headers=auth_headers, json={"question": ""}).status_code == 422
    assert fake.messages.calls == []


def test_ai_routes_answer_503_without_an_api_key(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unconfigured server refuses this feature and serves everything else.

    Note there is no `fake_ai` here: the real dependency runs, finds no key, and
    declines. The assertion on `/accounts` afterwards is the important half —
    the AI layer being off must not take any of the rest of the API with it.
    """
    monkeypatch.setattr(settings, "anthropic_api_key", None)

    response = client.post(
        "/ai/query", headers=auth_headers, json={"question": "How much did I spend?"}
    )
    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]

    assert client.get("/accounts", headers=auth_headers).status_code == 200


# ===========================================================================
# #17  POST /ai/categorize  and  /ai/categorize/apply
# ===========================================================================


@pytest.fixture
def uncategorized(
    make_transaction, user: User, account: Account, categories: dict[str, Category]
) -> list[Transaction]:
    """Two uncategorized expenses, which is what the endpoint selects by default."""
    return [
        make_transaction(user, account, "12.40", "expense", date(2026, 6, 3), None, "TESCO 3421"),
        make_transaction(user, account, "4.10", "expense", date(2026, 6, 4), None, "SQ *COFFEE"),
    ]


def test_categorize_returns_suggestions_and_writes_nothing(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    uncategorized: list[Transaction],
    fake_ai,
) -> None:
    """The core contract: an opinion, not an edit."""
    # Candidates are selected newest-first, so index 1 is the 6 June coffee and
    # index 2 is the 3 June supermarket run. Spelling that out here rather than
    # assuming fixture order is what keeps this test honest about the mapping
    # between the model's indices and real rows — getting that backwards is the
    # bug the numbering exists to prevent.
    fake_ai(
        json_message(
            {
                "assignments": [
                    {
                        "index": 1,
                        "category": "Groceries",
                        "confidence": 0.4,
                        "reasoning": "A coffee shop, but no dining category exists",
                    },
                    {
                        "index": 2,
                        "category": "Groceries",
                        "confidence": 0.95,
                        "reasoning": "Tesco is a supermarket",
                    },
                ]
            }
        )
    )

    response = client.post("/ai/categorize", headers=auth_headers, json={})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["considered"] == 2

    # Sorted by confidence, highest first.
    high, low = body["suggestions"]
    assert high["confidence"] == 0.95
    assert high["recommended"] is True
    assert high["category_name"] == "Groceries"
    assert low["confidence"] == 0.4
    assert low["recommended"] is False  # below the 0.6 default threshold

    # The suggestion carries the transaction's own fields, so a review row needs
    # no second request to render.
    assert high["description"] == "TESCO 3421"
    assert high["amount"] == "12.40"

    # Nothing was written. This is the assertion the endpoint exists to satisfy.
    db_session.expire_all()
    assert all(t.category_id is None for t in uncategorized)


def test_categorize_refuses_a_category_the_user_does_not_own(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    uncategorized: list[Transaction],
    fake_ai,
) -> None:
    """A hallucinated category is reported, never resolved to something close.

    The schema's `enum` makes this unreachable in practice; the check exists
    because "unreachable" is a claim about the provider honouring a schema, and
    the layer that treats that as a guarantee is the layer that breaks when it
    slips.
    """
    fake_ai(
        json_message(
            {
                "assignments": [
                    {
                        "index": 1,
                        "category": "Supermarkets",  # not one of theirs
                        "confidence": 0.99,
                        "reasoning": "invented",
                    },
                    {
                        "index": 2,
                        "category": "Groceries",
                        "confidence": 0.8,
                        "reasoning": "fine",
                    },
                ]
            }
        )
    )

    body = client.post("/ai/categorize", headers=auth_headers, json={}).json()

    assert [s["category_name"] for s in body["suggestions"]] == ["Groceries"]
    skipped = body["skipped"]
    assert len(skipped) == 1
    assert "does not exist" in skipped[0]["reason"]
    assert "Supermarkets" in skipped[0]["reason"]


def test_categorize_reports_a_declined_match_rather_than_forcing_one(
    client: TestClient, auth_headers: dict[str, str], uncategorized: list[Transaction], fake_ai
) -> None:
    """"No confident match" is a real answer and has to survive to the response."""
    from app.ai.categorize import NO_MATCH

    fake_ai(
        json_message(
            {
                "assignments": [
                    {"index": 1, "category": NO_MATCH, "confidence": 0.2, "reasoning": "unclear"},
                    {"index": 2, "category": "Groceries", "confidence": 0.7, "reasoning": "ok"},
                ]
            }
        )
    )

    body = client.post("/ai/categorize", headers=auth_headers, json={}).json()

    assert len(body["suggestions"]) == 1
    assert body["skipped"][0]["reason"] == "no category was a confident fit"


def test_categorize_reports_a_transaction_the_model_ignored(
    client: TestClient, auth_headers: dict[str, str], uncategorized: list[Transaction], fake_ai
) -> None:
    """Two lists that account for every transaction, so nothing goes missing quietly."""
    fake_ai(
        json_message(
            {
                "assignments": [
                    {"index": 1, "category": "Groceries", "confidence": 0.9, "reasoning": "ok"}
                ]
            }
        )
    )

    body = client.post("/ai/categorize", headers=auth_headers, json={}).json()

    assert len(body["suggestions"]) == 1
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["reason"] == "the model returned no suggestion for this transaction"
    assert len(body["suggestions"]) + len(body["skipped"]) == body["considered"]


def test_categorize_clamps_an_out_of_range_confidence(
    client: TestClient, auth_headers: dict[str, str], uncategorized: list[Transaction], fake_ai
) -> None:
    """A model returning 1.5 must not become a 500 from this app's own validator."""
    fake_ai(
        json_message(
            {
                "assignments": [
                    {"index": 1, "category": "Groceries", "confidence": 1.5, "reasoning": "sure"},
                    {"index": 2, "category": "Groceries", "confidence": -0.2, "reasoning": "no"},
                ]
            }
        )
    )

    response = client.post("/ai/categorize", headers=auth_headers, json={})

    assert response.status_code == 200, response.text
    assert sorted(s["confidence"] for s in response.json()["suggestions"]) == [0.0, 1.0]


def test_categorize_offers_each_side_only_its_own_categories(
    client: TestClient,
    auth_headers: dict[str, str],
    make_transaction,
    user: User,
    account: Account,
    categories: dict[str, Category],
    fake_ai,
) -> None:
    """Income and expense are batched apart, so a type mismatch is unrepresentable.

    This asserts on what was *sent*: the `enum` in each request's schema. An
    expense batch that could see "Salary" is one bad guess away from filing a
    grocery run as income, and `Category.type` exists precisely so that cannot
    happen.
    """
    make_transaction(user, account, "9.99", "expense", date(2026, 6, 6), None, "SHOP")
    make_transaction(user, account, "500.00", "income", date(2026, 6, 7), None, "TRANSFER IN")

    fake = fake_ai(
        json_message({"assignments": []}),
        json_message({"assignments": []}),
    )

    client.post("/ai/categorize", headers=auth_headers, json={})

    assert len(fake.messages.calls) == 2

    enums = [
        call["output_config"]["format"]["schema"]["properties"]["assignments"]["items"][
            "properties"
        ]["category"]["enum"]
        for call in fake.messages.calls
    ]
    expense_enum = next(e for e in enums if "Groceries" in e)
    income_enum = next(e for e in enums if "Salary" in e)

    assert "Salary" not in expense_enum
    assert "Groceries" not in income_enum


def test_categorize_requires_categories_to_choose_from(
    client: TestClient, auth_headers: dict[str, str], user: User, account: Account, fake_ai
) -> None:
    """422 naming the fix, not an empty list that reads as "the AI had no ideas"."""
    fake = fake_ai()

    response = client.post("/ai/categorize", headers=auth_headers, json={})

    assert response.status_code == 422
    assert "no categories" in response.json()["detail"]
    assert fake.messages.calls == []


def test_categorize_ignores_another_users_transaction_ids(
    client: TestClient,
    auth_headers: dict[str, str],
    categories: dict[str, Category],
    other_user: User,
    other_account: Account,
    make_transaction,
    fake_ai,
) -> None:
    """A foreign id is not an error, it simply matches nothing."""
    theirs = make_transaction(
        other_user, other_account, "77.00", "expense", date(2026, 6, 8), None, "Theirs"
    )

    fake = fake_ai()

    response = client.post(
        "/ai/categorize", headers=auth_headers, json={"transaction_ids": [theirs.id]}
    )

    assert response.status_code == 200, response.text
    assert response.json()["considered"] == 0
    # Nothing to categorise means nothing to pay for.
    assert fake.messages.calls == []


def test_apply_writes_the_accepted_assignments(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    uncategorized: list[Transaction],
    categories: dict[str, Category],
) -> None:
    """The explicit second step — and note it needs no AI client at all."""
    target = uncategorized[0]

    response = client.post(
        "/ai/categorize/apply",
        headers=auth_headers,
        json={
            "assignments": [
                {"transaction_id": target.id, "category_id": categories["Groceries"].id}
            ]
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["updated"] == 1
    assert body["transactions"][0]["category_id"] == categories["Groceries"].id

    db_session.expire_all()
    assert target.category_id == categories["Groceries"].id
    # The one that was not accepted is untouched.
    assert uncategorized[1].category_id is None


def test_apply_works_with_no_api_key_configured(
    client: TestClient,
    auth_headers: dict[str, str],
    uncategorized: list[Transaction],
    categories: dict[str, Category],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Suggestions generated before a key expired must still be applicable.

    This is why `apply_categories` does not ask for `AiClient` in its signature:
    it is a bulk category update that happens to be reachable from an AI screen.
    """
    monkeypatch.setattr(settings, "anthropic_api_key", None)

    response = client.post(
        "/ai/categorize/apply",
        headers=auth_headers,
        json={
            "assignments": [
                {
                    "transaction_id": uncategorized[0].id,
                    "category_id": categories["Groceries"].id,
                }
            ]
        },
    )

    assert response.status_code == 200, response.text


def test_apply_refuses_another_users_transaction(
    client: TestClient,
    auth_headers: dict[str, str],
    categories: dict[str, Category],
    other_user: User,
    other_account: Account,
    make_transaction,
) -> None:
    """404, not 403 — "not yours" and "not there" are indistinguishable from outside."""
    theirs = make_transaction(
        other_user, other_account, "77.00", "expense", date(2026, 6, 8), None, "Theirs"
    )

    response = client.post(
        "/ai/categorize/apply",
        headers=auth_headers,
        json={
            "assignments": [
                {"transaction_id": theirs.id, "category_id": categories["Groceries"].id}
            ]
        },
    )

    assert response.status_code == 404


def test_apply_refuses_a_category_of_the_wrong_type(
    client: TestClient,
    auth_headers: dict[str, str],
    uncategorized: list[Transaction],
    categories: dict[str, Category],
) -> None:
    """The invariant that keeps a paycheck out of a spending chart, enforced on write."""
    response = client.post(
        "/ai/categorize/apply",
        headers=auth_headers,
        json={
            "assignments": [
                {
                    "transaction_id": uncategorized[0].id,
                    "category_id": categories["Salary"].id,  # an income category
                }
            ]
        },
    )

    assert response.status_code == 422
    assert "income category" in response.json()["detail"]


def test_apply_is_all_or_nothing(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    uncategorized: list[Transaction],
    categories: dict[str, Category],
) -> None:
    """One bad pair rejects the batch, so the user never has to reconcile a partial write."""
    response = client.post(
        "/ai/categorize/apply",
        headers=auth_headers,
        json={
            "assignments": [
                {
                    "transaction_id": uncategorized[0].id,
                    "category_id": categories["Groceries"].id,
                },
                {"transaction_id": 999_999, "category_id": categories["Groceries"].id},
            ]
        },
    )

    assert response.status_code == 404

    db_session.expire_all()
    assert uncategorized[0].category_id is None


# ===========================================================================
# #18  GET /ai/insights/monthly
# ===========================================================================


INSIGHT_PAYLOAD = {
    "headline": "You spent 100.00 in June against 3000.00 of income.",
    "summary": "Spending rose from 25.00 in May to 100.00 in June, all of it groceries.",
    "highlights": ["Groceries: 100.00, up from 25.00", "Savings rate 96.67%"],
}


def test_monthly_insight_returns_the_facts_it_was_written_from(
    client: TestClient, auth_headers: dict[str, str], june: None, fake_ai
) -> None:
    """The grounding contract: the prose and the aggregates arrive together.

    The `facts` block is asserted against figures derivable by hand from the
    `june` fixture — and cross-checked against `/summary/income-vs-expense`
    below, because "the same numbers the dashboard shows" is the actual promise.
    """
    fake_ai(json_message(INSIGHT_PAYLOAD))

    response = client.get("/ai/insights/monthly", headers=auth_headers, params={"month": "2026-06"})

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["month"] == "2026-06"
    assert body["headline"] == INSIGHT_PAYLOAD["headline"]
    assert len(body["highlights"]) == 2
    assert body["model"] == "claude-opus-5"

    facts = body["facts"]
    assert facts["previous_month"] == "2026-05"
    assert facts["totals"]["expense"]["total"] == "100.00"
    assert facts["totals"]["income"]["total"] == "3000.00"
    assert facts["previous_totals"]["expense"]["total"] == "25.00"
    assert facts["categories"]["categories"][0]["category_name"] == "Groceries"


def test_monthly_insight_facts_match_the_summary_endpoints(
    client: TestClient, auth_headers: dict[str, str], june: None, fake_ai
) -> None:
    """Two views of one query, not two queries that might disagree.

    This is the assertion that would catch a re-implemented aggregate drifting
    from the real one — the failure mode `app/ai/insights.py` imports the
    summary handlers to avoid.
    """
    fake_ai(json_message(INSIGHT_PAYLOAD))

    insight = client.get(
        "/ai/insights/monthly", headers=auth_headers, params={"month": "2026-06"}
    ).json()
    summary = client.get(
        "/summary/income-vs-expense",
        headers=auth_headers,
        params={"date_from": "2026-06-01", "date_to": "2026-06-30"},
    ).json()

    assert insight["facts"]["totals"]["expense"] == summary["expense"]
    assert insight["facts"]["totals"]["income"] == summary["income"]
    assert insight["facts"]["totals"]["savings_rate"] == summary["savings_rate"]


def test_monthly_insight_prompt_contains_the_real_figures(
    client: TestClient, auth_headers: dict[str, str], june: None, fake_ai
) -> None:
    """The model narrates numbers it was handed; it is never asked to recall them."""
    fake = fake_ai(json_message(INSIGHT_PAYLOAD))

    client.get("/ai/insights/monthly", headers=auth_headers, params={"month": "2026-06"})

    prompt = fake.messages.calls[0]["messages"][0]["content"]
    assert '"total":"100.00"' in prompt
    assert '"total":"3000.00"' in prompt
    assert "2026-05" in prompt  # the comparison month travels with it


def test_empty_month_never_reaches_the_model(
    client: TestClient, auth_headers: dict[str, str], june: None, fake_ai
) -> None:
    """Nothing to summarise means nothing to pay for, and nothing to invent.

    `model: null` is the signal that the text below it is deterministic — which
    is a distinction a client may well want to render, and one nobody can
    reconstruct later from the prose alone.
    """
    fake = fake_ai()

    response = client.get(
        "/ai/insights/monthly", headers=auth_headers, params={"month": "2026-01"}
    )

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["model"] is None
    assert body["usage"] is None
    assert body["highlights"] == []
    assert "No transactions recorded in 2026-01" in body["headline"]
    assert fake.messages.calls == []


@pytest.mark.parametrize("month", ["2026-13", "2026-6", "june", "26-06", ""])
def test_monthly_insight_rejects_a_malformed_month(
    client: TestClient, auth_headers: dict[str, str], fake_ai, month: str
) -> None:
    """Each of these would otherwise silently report on a month nobody asked for."""
    fake = fake_ai()

    response = client.get("/ai/insights/monthly", headers=auth_headers, params={"month": month})

    assert response.status_code == 422
    assert fake.messages.calls == []


def test_monthly_insight_requires_authentication(client: TestClient) -> None:
    assert client.get("/ai/insights/monthly", params={"month": "2026-06"}).status_code == 401


def test_monthly_insight_scopes_facts_to_the_caller(
    client: TestClient,
    auth_headers: dict[str, str],
    june: None,
    other_user: User,
    other_account: Account,
    make_transaction,
    fake_ai,
) -> None:
    """The aggregates behind the write-up are scoped like every other query here."""
    make_transaction(
        other_user, other_account, "9999.00", "expense", date(2026, 6, 12), None, "Not mine"
    )

    fake_ai(json_message(INSIGHT_PAYLOAD))

    body = client.get(
        "/ai/insights/monthly", headers=auth_headers, params={"month": "2026-06"}
    ).json()

    assert body["facts"]["totals"]["expense"]["total"] == "100.00"
    assert Decimal(body["facts"]["totals"]["expense"]["total"]) < Decimal("9999.00")
