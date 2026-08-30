# Personal Finance Tracker

A personal finance dashboard: record what you spend, import a bank statement,
and see where the money actually went.

**FastAPI · PostgreSQL · SQLAlchemy · React · TypeScript · Recharts · Docker**

<!-- Add once deployed — see DEPLOYMENT.md
**[Live demo](https://your-app.vercel.app)** · demo@example.com / demo1234
-->

<!--
SCREENSHOTS — the one thing this README is still missing.

Take three, at 1440px wide, in dark mode, with the seeded demo data:
  docs/screenshots/dashboard.png    the stat cards + all three charts
  docs/screenshots/transactions.png the table mid-edit, with filters visible
  docs/screenshots/mobile.png       the card list at 390px

then replace this comment with:

| Dashboard | Transactions |
|---|---|
| ![Dashboard](docs/screenshots/dashboard.png) | ![Transactions](docs/screenshots/transactions.png) |
-->

---

## What it does

| | |
|---|---|
| **Record** | Add, edit and delete transactions inline, with optimistic updates so nothing waits on the network |
| **Import** | Upload a bank-statement CSV; bad rows are skipped and reported by line number, not silently guessed at |
| **Analyse** | Spending by category, monthly trend, and net position — every figure computed by PostgreSQL, not in a Python loop |
| **Filter** | Date range, account, category, type and debounced search, all driven from the URL so a view is shareable |

## The stack, and why

| Layer | Choice | Why |
|---|---|---|
| API | FastAPI | Typed handlers that generate their own OpenAPI spec; dependencies make auth a function parameter rather than a URL pattern |
| Database | PostgreSQL | `GROUP BY`, `date_trunc`, `SUM(...) FILTER`, real `NUMERIC`, real enums, real foreign keys |
| ORM | SQLAlchemy 2.0 | Typed models, and `insertmanyvalues` for batched inserts on import |
| Frontend | React + TypeScript + Vite | Typed API contract end to end; instant dev server |
| Data layer | TanStack Query | Optimistic updates with rollback, and cache invalidation that keeps table and charts in step |
| Charts | Recharts | Composable SVG charts that theme from CSS custom properties |
| Styling | CSS Modules + design tokens | Scoped class names, one token file, dark and light from the same variables |
| Infra | Docker Compose | One command to a working stack; the same image deploys |

## Quick start

**Everything, with Docker:**

```bash
docker compose up --build          # Postgres + schema + API
# → http://127.0.0.1:8000/docs

cd frontend && npm install && npm run dev
# → http://localhost:5173
```

Compose starts Postgres, waits for it to be genuinely ready, runs the schema
step once, and only then starts the API — see [Docker](#docker) for how each of
those is enforced. Sign up in the browser and the app walks you through creating
your first account.

```bash
docker compose down                # stop, keeping the data
docker compose down -v             # stop and delete the database volume
```

**Tests** (needs a Postgres; `docker compose up -d db` is enough):

```bash
pip install -r requirements-dev.txt && pytest
```

**Deploying:** see [DEPLOYMENT.md](DEPLOYMENT.md) — Vercel for the frontend,
Railway for the API and database, and the two environment variables that break
everything if they disagree.

---

## How it was built

Nine milestones, each with a section below explaining the decisions worth
defending:

| | Milestone | |
|---|---|---|
| 1–2 | Setup and the data model | [The data model](#the-data-model) |
| 3–4 | JWT auth, then per-user scoping | [Authentication](#authentication) · [Transactions](#transactions) |
| 5 | Aggregation endpoints | [Aggregations](#aggregations) |
| 6 | CSV import | [CSV import](#csv-import) |
| 7 | Test suite | [Tests](#tests) |
| 8 | Docker Compose | [Docker](#docker) |
| 9 | React frontend | [Frontend](#frontend) |

<details>
<summary>Without Docker — running against your own PostgreSQL</summary>

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

</details>

**Running the tests** (needs a Postgres; `docker compose up -d db` is enough):

```bash
pip install -r requirements-dev.txt
pytest
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

# --- bulk import (milestone 6) ---

# A small statement to play with. Note the quoted comma and the blank category.
cat > statement.csv <<'CSV'
Transaction Date,Amount,Description,Category
2026-03-04,-45.20,"COFFEE, LARGE",Groceries
2026-03-05,1500.00,March salary,Salary
2026-03-06,-12.00,Bus fare,
2026-03-07,-9.99,Missing a category,Rent
CSV

# Check it first — parses and validates everything, writes nothing
curl -X POST "http://127.0.0.1:8000/transactions/import?dry_run=true" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@statement.csv" -F "account_id=1"

# Then do it for real
curl -X POST http://127.0.0.1:8000/transactions/import \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@statement.csv" -F "account_id=1"
```

<details>
<summary>What <code>/transactions/import</code> returns</summary>

```json
{
  "filename": "statement.csv",
  "account_id": 1,
  "dry_run": false,
  "total_rows": 4,
  "imported": 3,
  "failed": 1,
  "errors": [
    {"row": 5, "field": "category", "value": "Rent",
     "reason": "no category named 'Rent' (create it first, or clear the column to import the row uncategorized)"}
  ],
  "errors_truncated": false
}
```

200, not 4xx, with three rows written and one skipped — partial success is the
normal outcome of a bulk import and there's no status code that means "mostly".
`imported + failed == total_rows` always holds. `row` is the line number in the
file, so the fix is "open line 5"; `errors` is capped at 100 entries while
`failed` stays exact, which is what `errors_truncated` is for.

Note what happened to the amounts on the way in: `-45.20` became `45.20` with
`type = "expense"`, because the model stores magnitudes and lets `type` carry
the sign. A statement that instead has a `direction`/`type` column of its own
(`debit`, `credit`, `withdrawal`, …) works too — and a row where the two
*disagree*, like `-50.00` labelled `credit`, is rejected rather than resolved.

</details>

> The `account_id` is a form field rather than a CSV column on purpose: one
> upload is one bank statement, which is one account. That makes the ownership
> check a single `SELECT` performed before a byte of the file is parsed, instead
> of a per-row question a file could answer differently on every line.

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
Dockerfile           # Two-stage build: deps in a venv, then a clean runtime
docker-compose.yml   # db + one-shot schema step + api, wired together
.dockerignore        # Keeps .venv/, .git/ and .env out of the build context
DEPLOYMENT.md        # Vercel + Railway, and the two env vars that break it
pytest.ini           # pythonpath, testpaths, --strict-markers
requirements.txt     # Runtime deps — what the image installs
requirements-dev.txt # pytest, httpx, ruff — deliberately NOT in the image

frontend/            # React + TypeScript SPA (see the Frontend section)

tests/
├── conftest.py      # Real Postgres, one rolled-back transaction per test
├── test_auth.py     ├── test_transactions.py
├── test_summary.py  └── test_csv_import.py

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
│   ├── summary.py   # GROUP BY aggregates: monthly / by-category / in-vs-out
│   ├── csv_import.py    # multipart upload -> parse -> validate -> one batch
│   ├── accounts.py  # list + create — the frontend needs these to exist
│   └── categories.py    # list (filterable by type) + create
└── schemas/         # Pydantic request/response shapes  (API contract)
    ├── user.py      # UserCreate (in) / UserRead (out) — the hash never appears
    ├── transaction.py   # Create / Update / Read — no `user_id` on any input
    ├── summary.py   # output-only shapes; the aggregations take no body
    ├── csv_import.py    # the import report: a tally plus a defect list
    ├── account.py   # AccountCreate / AccountRead
    ├── category.py  # CategoryCreate / CategoryRead
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

## CSV import

One endpoint, and the first one whose request body is a file:

```
POST /transactions/import          multipart/form-data
    file=@statement.csv            the upload
    account_id=1                   form field — one statement is one account
    ?dry_run=true                  optional: validate everything, write nothing
```

The pipeline, and where each kind of failure is answered:

```
read (capped at 5 MB)  ──► 413   the request never had a chance
decode utf-8-sig       ──► 422   not text this app can read
map header aliases     ──► 422   wrong file entirely — fails before row 1
    │
    └─► per row:  ragged? ─► date ─► amount ─► direction ─► category ─► TransactionCreate
                     │        │        │           │            │              │
                     └────────┴────────┴───────────┴────────────┴──────────────┘
                                          ▼
                            skipped, reported by line number   (200, in the report)
                                          ▼
                     survivors ──► add_all ──► ONE commit ──► 409 only if the FK broke
```

The columns it understands. Only the first two are required, and every name is
matched case-insensitively with spaces normalized, so `Transaction Date` and
`transaction_date` are the same column:

| Canonical | Accepted headers | Notes |
|---|---|---|
| `date` | `date`, `transaction_date`, `posted_date`, `posting_date`, `value_date`, `occurred_on` | ISO only; a trailing time is dropped |
| `amount` | `amount`, `value` | signed, or a magnitude paired with `type` |
| `type` | `type`, `direction`, `transaction_type` | `debit`/`credit`, `withdrawal`/`deposit`, … |
| `description` | `description`, `memo`, `narrative`, `details`, `payee` | first match in header order wins |
| `category` | `category` | resolved by name against *your* categories |

### The decisions worth defending

- **One bad row is not a bad file — and one ambiguous row is not a row.** These
  pull in opposite directions and both matter. Rejecting the whole upload over a
  stray blank line means the user fixes one thing, re-uploads, and finds the next
  one; so rows are independent. But the reason to skip a row rather than
  interpret it is that a *quietly* wrong ledger is the worse failure. `04/03/2026`
  is the 4th of March in most of the world and the 3rd of April in the US, and a
  parser that picks one is wrong about a third of a real statement while
  reporting complete success. Rejected, it costs one column edit.

- **`45,20` is refused, not stripped.** The obvious amount parser removes commas
  so `1,234.56` works — and silently turns the European spelling of forty-five
  euros twenty into **4520**. A hundred-fold error, on money, with no symptom
  until someone reads their spending report. So commas are allowed only in valid
  thousands positions and rejected anywhere else. `(45.20)`, the accounting
  negative that falls out of every currency-formatted spreadsheet, is understood.

- **`csv.DictReader`, never `line.split(",")`.** The split version is shorter and
  wrong on the first `"COFFEE, LARGE"` — it shifts every later field one column
  over, so an amount gets parsed out of a description. Quoting, escaped quotes
  and embedded newlines *are* the format, not edge cases. `restkey`/`restval` are
  set explicitly so a ragged line is detected instead of silently misaligned.

- **`utf-8-sig`, and no encoding fallback.** Excel writes a BOM; under plain
  `utf-8` those three bytes become part of the first header name, so `date`
  silently becomes `﻿date` and the error says the file has no date column
  while the user is looking straight at one. Anything that isn't valid UTF-8 is
  refused rather than guessed at — cp1252 would rescue a pound sign and would
  also decode genuinely broken bytes into plausible garbage, landing it in the
  description column, which is the one field nothing downstream validates.

- **Rows are validated by `TransactionCreate` — the same schema `POST
  /transactions` uses.** The parsers here turn the file's *notation* into Python
  values; every actual rule (amount positive, `NUMERIC(12,2)` fits, no future
  dates, description length) stays in one place. Re-implementing them for the
  import path is the trap, because the copies drift — and the import path is
  precisely the one that gets thousands of rows with no human reading them, so a
  rule that quietly went missing there does the most damage before anyone notices.

- **Categories are looked up once, not once per row.** The N+1 problem in its
  most avoidable form: a `SELECT` inside the loop turns a 400-row import into 401
  round trips to distinguish four category names. One query builds a dict; the
  loop does lookups. An unknown name *rejects* the row rather than importing it
  uncategorized (a success whose result is wrong is what the report exists to
  prevent) and rather than creating the category (which makes every typo
  permanent). `dry_run=true` is what makes that cheap: preview, see which names
  are missing, create them, import.

- **The handler is `def`, not `async def`.** `await file.read()` is the tempting
  spelling and it forces the whole function async — at which point every blocking
  psycopg2 call in it runs *on the event loop* and one slow import stalls every
  other request the process is serving. A plain `def` handler is dispatched to a
  threadpool, and `UploadFile.file` is the synchronous file object underneath.
  Every other route in this app is `def`; this is the one where the temptation
  to differ shows up.

- **The upload is read in chunks against a byte cap.** Neither FastAPI nor
  Starlette limits request body size by default, so `.read()` with no argument is
  a memory bomb with a one-line trigger. Checking the length *after* reading
  would measure a file that is already resident — the cap has to be enforced
  while the bytes arrive. A proxy in front of this should have its own limit; the
  app shouldn't depend on deployment topology to stay up.

- **One `add_all`, one commit.** Per-row commits mean 400 transactions, 400
  fsyncs, and a failure halfway through leaving a half-imported statement that
  the user has to reconcile by hand — with the rows that *did* land being exactly
  the ones a retry would duplicate. The INSERTs inside that one transaction are
  batched by SQLAlchemy 2.0's `insertmanyvalues`, and the reason is worth
  knowing: the ORM needs each new row's `id`, and PostgreSQL has no
  `cursor.lastrowid`, so it fetches them with `RETURNING` — which works on a
  multi-row INSERT. Hence `INSERT ... VALUES (...), (...), ... RETURNING id` in
  pages of 1000. (Run the same code on SQLite and you get one INSERT per row; the
  batching is a property of the dialect, not of `add_all`. `bulk_save_objects` is
  the 1.x tool for this and is legacy in 2.0 because none of it needs asking for.)

- **`dry_run` runs the same code path and stops one line short of the commit.**
  A preview implemented as a separate, simpler validation pass is a preview of a
  different program, and the rows the two disagreed about would be the ones that
  mattered.

- **The response is a report, not the created rows.** Returning 5,000 serialized
  transactions to answer "did it work?" is work done to be thrown away, and it
  still leaves nowhere to say what happened to the rows that failed. So: a tally
  where `imported + failed == total_rows` by construction, plus a defect list
  capped at 100 entries with `errors_truncated` saying so — a structurally wrong
  file is wrong in every row, and 50,000 copies of one mistake is not a report.
  Each entry names the field and the offending cell, and deliberately not the
  rest of the line: a statement row is someone's private spending, and this
  object travels into logs, toasts and screenshots that the row itself doesn't.

- **What this milestone deliberately does *not* do: detect duplicates.**
  Re-uploading an overlapping statement imports the overlap twice. Solving it
  properly is a schema change rather than a check — a stable fingerprint (the
  bank's own reference id, else a hash of account + date + amount + description)
  under a unique index, so the guarantee comes from PostgreSQL instead of from a
  `SELECT` that races the insert. Doing it halfway, by skipping rows that
  *look* like existing ones, is worse than not doing it: it drops the second
  identical coffee someone genuinely bought that day.

## Tests

Twenty of them, in about ten seconds:

```bash
pytest                       # all of it
pytest tests/test_auth.py    # one file
pytest -k ownership          # by name
```

| File | Covers |
|---|---|
| `tests/test_auth.py` | register, duplicate email, login, account enumeration, token validity |
| `tests/test_transactions.py` | create / list / filter / patch / delete, and who can see what |
| `tests/test_summary.py` | gap-filling, category shares, savings rate, scoping |
| `tests/test_csv_import.py` | a real-shaped statement, skipped bad rows, dry run |

### The decisions worth defending

- **They run against real PostgreSQL, not SQLite.** That costs a running server
  and it buys the only thing a suite is for. SQLite cannot run `/summary/*` at
  all — those endpoints are built on `date_trunc` and `SUM(...) FILTER (WHERE
  ...)` — so the choice isn't "slower tests" versus "faster tests", it's
  "tests" versus "no tests on the most intricate code in the project". It would
  also skip the `CHECK (amount > 0)` constraint, the `ON DELETE SET NULL`
  behaviour, and the real `ENUM` types. A suite that passes against a database
  the application will never use reports on a program nobody runs.

- **One transaction per test, rolled back.** The `db_session` fixture opens a
  connection, begins a transaction, and rolls it back at the end, so every test
  starts from an empty database without the schema being rebuilt. The load-bearing
  detail is `join_transaction_mode="create_savepoint"`: handlers under test call
  `db.commit()`, which would normally end that outer transaction, and this makes
  their work happen inside a SAVEPOINT instead. Nothing in the application is
  written differently because it is being tested.

- **Authentication is exercised, not stubbed.** `get_db` is the only dependency
  overridden. Tests log in through `/auth/login` and send real bearer tokens, so
  the signature check, the user lookup and the `is_active` check all run.
  Overriding `get_current_user` would make the suite faster and would stop it
  ever noticing that auth broke.

- **Most of these tests assert on what the API refuses to say.** A hash that
  never appears in a response; one error message shared by "wrong password" and
  "no such account"; a 404 rather than a 403 for someone else's row. Those
  properties are invisible when you break them and trivial to break in a
  refactor — a dropped `WHERE user_id = :me` leaves every endpoint working
  perfectly for the person testing by hand. Note the scoping tests seed a
  *second* user with real data: against an empty database, a 404 passes for the
  wrong reason.

- **Fixtures build data through the ORM, not through the API.** Creating a user
  by POSTing to `/auth/register` would make almost every test depend on
  registration working, so one bug there would fail the whole suite at once and
  say nothing about where it was. Arrange directly, act through the API.

- **The test database is never the development one.** `conftest.py` calls
  `drop_all`, so it refuses to start if the two names match. That guard is three
  lines and the failure it prevents is unrecoverable and noticed afterwards.

- **bcrypt is cached across fixtures, not weakened.** Hashing the same two
  constant passwords forty times costs 50 seconds of a 60-second run, entirely
  to re-derive identical digests. The hash is memoized per run; the work factor
  is untouched and `/auth/login` still runs a real `verify_password` on every
  test that logs in. A suite people stop running has no value, and this one went
  from 61s to 10s without giving up a single assertion.

- **What this milestone deliberately does *not* do: chase coverage.** There is no
  test per CSV date format, no parametrized sweep of every validation rule. The
  brief was enough to refactor safely, and a suite that costs more to maintain
  than the code it guards is one that gets deleted in a hurry six months from now.

## Docker

```
docker-compose.yml
├── db        postgres:16-alpine   named volume, healthcheck, 127.0.0.1:5432
├── init-db   runs create_all once, exits 0
└── api       uvicorn, 127.0.0.1:8000, starts only after both of the above
```

```
docker compose up --build     start everything
docker compose logs -f api    follow the API's output
docker compose down           stop, keep the data
docker compose down -v        stop, delete the data
```

The final image is ~300 MB and runs as a non-root user.

### The decisions worth defending

- **Service names are hostnames.** The API connects to `db:5432`, not
  `localhost:5432` — inside the API container, `localhost` *is* the API
  container. This is the single most common way a working compose file gets
  written wrong, because the value is correct everywhere except the one place
  it's used.

- **`depends_on` alone means almost nothing; the healthcheck is what makes it
  mean something.** Without one, `depends_on` waits only for the container to
  have *started*, and Postgres takes a second or two after that before it
  accepts connections — so the API reliably loses the race on a cold start.
  `pg_isready` asks the question actually being asked.

- **Schema creation is a one-shot service, not something the app does at boot.**
  `app/db/init_db.py` already argues why: if the web app ran `create_all` on
  startup, every worker in a scaled deployment would run it simultaneously
  against the same database and race the others into a half-created schema. One
  container, running once, has nobody to race. The API then waits on
  `service_completed_successfully`, so a failed schema step stops the API from
  starting at all rather than surfacing as a crash loop three services away.

- **A named volume, not a bind mount, for the database.** Without a volume,
  `docker compose down` deletes the database along with the container's writable
  layer and every restart begins from an empty schema. Named rather than a host
  path because Postgres wants specific ownership and permissions on its data
  directory, which a bind mount from a Windows or macOS filesystem can't
  reliably provide.

- **Two build stages.** Dependencies are installed into a virtualenv in a
  `builder` stage; the runtime stage copies just that venv into a clean base. The
  pip cache and any build tooling stay in a layer that never ships. The
  virtualenv looks redundant inside an already-isolated container and earns its
  place by putting every installed package under one directory that the next
  stage can take in a single `COPY`.

- **`requirements.txt` is copied before the application code.** Docker caches
  layers until one of their inputs changes, so this is what decides whether
  editing a router re-runs `pip install`. The tempting `COPY . .` first would
  invalidate the install on every source edit.

- **`.dockerignore` is a security file as much as a speed one.** It keeps
  `.venv/` (hundreds of megabytes) out of the build context, and it keeps `.env`
  and `.git/` out of the image — a secret baked into a layer survives a later
  layer deleting it, and travels wherever the image is pushed.

- **`--host 0.0.0.0` in the CMD.** Uvicorn defaults to `127.0.0.1`, which inside
  a container is a loopback nothing outside can reach: the container starts, the
  logs look perfect, and every request from the host is refused.

- **The image is the production artifact; compose layers development on top.**
  No `--reload` in the `CMD` and the code is baked in. The compose file overrides
  the command and bind-mounts `./app` read-only for hot reload. An image that
  only works when a volume is mounted over it is not a deployable image.

- **Ports are published on `127.0.0.1`, not `0.0.0.0`.** A development database
  with a placeholder password has no business listening on the machine's LAN
  address.

- **Non-root, and a pinned base image.** Root in the container is root on the
  host kernel. `python:3.12-slim` and `postgres:16-alpine` are pinned because a
  floating tag means the image silently changes between two builds of the same
  commit — and a new Postgres *major* wouldn't read the existing volume at all,
  turning `compose pull` into a restart loop.

## Frontend

```
frontend/src/
├── api/          client.ts (fetch + errors) · types.ts (the contract) · queries.ts (hooks)
├── auth/         AuthContext.tsx · ProtectedRoute.tsx
├── components/   ui/ · layout/ · dashboard/ · transactions/ · filters/
├── hooks/        useFilters (URL state) · useCountUp · useDebouncedValue · useTheme
├── lib/          format.ts (money, dates) · palette.ts (chart colours from CSS)
├── pages/        Login · Signup · Dashboard · Transactions · Onboarding
└── styles/       tokens.css (every colour, space, duration) · global.css
```

### The decisions worth defending

- **Money crosses the wire as a `string`, and the types say so.** The API sends
  `"1500.00"` because a JSON number is an IEEE double, and a total a double
  can't hold exactly arrives differing from the database in its last decimal —
  on precisely the figures a user checks against their bank. Typing them as
  `string` means you cannot add two amounts with `+` and get `"45.20100.00"`
  past the compiler without noticing. `lib/format.ts` is the only place that
  parses.

- **Auth state has three values, not a boolean.** `loading | authenticated |
  anonymous`, because on first paint the app genuinely does not know: a token
  exists in storage but has not been verified. Collapsing that into
  `isAuthenticated: false` makes the app flash the login page on every refresh
  before redirecting back. That flash is the most common bug in hand-rolled SPA
  auth, and it is a modelling error rather than a timing one.

- **Route guards are layout routes, not per-page wrappers.** A new `<Route>`
  inside the `RequireAuth` block is protected by virtue of being there. The
  dangerous mistake — adding a page and forgetting to protect it — becomes
  structurally hard rather than something to remember.

- **The token is in `localStorage`, with eyes open.** It is readable by any
  script on the origin, so a successful XSS steals the session. The alternative
  — an httpOnly cookie — is immune to that but needs CSRF protection and a
  backend that sets cookies, which this one doesn't (it issues bearer tokens).
  What makes it defensible rather than merely convenient: the token lives 30
  minutes and there is no refresh token to steal alongside it. The real fix is a
  refresh-token cookie, and that's a backend milestone.

- **Optimistic updates cancel in-flight queries first.** `onMutate` calls
  `cancelQueries` before snapshotting, because a refetch already in flight when
  the user hits save lands *after* the optimistic update and overwrites it with
  data that predates the change. The row flickers back to its old value — which
  looks exactly like the save having failed. Every mutation snapshots, applies,
  and rolls back to the exact previous cache on error.

- **Optimistic rows get negative ids.** Server ids are positive, so a temporary
  id can never collide. That matters because React keys rows by id, and a
  temporary id matching a real one makes the reconciler reuse the wrong DOM node
  when the real row arrives. Rows with a negative id have their edit and delete
  buttons disabled — there is nothing on the server to address yet.

- **Writes invalidate the summaries too.** Adding a transaction changes the
  monthly chart, the donut and the stat cards, none of which the mutation
  touched. Forgetting that is how a dashboard ends up showing a table containing
  a new row above charts that don't.

- **Filter state lives in the URL.** A filtered view is something people
  bookmark and share; in component state, a refresh silently resets it and the
  back button leaves the page instead of undoing a filter. It also removes a
  whole class of bug — the table and the charts cannot disagree about what is
  being shown, because they read the same parameters rather than each keeping a
  copy.

- **The search box is debounced, the input is not.** Typing "groceries" would
  otherwise fire nine requests, eight obsolete before they land, with results
  flickering through prefixes. The *input* stays bound to raw state so typing
  feels instant; only the query waits for a pause. Debouncing the input itself
  is the classic mistake — it makes typing feel broken.

- **Period and account filter everything; category, type and search filter only
  the table.** The aggregation endpoints take a date range and an account, and a
  category breakdown narrowed to one category has nothing to say. The filter bar
  states this in a line of text rather than leaving the user to notice, because
  a filter that silently applies to half the screen is worse than one that says
  which half.

- **`placeholderData` keeps the previous page rendered while the next loads.**
  Changing a filter dims the table instead of collapsing it to skeletons and
  back. Skeletons appear only on a genuine first load — `isLoading`, not
  `isFetching`. This single distinction is most of what makes the app feel fast
  rather than merely be fast.

- **The count-up animates from the previous value, not from zero.** When a
  filter changes and the total goes from $2,400 to $2,650, counting from zero
  implies the data was replaced; counting from the old figure shows the *change*,
  which is the information the animation exists to convey. It also checks
  `prefers-reduced-motion` in JS — a CSS media query cannot stop a
  `requestAnimationFrame` loop, and for someone with a vestibular disorder,
  numbers spinning on every filter change is a symptom trigger rather than
  decoration.

- **One token file, and dark is the designed theme.** Semantic names
  (`--surface-raised`, not `--grey-800`) because the literal name has to be
  renamed the moment the value changes. Light mode redefines every token rather
  than inverting, because a dark palette flipped mechanically produces muddy
  greys and text that fails contrast. The theme is applied before first render,
  not in an effect, or the page renders dark and then flips.

- **Chart colours are read from the CSS custom properties.** Recharts takes
  colours as props, so it cannot use `var(--chart-1)` on an SVG fill in a way
  that survives a theme switch. Reading the computed values keeps one palette
  serving both — the alternative is a second copy of eight hex codes that has to
  be kept in step by hand, and won't be.

- **A category's colour is keyed on its id, not its index.** The donut is sorted
  largest-first, so a category's *position* changes whenever spending does. A
  legend whose colours reshuffle month to month is actively misleading.

- **Empty states distinguish three situations.** "You have nothing yet",
  "your filters excluded everything" and "something failed" need different
  sentences and different next actions. Rendering nothing — or the words "No
  data" — leaves the user unable to tell which they are looking at.

- **The table becomes cards below 720px, as a different component tree.** A
  table squeezed into 360px is either scrolled sideways or has columns too
  narrow to read. Rendering both and hiding one with CSS would put every row in
  the DOM twice, so `useMediaQuery` picks one.

- **A new user sees onboarding, not an empty dashboard.** Every newly registered
  user owns no accounts, and `POST /transactions` requires one — so the default
  first screen would be four zeroes above three empty charts and a table that
  cannot accept a row. Technically accurate, and the exact moment someone
  decides an app is broken.

- **What this milestone deliberately does *not* do.** No component tests — the
  backend suite covers the contract, and testing React well is a project of its
  own. No i18n, no virtualised table (the API caps a page at 200 rows), no
  refresh-token flow. Each is a real feature; none is a gap this app has felt.

### What the frontend needed from the backend

Three things had to be added before a browser could talk to this API at all, and
they're worth naming because they were invisible while `curl` was the only
client:

- **CORS.** Vite serves on `:5173` and the API on `:8000`, which are different
  origins to a browser. Without the middleware, every request is blocked before
  it is sent and the failure looks like a network error rather than a policy.
- **`GET`/`POST /accounts` and `/categories`.** The README used to tell you to
  seed these from a Python shell. A browser cannot do that, and a user who has
  just signed up owns no account — so "create an account" went from a
  convenience to the first thing the app must let you do.
- **`?q=` on `GET /transactions`.** Searching client-side would only search the
  page already loaded, which is a search box that lies.

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
