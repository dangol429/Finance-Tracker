# Personal Finance Tracker — Backend

FastAPI + PostgreSQL + SQLAlchemy backend for a personal finance tracker.
Docker deployment comes in a later milestone; **this is milestone 5
(aggregations): three read-only endpoints that turn the ledger into the numbers
a dashboard draws, using real SQL `GROUP BY` and aggregate functions.**

Milestone 2 built the four core tables, milestone 3 added the identity layer,
and milestone 4 was where the two met — `WHERE user_id = :me` stopping being the
plan and starting to be the code, across five CRUD endpoints whose interesting
half is what they refuse.

This one changes the question from *which rows?* to *what do they add up to?*
The whole point is that the database answers it. A year of spending might be
5,000 rows; the monthly chart above it is twelve numbers, and the difference
between computing those twelve in PostgreSQL and computing them in a Python loop
is the difference between an endpoint that stays fast and one that gets slower
every month a user keeps using the app.

## Quick start

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
copy .env.example .env         # Windows  (cp on macOS/Linux)
# edit .env with your real DB credentials

# 4. Generate a real JWT signing key and put it in .env as SECRET_KEY
python -c "import secrets; print(secrets.token_hex(32))"

# 5. Create the tables (needs PostgreSQL running)
python -m app.db.init_db

# 6. Run
uvicorn app.main:app --reload
```

| Endpoint | Auth | What it does |
|---|---|---|
| `GET /health` | — | Is the process up? |
| `GET /health/db` | — | Can it reach Postgres? |
| `POST /auth/register` | — | Create an account → `201` + the new user |
| `POST /auth/login` | — | Email + password → `{access_token, token_type}` |
| `GET /auth/me` | **Bearer** | The authenticated user's own profile |
| `POST /transactions` | **Bearer** | Record a transaction → `201` |
| `GET /transactions` | **Bearer** | Your ledger, newest first — filtered and paged |
| `GET /transactions/{id}` | **Bearer** | One transaction, or `404` |
| `PATCH /transactions/{id}` | **Bearer** | Change some fields, leave the rest |
| `DELETE /transactions/{id}` | **Bearer** | Delete it → `204` |
| `GET /docs` | — | Interactive API docs |

### Trying it

The fastest path is http://127.0.0.1:8000/docs — register, then click
**Authorize**, enter the same email and password, and the padlocked routes work
for the rest of the session. By hand:

```bash
# Register
curl -X POST http://127.0.0.1:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"a-long-enough-password"}'

# Log in — note: FORM-encoded, and the field is `username` (see below)
curl -X POST http://127.0.0.1:8000/auth/login \
  -d "username=you@example.com&password=a-long-enough-password"

TOKEN=<paste-token>

# Use the token
curl http://127.0.0.1:8000/auth/me -H "Authorization: Bearer $TOKEN"

# Record a transaction (see the note below about getting an account_id)
curl -X POST http://127.0.0.1:8000/transactions \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"account_id":1,"category_id":1,"amount":"42.50","type":"expense",
       "occurred_on":"2026-08-17","description":"weekly shop"}'

# Read the ledger back, filtered
curl "http://127.0.0.1:8000/transactions?type=expense&limit=20" \
  -H "Authorization: Bearer $TOKEN"

# Fix a typo — PATCH, so the fields you don't mention are left alone
curl -X PATCH http://127.0.0.1:8000/transactions/1 \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"amount":"45.00"}'

# --- the dashboard numbers (milestone 5) ---

# One row per month, gaps filled with zeros so a chart's x-axis is continuous
curl "http://127.0.0.1:8000/summary/monthly?date_from=2026-01-01&date_to=2026-12-31" \
  -H "Authorization: Bearer $TOKEN"

# Where the money went, largest slice first (defaults to type=expense)
curl "http://127.0.0.1:8000/summary/by-category" -H "Authorization: Bearer $TOKEN"

