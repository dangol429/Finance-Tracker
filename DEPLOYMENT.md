# Deployment

Frontend on **Vercel**, backend and PostgreSQL on **Railway**. Both have free
tiers that fit this project.

The two halves are deployed separately and know about each other through exactly
two environment variables — `VITE_API_URL` on the frontend and `CORS_ORIGINS` on
the backend. Getting those two wrong in opposite directions is the cause of
almost every "it works locally" deployment failure, so they have a section of
their own at the bottom.

---

## 1. Backend + database (Railway)

Railway builds the repository's `Dockerfile`, which is the same image
`docker compose` runs locally. No separate production build to keep in step.

1. **New Project → Deploy from GitHub repo**, and pick this repository.
2. **Add a PostgreSQL service** to the same project (*New → Database →
   PostgreSQL*).
3. On the **app** service, set the root directory to `/` and confirm Railway
   detected the `Dockerfile`.
4. Set these variables on the app service:

   | Variable | Value | Notes |
   |---|---|---|
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | Railway's reference syntax — it fills in the real URL |
   | `SECRET_KEY` | *(generate one, see below)* | The app refuses to boot without a real one outside development |
   | `ENVIRONMENT` | `production` | Turns on the secret-key guard |
   | `DEBUG` | `false` | `true` logs every SQL statement, parameters included |
   | `CORS_ORIGINS` | `https://your-app.vercel.app` | Set after step 2; comma-separated for several |

   Generate the signing key with:

   ```bash
   python -c "import secrets; print(secrets.token_hex(32))"
   ```

   `ENVIRONMENT=production` makes `app/core/config.py` refuse to start if
   `SECRET_KEY` is still the repository's placeholder. That is deliberate: a
   deployment running the committed dev key would accept tokens forged by anyone
   who read this repo, and it would look completely healthy while doing it.

5. **Create the tables.** The schema is not created automatically — the app does
   not run `create_all` at startup, because every worker in a scaled deployment
   would race the others into a half-built schema. Run it once, from Railway's
   shell on the app service:

   ```bash
   python -m app.db.init_db
   ```

   > `create_all` only issues `CREATE TABLE` for tables that do not exist. It
   > will not *alter* an existing table to match a changed model — the point at
   > which Alembic migrations become necessary.

6. Note the public URL Railway assigns (*Settings → Networking → Generate
   Domain*). Check it:

   ```bash
   curl https://your-api.up.railway.app/health/db
   # {"status":"ok","database":"connected"}
   ```

### A note on `$PORT`

Railway assigns a port at runtime and passes it as `$PORT`. The `Dockerfile`'s
`CMD` reads it (`--port "${PORT:-8000}"`) and falls back to 8000 locally, so the
same image works in both places. This is why that line uses the shell form of
`CMD` with `exec` rather than the usual exec form — see the comment above it.

---

## 2. Frontend (Vercel)

1. **Add New → Project**, import this repository.
2. Set **Root Directory** to `frontend`. This is the step that is easy to miss
   in a monorepo, and skipping it makes Vercel try to build the Python backend.
3. Framework preset: **Vite** (Vercel usually detects it). `frontend/vercel.json`
   already declares the build command, output directory and — importantly — the
   SPA rewrite.
4. Set one environment variable:

   | Variable | Value |
   |---|---|
   | `VITE_API_URL` | `https://your-api.up.railway.app` |

   No trailing slash. Only `VITE_`-prefixed variables reach the browser, and
   **everything that does is compiled into the bundle in plain text** — so this
   is the right place for a public API URL and the wrong place for any secret.

5. Deploy, then go back to Railway and set `CORS_ORIGINS` to the Vercel URL.

### Why `vercel.json` has a rewrite

```json
"rewrites": [{ "source": "/(.*)", "destination": "/index.html" }]
```

Without it the app works until someone refreshes the page on `/transactions`.
This is a single-page app: the router is JavaScript, and only `/` exists as a
file. A hard request for any other path asks the CDN for a file that is not
there, and it answers 404. The rewrite serves `index.html` for every path and
lets the router take over. Shared links and the back button both depend on it.

---

## 3. The two variables that break everything

These point at each other, and the failure modes look nothing like the cause.

```
  Vercel                                   Railway
  ┌────────────────────────┐               ┌────────────────────────┐
  │ VITE_API_URL ──────────┼──── calls ───▶│ the API                │
  │                        │◀── allows ────┼── CORS_ORIGINS         │
  └────────────────────────┘               └────────────────────────┘
```

| Symptom | Cause |
|---|---|
| Every request fails, console says *blocked by CORS policy* | `CORS_ORIGINS` on Railway does not exactly match the Vercel origin. It must include the scheme and no trailing slash: `https://app.vercel.app` |
| Requests go to `localhost:8000` in production | `VITE_API_URL` was not set at **build** time. Vite inlines it during the build, so changing it needs a redeploy, not a restart |
| Works on the main domain, fails on preview deploys | Each Vercel preview gets its own subdomain. Add them to `CORS_ORIGINS` too, or accept that previews cannot reach the API |
| 404 on refresh, fine when navigating | The SPA rewrite is missing — see above |

`CORS_ORIGINS` accepts a comma-separated list, so several origins is:

```
CORS_ORIGINS=https://finance.vercel.app,https://finance-git-main-you.vercel.app
```

Never `*`. The wildcard cannot be combined with credentialed requests, and it
means any page on the internet can call the API with a user's session — which is
the entire attack CORS exists to prevent.

---

## Checklist

- [ ] `SECRET_KEY` is a generated value, not the repo placeholder
- [ ] `ENVIRONMENT=production` and `DEBUG=false` on Railway
- [ ] `python -m app.db.init_db` has been run once
- [ ] `GET /health/db` returns `"database":"connected"`
- [ ] `VITE_API_URL` set on Vercel, no trailing slash
- [ ] `CORS_ORIGINS` on Railway matches the Vercel origin exactly
- [ ] Refreshing `/transactions` on the deployed site does not 404
- [ ] Signing up on the live site creates an account and lands on onboarding
