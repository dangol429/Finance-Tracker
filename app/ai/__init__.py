"""The AI layer — everything in this app that talks to a language model.

Four modules, one rule
----------------------

    client.py      constructs the Anthropic client and translates its failures
    query.py       #16  natural-language questions, answered with tool calls
    categorize.py  #17  category suggestions for uncategorized transactions
    insights.py    #18  a monthly write-up grounded in the user's aggregates

**The rule: the model never produces a number.** Every figure that reaches a
user is computed by PostgreSQL — by the same `SUM`/`GROUP BY` statements the
dashboard is built on — and the model's job is to decide *which* aggregate to
ask for (`query.py`), to pick a label from a fixed list (`categorize.py`), or to
narrate figures it was handed (`insights.py`). None of those three jobs involves
arithmetic, and none of them can be done wrong in a way that invents money.

That constraint is not decoration on a finance app; it is the only thing that
makes the feature shippable. A language model asked "how much did I spend on
food in June" will happily produce a confident, plausible, wrong number, and the
user has no way to tell it apart from a right one — the failure is silent, and
it is silent in exactly the place where being wrong matters most. Grounding is
what removes that failure mode, and every module here is shaped by it:

  - `query.py` gives the model *tools*, not data, and the tools run scoped SQL.
    The answer is assembled from tool results, and the tool results travel back
    to the client as `evidence` so the number in the sentence can be checked
    against the number in the query.
  - `categorize.py` constrains the model's output to a JSON schema whose `enum`
    is the user's own category names, so a hallucinated category is not
    something to detect after the fact — it is unrepresentable. What the schema
    cannot enforce (an income category on an expense row) is re-checked in
    Python.
  - `insights.py` computes every aggregate first, hands them over as facts, and
    returns those same facts alongside the prose. The write-up and the numbers
    it describes arrive together, so a claim can be audited without a second
    request.

**Ownership works exactly as it does everywhere else in this app.** Every query
in this package starts from `Transaction.user_id == current_user.id`, and the
user id comes from the access token — never from a request body, and never from
anything a model produced. A model is an untrusted input source in the same way
a JSON body is: it can ask for whatever it likes, and the scope it asks inside
is not one of the things it gets to choose.

**Nothing here is on the critical path.** `settings.anthropic_api_key` is
optional, and with it unset every endpoint in this package answers 503 while the
rest of the API is unaffected. That is deliberate: a finance tracker that cannot
record a transaction because an AI provider is down is a worse product than one
without the AI.
"""