# The headline pair, plus a savings rate
curl "http://127.0.0.1:8000/summary/income-vs-expense" -H "Authorization: Bearer $TOKEN"
```

<details>
<summary>What <code>/summary/monthly</code> returns</summary>

```json
{
  "date_from": "2026-01-01",
  "date_to": "2026-12-31",
  "months": [
    {"month": "2026-01", "month_start": "2026-01-01",
     "income": "3000.00", "expense": "1500.00", "net": "1500.00",
     "transaction_count": 4},
    {"month": "2026-02", "month_start": "2026-02-01",
     "income": "0.00", "expense": "0.00", "net": "0.00",
     "transaction_count": 0}
  ]
}
```

February has no transactions at all. `GROUP BY` returned no row for it — the
bucket of zeros is filled in by the API, because a chart that skips from January
to March draws a line implying February didn't happen. Money is a JSON *string*
(`"1500.00"`), the same form `/transactions` already uses: a JSON number is an
IEEE double, and a total that a double can't hold exactly would come back
differing from the database in its last decimal.

</details>

> **You need an `account_id` first, and there is no `/accounts` endpoint yet** —
> accounts and categories get their own routers in the next milestone. Until
> then, seed one row each from a Python shell (`.venv` active, in the project
> root):
>
> ```python
> from app.db.session import SessionLocal
> from app.models import Account, Category, User
> with SessionLocal() as db:
>     me = db.query(User).filter_by(email="you@example.com").one()
>     db.add_all([
>         Account(user_id=me.id, name="Chase Checking", type="checking"),
>         Category(user_id=me.id, name="Groceries", type="expense"),
>     ])
>     db.commit()
> ```

## Study guide

[`docs/Finance-Tracker-Study-Guide.pdf`](docs/Finance-Tracker-Study-Guide.pdf) — a
16-page walkthrough of the reasoning behind each decision, including the
generated SQL, the gotchas, and interview Q&A. **Currently covers milestones 1–2
(setup and the data model); the auth, transaction and aggregation material above
hasn't been folded in yet.** Its
source is the sibling `.html` file; re-render it after edits with:

```bash
chrome --headless=new --no-pdf-header-footer \
  --user-data-dir=/tmp/chrome-pdf \
  --print-to-pdf=docs/Finance-Tracker-Study-Guide.pdf \
  docs/Finance-Tracker-Study-Guide.html
```

## Project structure

```
app/
├── main.py          # Entry point: creates the app, wires routers together
├── core/
│   ├── config.py    # Typed settings loaded from .env (single source of truth)
│   ├── security.py  # bcrypt hashing + JWT encode/decode  (no FastAPI imports)
│   └── deps.py      # get_current_user & friends  (no crypto)
├── db/
│   ├── base.py      # DeclarativeBase + TimestampMixin (no connection)
│   ├── session.py   # Engine, SessionLocal, get_db() request dependency
│   └── init_db.py   # `python -m app.db.init_db` — creates tables in dev
├── models/          # SQLAlchemy ORM classes  (DB layer)
│   ├── enums.py     # AccountType / TransactionType + their shared SQL types
│   ├── user.py      ├── account.py
│   ├── category.py  └── transaction.py
├── routers/
│   ├── health.py    # HTTP endpoints, grouped by feature
│   ├── auth.py      # /auth/register, /auth/login, /auth/me
│   ├── transactions.py  # full CRUD, every query scoped to the token's user
│   └── summary.py   # GROUP BY aggregates: monthly / by-category / in-vs-out
└── schemas/         # Pydantic request/response shapes  (API contract)
    ├── user.py      # UserCreate (in) / UserRead (out) — the hash never appears
    ├── transaction.py   # Create / Update / Read — no `user_id` on any input
    ├── summary.py   # output-only shapes; the aggregations take no body
    └── token.py     # {access_token, token_type}
```

`security.py` and `deps.py` are split on a deliberate line: **`security.py`
imports no FastAPI and `deps.py` imports no crypto.** One deals in strings and
signatures, the other in headers and status codes. That's what lets the hashing
be tested without a request and the 401 logic be tested without valid tokens —
and it's why `create_access_token` will serve a password-reset email later
without dragging HTTP concerns along.

## The data model

```
User ──< Account ──< Transaction >── Category
 │                       │               │
 └───────────────────────┴───────────────┘
        (user_id on every table — ownership is never inferred)
