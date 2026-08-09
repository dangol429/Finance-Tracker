# Personal Finance Tracker — Backend

FastAPI + PostgreSQL + SQLAlchemy backend for a personal finance tracker.
Docker deployment comes in a later milestone; **this is milestone 3 (JWT
authentication): bcrypt-hashed passwords, a login endpoint that issues signed
access tokens, and a dependency that turns a token back into a `User`.**

Milestone 2 built the four core tables and the foreign keys between them; this
one adds the identity layer that every later ownership check (`WHERE user_id =
:me`) depends on.

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

# Use the token
curl http://127.0.0.1:8000/auth/me -H "Authorization: Bearer <paste-token>"
```

## Study guide

[`docs/Finance-Tracker-Study-Guide.pdf`](docs/Finance-Tracker-Study-Guide.pdf) — a
16-page walkthrough of the reasoning behind each decision, including the
generated SQL, the gotchas, and interview Q&A. **Currently covers milestones 1–2
(setup and the data model); the auth material above hasn't been folded in yet.** Its
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
│   └── auth.py      # /auth/register, /auth/login, /auth/me
└── schemas/         # Pydantic request/response shapes  (API contract)
    ├── user.py      # UserCreate (in) / UserRead (out) — the hash never appears
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
  area (`health`, `auth`; `transactions` and `accounts` later). `main.py` just
  calls `include_router()` on each. This is what keeps `main.py` thin and lets
  the API grow by *adding a file* rather than by growing one giant file. Note
  that `main.py` says nothing about which routes are protected — that lives in
  each handler's signature, so a new route can't be left unguarded by an
  omission in the wiring.

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