```

| Table | Belongs to | Key columns |
|---|---|---|
| `users` | — | `email` (unique), `hashed_password`, `is_active` |
| `accounts` | user | `name`, `type` (enum), `balance` `NUMERIC(12,2)`, `currency` |
| `categories` | user | `name`, `type` (income/expense) |
| `transactions` | user + account + *optional* category | `amount` `NUMERIC(12,2)`, `type`, `occurred_on`, `description` |

Unique per owner: `(user_id, name)` on both `accounts` and `categories`.

### The decisions worth defending

- **`Numeric`, never `Float`, for money.** Floats are binary — `0.1 + 0.2 != 0.3` —
  and those fractions of a cent compound across a ledger until a balance is
  visibly wrong. `NUMERIC(12, 2)` is exact decimal and maps to Python's `Decimal`.

- **Positive `amount` + a `type` enum, not signed amounts.** Direction lives in
  a column you can index, filter, and `GROUP BY`. A `CHECK (amount > 0)`
  constraint then makes "negative expense" — a row that would silently *reduce*
  a spending total — unrepresentable rather than merely discouraged.

- **`transactions.user_id` is denormalized.** It's reachable via
  `account.user_id`, so it's redundant on paper. It earns its place because
  ownership checks and the transaction feed are the hot path, and this turns
  them into one indexed scan instead of a join on every request. The price is a
  real invariant the service layer must uphold: a transaction's user must match
  its account's user.

- **Categories are per-user, not global.** A shared table would put one
  person's custom "Side Hustle" category in everyone's dropdown, and renaming it
  would relabel strangers' reports. Duplicated "Groceries" rows cost a few
  hundred bytes; the alternative leaks data between accounts.

- **`category_id` is nullable; the other two FKs aren't.** A just-imported
  transaction is legitimately uncategorized, and deleting a category must not
  erase spending history — hence `ON DELETE SET NULL` there versus
  `ON DELETE CASCADE` for user and account.

- **Cascades are declared twice, on purpose.** `ondelete=` on the ForeignKey is
  the *database* enforcing it (survives raw SQL, migrations, other clients);
  `cascade="all, delete-orphan"` on the relationship keeps the *in-memory*
  object graph consistent in the same session. `passive_deletes=True` then tells
  SQLAlchemy to let the database do the bulk work rather than loading every
  child row to delete it individually.

- **One composite index, not three single-column ones.**
  `(user_id, occurred_on)` serves "my transactions, newest first" — filter and
  sort from one index — *and* covers plain `WHERE user_id = ?` on its own,
  because an index is usable by any query constraining a prefix of its columns.
  Separate indexes on those columns would be dead weight every INSERT maintains.

- **Enum SQL types are bound to the metadata once.** On Postgres an enum is a
  database object, not a column modifier. Two columns each building their own
  `Enum(TransactionType, name="transaction_type")` makes `create_all` emit
  `CREATE TYPE` twice, and the second fails.

- **`init_db.py` is a script, not startup code.** Schema changes are a
  deploy-time decision; a web app that mutates the schema on every worker boot
  races itself into a half-created database. `create_all` also only *creates* —
  it will not alter an existing table to match a changed model, which is exactly
  where Alembic takes over in a later milestone.

## Authentication

```
POST /auth/register    password ──bcrypt(cost 12)──> hash ──> users.hashed_password
                                                              (plaintext never stored)

POST /auth/login       email + password ──verify──> HMAC-SHA256 sign ──> access token
                                                                          (30 min)

GET  /auth/me          Authorization: Bearer <token>
                              │
                              ├─ oauth2_scheme      pull token out of the header
                              ├─ decode_access_token  verify signature + expiry
                              ├─ get_current_user     sub → SELECT users WHERE id
                              └─ get_current_active_user  is_active?
                                        │
                                        └──> handler runs with a real `User`
```

### The decisions worth defending

- **bcrypt, not SHA-256.** SHA-256 is built to be *fast*, which is the wrong
  property here — a GPU does billions of guesses a second against a stolen
  table. bcrypt has a tunable cost factor (default 12 = 2¹² iterations, ~0.25s
  per hash) that turns those billions into a handful. Login being measurably
  slower than other routes isn't a bug to optimize away; it's the defense
  working. And the cost is *stored in the hash*, so raising it later doesn't
  invalidate existing passwords.

- **A per-password salt, stored in the hash itself.** `gensalt()` means the same
  password hashes differently for two users, so one precomputed rainbow table
  can't crack the whole table at once. The salt isn't secret — it's right there
  in the `$2b$12$<salt><digest>` string — because its job is uniqueness, not
  concealment.

- **The 72-byte limit is enforced, not ignored.** bcrypt reads only the first 72
  bytes and, as of bcrypt 4.x, truncates *silently* rather than raising. Left
  unchecked that's a real hole: every passphrase sharing its first 72 bytes
  would authenticate. The check counts **bytes, not characters**, because
  `len()` and UTF-8 disagree — a 72-character passphrase with accents or emoji
  is well over 72 bytes and would get quietly cut to a fraction of itself.

- **JWTs are signed, not encrypted.** The payload is base64, not ciphertext —
  paste any token into jwt.io and read it. The signature guarantees *integrity*
  (nobody can change a claim), never *privacy*. So the token carries a user id
  and two timestamps and nothing else; anything sensitive in there would be
  public.

- **`algorithms=[...]` is passed on every decode.** It's a whitelist, and
  omitting it is the classic JWT vulnerability: the library would otherwise
  trust the `alg` field in the token's own header, which the attacker wrote.
  Set it to `none` and the signature check is skipped entirely. Pinning the
  algorithm server-side means the token doesn't get a vote — verified by test:
  an `alg: none` forgery gets a 401.

- **The token's subject is the user *id*, not the email.** Ids are immutable.
  An email can change, and a still-valid token carrying the old address would
  either break or — much worse — start resolving to whoever registers that
  address next.

- **`get_current_user` hits the database, even though it doesn't have to.** The
  signature already proves the id is genuine, so the query is skippable and auth
  could be zero-database. It's there because a token stays valid until it
  expires: without the lookup, an account deleted or deactivated five minutes
  ago keeps working for the rest of the window. One indexed primary-key fetch
  buys "this account still exists *right now*".

- **Short expiry, because there is no logout.** A JWT is verified offline, so
  there's no server-side list to delete from — you cannot revoke one early. That
  makes lifetime the *only* bound on a stolen token, hence 30 minutes rather
  than 30 days. (Rotating `SECRET_KEY` invalidates every token at once, which is
  the blunt emergency version. Refresh tokens, which give revocation something
  to revoke, are a later milestone.)

- **Every auth failure returns the same message.** "No such account" and "wrong
  password" are one response, and the 401 from a protected route doesn't say
  whether the token was expired, forged, or malformed. Distinguishing them hands
  an attacker a debugger for their own forgery — and confirms which email
  addresses are registered here, which on a finance app is itself worth hiding.

- **Login spends the same time whether or not the email exists.** Uniform
  messages leak nothing, but uniform *timing* has to be bought: an unknown email
  would otherwise return instantly while a known one pays bcrypt's 0.25s, and
  that gap is measurable over the network. So a miss verifies against a
  throwaway hash. Measured: 227.2ms for a known email vs 227.7ms for an unknown
  one — a 1.00x ratio, no oracle.

- **401 and 403 mean different things.** 401 = "I don't know who you are" —
  missing, expired, or invalid token, and it carries `WWW-Authenticate: Bearer`
  as RFC 7235 requires. 403 = "I know exactly who you are, and the answer is
  still no" — the deactivated-account case. Returning 401 there would tell a
  well-behaved client to discard a perfectly good token and re-prompt for a
  password that was never the problem.

- **Protection is a dependency, not middleware.** A route is protected by asking
  for `CurrentUser` in its signature. The alternative — matching protected paths
  by URL in middleware — fails *open*: a new route is unguarded until someone
  remembers to add it, and a typo'd pattern opens a hole silently. Here the
  dependency is visible in the same three lines as the handler, it's typed (a
  real `User`, not `request.state.user`), and FastAPI walks the dependency tree
  to build OpenAPI — which is why `/auth/me` shows a padlock in `/docs` and
  `/health` doesn't, with nothing configured to say so.

- **Login takes a form, not JSON, and the field is called `username`.** That's
  the OAuth2 password-grant spec (RFC 6749), and matching it is what lets the
  Authorize button in `/docs` log in and then attach the token to every
  subsequent request with no custom code. The field stays `username` even though
  this app authenticates by email, because renaming it would break the standard
  clients that are the whole reason for the form.

- **Emails are lowercased on the way in.** `Foo@Example.com` and
  `foo@example.com` are one mailbox to a human and two strings to a UNIQUE
  index. Without normalizing, both register and the second person is silently
  locked out of the account they think they created. Login normalizes too —
  form data skips Pydantic entirely, so the validator on `UserCreate` doesn't
  cover it.

- **The duplicate-email check is for the error message; the UNIQUE index is for
  correctness.** The `SELECT` before the `INSERT` is a race by construction —
  two simultaneous signups both read "no such email" and both insert. So
  `IntegrityError` is caught and turned into the same 409. The general rule:
  application checks give good errors, database constraints give guarantees;
  never rely on only the first.

- **The app refuses to boot in production with the placeholder secret.** The
  dev default is committed to this repository, so a deployment still using it
  would accept tokens forged by anyone who read the source — and nothing would
  look wrong, because a bad secret breaks no visible behaviour. Hence a
  validator that fails *closed* at import time. It keys off an explicit
  `ENVIRONMENT` setting rather than `DEBUG`, since those answer different
  questions (verbosity vs. whether real users' data is at stake), and
  `ENVIRONMENT` is a `Literal` so a typo like `producton` crashes loudly instead
  of quietly disabling the guard.

## Transactions

```
POST /transactions
      │
      ├─ CurrentUser          token ──> a real, active User          (401 / 403)
      ├─ TransactionCreate    shape, amount > 0, NUMERIC(12,2), date  (422)
      ├─ _require_owned_account    SELECT ... WHERE id=? AND user_id=?  (404)
      ├─ _require_owned_category   ...same, plus income/expense match  (404 / 422)
      └─ INSERT  user_id = current_user.id   ← from the token, never the body
                                 │
                                 └─ FK violation on commit ──────────> 409
```

Every read is the same shape in reverse — `WHERE user_id = :me` first, filters
after:

```sql
SELECT * FROM transactions
 WHERE user_id = :me                    -- not a filter; the scope
   AND (:account_id IS NULL OR account_id = :account_id)   -- ...filters
 ORDER BY occurred_on DESC, id DESC     -- id is what makes paging correct
 LIMIT :limit OFFSET :offset;
```

### The decisions worth defending

- **Ownership is a `WHERE` clause, never an `if` after the fetch.** Both reject
  the request, but only one can't be forgotten: a missing `WHERE` makes the
  query visibly wrong, while a missing `if` five lines into a handler looks like
  nothing at all. It also means there is no window where someone else's row is
  loaded in memory, one early `return` away from being serialized.

- **Another user's row returns 404, not 403.** 403 means "this exists and you
  can't have it" — which confirms it exists. Walk the ids and you've mapped how
  many transactions the app holds and roughly when other people are active. 404
  makes someone else's transaction and a transaction that was never created
  indistinguishable from outside. Same reasoning as login's single error message
  for "no such account" and "wrong password": don't answer questions you weren't
  asked.

- **404 for "not found or not yours", 422 for "both exist and contradict".** A
  body reference to an account is as much an oracle as a path parameter, so an
  unowned `account_id` is a 404 too. But filing groceries under a "Salary"
  category is different in kind — both rows were found, both are yours, nothing
  is being concealed. The request is well-formed and its meaning is impossible,
  which is precisely what 422 is for.

- **Filtering by someone else's `account_id` returns `[]`, not 404.** The
  `user_id` scope has already made that condition unsatisfiable, so an empty page
  falls out for free — and it's the one answer that reveals nothing about whether
  that account exists. A 404 here would reintroduce the oracle the point above
  closes.

- **`TransactionCreate` has no `user_id` field.** Not "ignored", not
  "overwritten by the handler" — absent, so the request has no way to express
  the idea. The value comes from the token, and the closest a client can get to
  suggesting otherwise is a 422 from `extra="forbid"`.

- **Unknown JSON keys are rejected, not dropped.** Pydantic's default is to
  ignore them, which for a hand-written API means `{"catagory_id": 3}` is
  accepted, stored uncategorized, and reports success — leaving the client to
  debug a field it believes it sent. One 422 finds that at the only moment
  anyone is looking.

- **PATCH, not PUT.** PUT means "replace this resource", so fixing a typo in an
  amount requires resending every other field, and any the client forgets get
  wiped. That's data loss waiting on a forgetful caller. PATCH means "change
  what I mention", which is what editing a row actually is.

- **`null` and *absent* are different, and only nullable columns may be
  cleared.** `{"description": null}` clears it; `{}` leaves it alone. Pydantic
  gives both the same attribute value, so the distinction lives in
  `model_fields_set` — which is what `model_dump(exclude_unset=True)` reads.
  Explicit `null` for `amount`, `type`, `occurred_on`, or `account_id` is a 422,
  because `NOT NULL` would otherwise turn a client's mistake into a 500. An
  empty body is a 422 too: it's almost always a serializer that dropped the
  payload, and a 200 hides that.

- **A patch that changes only `type` re-checks the category already on the row.**
  The category isn't mentioned in the request and may have just become illegal
  for it. Validating the *post-patch* state rather than the incoming fields is
  what catches an expense flipped to income while still filed under "Groceries" —
  the kind of row that breaks a report months later and can't be traced back.

- **`ORDER BY occurred_on DESC, id DESC` — the `id` is not decoration.** Without
  a unique tiebreaker, rows sharing a date have no defined order between two
  queries, so PostgreSQL may order them differently for `offset=0` and
  `offset=50`: one row appears on both pages, another on neither. It looks like
  data loss and reproduces almost never. The sort also matches
  `ix_transactions_user_id_occurred_on` exactly, and an index scans backwards, so
  DESC is free.

- **`limit` has a default *and* a ceiling.** The default protects the caller who
  forgot to page; `le=200` protects the server from the one who didn't forget.
  OFFSET is honest for page-numbered UIs and degrades on deep pages — the fix
  when that hurts is keyset pagination, which the sort order above is already
  shaped for.

- **Amounts reject 10.999 rather than rounding it.** `max_digits=12,
  decimal_places=2` mirrors `NUMERIC(12, 2)` exactly, and `gt=0` mirrors the
  `CHECK` constraint — the schema for a readable 422, the constraint for the
  guarantee. Quietly turning a client's number into a different number is how a
  cent goes missing and nobody can say where.

- **`occurred_on` may be at most one day ahead of UTC.** This is a ledger of
  money that moved, and the default sort is newest-first — so a fat-fingered
  `3025-01-04` doesn't merely sit in the data, it pins itself to the top of
  every page forever. The one day of slack is because the server clocks in UTC
  while users run as far ahead as +14, and rejecting someone's genuinely-today
  purchase is a worse failure than accepting one that's a day early. Genuinely
  scheduled transactions are a `scheduled` flag plus a job, not a loosened
  validator.

- **The denormalized `transactions.user_id` is written from the token, right
  after the account is proven to be the caller's.** The model flags that column
  as an invariant the service layer owes it — this is where that debt is paid.
  Copying `payload.account_id` in without the ownership check is exactly how the
  denormalization turns from an optimization into corruption.

- **`IntegrityError` on commit becomes a 409.** The ownership checks are a race
  by construction: an account can be deleted between the `SELECT` that proved it
  exists and the `INSERT` that references it. The FK is what guarantees the
  reference is real; the checks exist for the error message. Same rule as the
  duplicate-email 409 — application checks give good errors, database
  constraints give guarantees.

- **DELETE returns 204 with no body, and a repeat DELETE returns 404.** The
  status code already says it worked; inventing `{"deleted": true}` gives clients
  something to parse that the next endpoint won't have. And 404 on the second
  call is the honest answer to "delete the thing at this id" once there isn't
  one — correctly indistinguishable from an id that was never yours.

- **`signed_amount` is computed on the way out, not stored.** A signed column
  next to `amount` and `type` is three fields that can disagree, and one day two
  of them will. It's a property on the model that `from_attributes` picks up.

- **What this milestone deliberately does *not* do: touch `Account.balance`.**
  Keeping a stored balance correct means applying a delta on create, reversing
  and re-applying it on update (including when the account itself changes),
  reversing it on delete, and doing all of that atomically under concurrent
  writes — `UPDATE accounts SET balance = balance + :delta`, not a read-modify-
  write. That's a milestone, not a line, and doing it halfway is worse than not
  starting: a balance that's *usually* right is one nobody can trust or audit.
  Until then it stays at its default and the ledger is the source of truth.

## Aggregations

Three read-only endpoints, one idea: **the database does the arithmetic.**

```
GET /summary/monthly            ─┐
GET /summary/by-category         ├─ all three:  WHERE user_id = :me   ← the scope
GET /summary/income-vs-expense  ─┘              + optional account_id / date range
                                                + GROUP BY  →  one row per bucket
```

| Endpoint | Groups by | Aggregates | Shape back |
|---|---|---|---|
| `/summary/monthly` | `date_trunc('month', occurred_on)` | `SUM ... FILTER`, `COUNT(*)` | one row per month, gaps filled |
| `/summary/by-category` | `category_id, name` (LEFT JOIN) | `SUM`, `COUNT(*)`, `AVG` | slices + share of total, largest first |
| `/summary/income-vs-expense` | `type` | `SUM`, `COUNT(*)`, `AVG`, `MAX` | two sides pivoted into one object |

The monthly query, in full:

```sql
SELECT CAST(date_trunc('month', CAST(occurred_on AS TIMESTAMP)) AS DATE) AS month_start,
       COALESCE(SUM(amount) FILTER (WHERE type = 'income'),  0) AS income,
       COALESCE(SUM(amount) FILTER (WHERE type = 'expense'), 0) AS expense,
       COUNT(*)                                                 AS transaction_count
  FROM transactions
 WHERE user_id = :me                     -- the scope, before any filter
   AND occurred_on >= :date_from         -- ...the filters
 GROUP BY 1
 ORDER BY 1;
```

### The decisions worth defending

- **The aggregate is the pagination.** `GET /transactions` needs a `limit` and a
  ceiling because an unbounded list is a memory hazard. These endpoints need
  neither, and that isn't an oversight — `GROUP BY` collapses arbitrarily many
  transactions into one row per bucket, so the response size is bounded by the
  calendar or by the user's category list rather than by their spending history.
  The Python-loop version of these endpoints has a cost that grows every month a
  user keeps using the app; this one's doesn't.

- **`SUM(...) FILTER (WHERE type = 'income')` — conditional aggregation.** One
  scan produces both totals, so each month arrives as a single row with income
  and expense side by side. `GROUP BY (month, type)` is the naive shape and it
  returns *up to* two rows per month, which every client then has to pivot — and
  pivot carefully, since a month with only expenses yields one row, not two. The
  portable spelling is `SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END)`;
  `FILTER` is the same idea with the condition where it belongs.

- **`COALESCE(..., 0)`, because an aggregate over zero rows is NULL.** SQL is
  being precise about the difference between "nothing was added up" and "the
  total was zero". For a chart those genuinely mean the same thing, so the API
  collapses them. **That reasoning deliberately does not extend to
  `savings_rate`**, which stays `null` when income is zero: percent-of-income
  with no income is undefined, and both available lies are bad — `0` reads as
  "saved nothing" (false; there was nothing to save) and `-100` reads as a
  catastrophe. The test is whether zero is the *honest* answer, not whether it's
  the convenient one.

- **A month with no transactions still gets a bucket.** `GROUP BY` emits no row
  for a group that has no rows, so a user who took January off gets a series
  jumping from December to February — and every charting library draws a
  straight line between them, implying a January value the data never contained.
  Filling the gap in the API means every consumer gets the same answer instead
  of each inventing its own. PostgreSQL can do this with `generate_series`
  LEFT JOINed to the aggregate, and on a larger result that would be the right
  call; here the bounds are often *derived from the rows the query just
  returned*, so doing it in SQL would cost a second query or a CTE to save a
  loop over twelve rows already in memory.

- **The category join is a LEFT JOIN, and the ownership check is in the `ON`
  clause.** `category_id` is nullable — an imported transaction is legitimately
  uncategorized — and an inner join drops those rows, which is the worst kind of
  bug: the response still looks complete, the slices still render, and the pie is
  simply missing money. Moving `c.user_id = :me` from `ON` into `WHERE` looks
  equivalent and silently undoes it, since unmatched rows have `c.user_id IS
  NULL` after the join and `NULL = :me` isn't true. That's the classic way a LEFT
  JOIN degrades into an inner one.

- **`COUNT(*)`, not `COUNT(c.id)`.** `COUNT(column)` skips NULLs, so the
  uncategorized group — whose `c.id` is NULL on every row — would report a count
  of `0` sitting next to a real, non-zero total.

- **`date_trunc` gets an explicit `::TIMESTAMP` cast.** PostgreSQL has
  `date_trunc(text, timestamp)`, `(text, timestamptz)` and `(text, interval)`; a
  `date` implicitly converts to two of them, and PostgreSQL breaks the tie by
  *preferring `timestamptz`* — dragging the session's `TimeZone` setting into a
  calculation that has nothing to do with clocks. Casting first picks the
  overload exactly and keeps the arithmetic in calendar space, which is where
  `occurred_on` already lives (it's a `Date`, not a `DateTime`, precisely so a
  purchase can't land in the wrong month).

- **`func.avg()` needs its type declared; `func.sum()` doesn't.** SQLAlchemy
  treats `sum` and `max` as "return type from args", so they inherit
  `Numeric(12, 2)` from the column. `avg` isn't in that set — it compiles with a
  `NullType`, no result processing runs, and you get back whatever the driver
  produced. On PostgreSQL that happens to be a `Decimal`, which means the code
  would be leaning on psycopg's type mapping rather than on anything it stated.
  `func.avg(col, type_=Numeric())` makes the contract the query's.

- **SQL computes, Python formats.** Every figure that touches a transaction row
  — `SUM`, `COUNT`, `AVG`, `MAX`, the grouping, the filtering, the ordering — is
  the database's. Rounding to the cent, percent shares, the `"YYYY-MM"` label
  and the gap-filling are Python's, over the handful of rows that came back.
  Rounding in SQL would bake a *display* decision into the query, so the export,
  the chart and the API would each need their own copy of it.

- **Percent shares are rounded independently and need not total exactly 100.**
  Three equal thirds come back as `33.33` three times. That's why every response
  also carries the raw `total` it divided: a client that needs to reconcile does
  it against the real figure, not against the rounded shares.

- **`net` is computed from the already-rounded pair.** So `income - expense ==
  net` holds exactly in the JSON a client receives. Rounding after the
  subtraction instead would let a chart's own arithmetic disagree by a cent with
  the number printed beside it — the kind of discrepancy that gets reported as a
  bug in the ledger rather than as a rounding artefact.

- **A missing `GROUP BY` group must not shift the other one.** A user with no
  income produces exactly one row from `GROUP BY type`, and code that reads
  `rows[0]` as income reports their *expenses* as earnings. The pivot goes
  through a dict with an explicit zeroed default, because for a new account the
  empty case is the normal one, not an edge.

- **Filtering by someone else's `account_id` returns an empty summary, not
  404** — same reasoning, and the same non-answer, as the ledger's list endpoint.
  The `user_id` scope has already made the condition unsatisfiable.

- **Why a missing scope would be worse here than on a list endpoint.** A leak on
  `GET /transactions` at least hands back rows with ids someone can notice are
  wrong. A missing `WHERE user_id = :me` on an aggregate silently folds other
  people's money into a total that looks entirely plausible — there's no id to
  spot, just a number that's too big.

- **What this milestone deliberately does *not* do: cache, or add a
  `top=N` collapse.** Both are real features and both are premature. The
  aggregate already bounds the response size, and the queries are served by the
  existing `(user_id, occurred_on)` index; adding a cache before there's a
  measurement is how you acquire an invalidation bug in exchange for nothing.

## Why it's laid out this way (the interview answer)

The guiding idea is **separation of concerns**: each folder owns one job, so a
change in one layer doesn't ripple through the others. Concretely:

- **`core/config.py` — one place for configuration.** Settings are loaded and
  *validated* once (via `pydantic-settings`) into a `settings` object that the
  rest of the app imports. Nothing reads `os.environ` scattered across the
  codebase, so there's a single, typed source of truth and no surprise about
  where a value came from.

- **`core/security.py` vs `core/deps.py` — crypto separated from HTTP.**
  `security.py` imports no FastAPI: it's pure functions over strings, so
  hashing and token signing can be tested without a request object, and
  `create_access_token` can serve a password-reset flow later without dragging
  status codes along. `deps.py` imports no crypto: it's the layer that knows a
  failed decode means `401` with a `WWW-Authenticate` header. Each is testable
  without the other, which is the point of the seam.

- **`routers/` — the HTTP layer.** Each file is a `APIRouter` for one feature
  area (`health`, `auth`, `transactions`, `summary`; `accounts` later).
  `main.py` just calls `include_router()` on each. This is what keeps `main.py`
  thin and lets the API grow by *adding a file* rather than by growing one giant
  file — a claim milestones 4 and 5 both cashed: five CRUD endpoints, then three
  aggregation endpoints, each costing one new file and one line in `main.py`
  with nothing above it touched. Note that `main.py` says
  nothing about which routes are protected — that lives in each handler's
  signature, so a new route can't be left unguarded by an omission in the
  wiring.

- **`models/` vs `schemas/` — the deliberately-split pair.** This is the split
  interviewers usually probe:
  - `models/` = **SQLAlchemy** classes — how data is stored in PostgreSQL.
  - `schemas/` = **Pydantic** classes — what the API accepts and returns.

  Keeping them separate means the database shape and the public API contract can
  evolve independently. You can add an internal DB column without exposing it,
  or hide a field like `hashed_password` from responses, without one concern
  leaking into the other. That stopped being hypothetical this milestone:
  `/auth/register` returns the ORM `User` object — hash and all — and the
  response contains no hash, because `UserRead` has no field for it. FastAPI
  serializes *through* the schema and drops the rest. The leak is prevented by
  the contract, not by a handler remembering to strip a field.

- **`db/` — connection lifetime, kept away from the models.** `base.py` holds
  `Base` and nothing that opens a socket, so tests and migrations can import the
  metadata without a live database. `session.py` owns the two lifetimes that get
  confused: **one `Engine` per process** (it owns the connection pool) and **one
  `Session` per request** (it owns a unit of work). `get_db()` yields a session
  and closes it in a `finally`, so a connection returns to the pool whether the
  route succeeds or raises — but it never commits, because a dependency
  shouldn't decide that a half-finished request is worth persisting.

- **`main.py` stays thin.** It creates the app and registers routers — no
  business logic. Its one non-obvious line is `import app.models`: importing the
  package registers every model on `Base.metadata` and in the mapper registry,
  which is what lets relationships resolve each other by *string* name
  (`Mapped["Account"]`) instead of by import — the trick that keeps four models
  referencing each other without a circular import.

**What was deliberately *left out* (and why that's the right call here):** no
service/repository layer, no dependency-injection framework, no Alembic yet —
a personal project of this size doesn't need those abstractions, and adding them
early is over-engineering. The structure gives clean seams to introduce them
*if* the project grows, without paying for them now. Being able to say *why you
stopped here* is as much the point as the structure itself.

FastAPI's `Depends` is worth calling out as the exception that proves the rule:
it's a DI system, but it came with the framework rather than being a library
bolted on, and `get_current_user` is what makes it earn its place — one
signature parameter replaces a token-parsing block in every protected handler.

The honest caveats on stopping here:

- `create_all` gets you a schema, not schema *evolution*. The moment a column
  changes on a database with real rows in it, Alembic stops being optional.
- **No refresh tokens**, so 30 minutes means re-entering a password every 30
  minutes, and no token can be revoked before it expires.
- **No rate limiting on `/auth/login`.** bcrypt's cost slows an *offline*
  attack on a stolen table; it does nothing about someone posting guesses at
  this endpoint all day. That's the most important gap on this list.
- **No email verification and no password reset**, so an address is never
  proven to belong to whoever typed it, and a forgotten password is
  unrecoverable.
- **Tokens are returned in a JSON body**, which means the client stores them —
  and `localStorage` is readable by any XSS on the page. An httpOnly cookie
  moves that risk (and adds CSRF to handle instead); neither is free.
