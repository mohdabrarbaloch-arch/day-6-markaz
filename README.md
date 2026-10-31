# Markaz

**An enterprise AI control plane that tells you "no" before you deploy.**

Most AI infrastructure dashboards show you what is running. Markaz answers the question you actually have at 11pm: *will this fit, in this region, at this cost — and what will break if I press the button?*

I built it after watching a team discover, three weeks into a regional launch, that their model was contractually not allowed to process data in that country. Nobody had written that rule down anywhere a computer could check it. So I made the rule checkable.

---

## The three things this does that a spreadsheet doesn't

**1. Admission control with arithmetic, not vibes.**
A 70B model needs 92GB. An 80GB card therefore needs *two* cards — and with a 25% safety margin, sometimes three. Markaz computes that per replica, multiplies by replicas, and tells you whether the region has the headroom *before* you commit. It also tells you what you will have left when you're done, because "fits" and "still has room to grow" are different facts.

**2. Residency and sovereignty as enforceable rules.**
Each region carries a set of residency tags (`eu`, `gdpr`, `us`, `sovereign`, `apac`). Each model declares what it requires. A model requiring `sovereign` cannot be deployed to a region without it — the request is refused with the reason spelled out, in the API and in the UI. This is the rule that gets forgotten until an auditor asks.

**3. Drift as a first-class number.**
Every deployment stores what it *should* look like and what the cluster last *reported*. The gap between those two is where on-call work comes from, so Markaz computes it explicitly: `in_sync`, `scaled_out`, `scaled_in`, `missing`, or `unreported`. Note that last one — a deployment that has never reported is *not* silently counted as healthy.

Plus a pipeline DAG validator that finds cycles and computes the critical path (the longest branch, not the sum of everything), because total work and wall-clock time are not the same number and confusing them is how a "20-minute" job takes 90.

---

## Screenshots

| | |
|---|---|
| **Regions** — live capacity per region, derived from your actual deployments | ![Regions](screenshots/02-regions.png) |
| **Plan rejected** — every reason named, with the numbers that produced it | ![Rejected plan](screenshots/04-plan-rejected.png) |
| **Drift** — desired vs observed, critical findings first | ![Drift](screenshots/06-drift.png) |
| **Mobile** — 390px, no horizontal scroll | ![Mobile](screenshots/07-mobile-regions.png) |

---

## Live demo

**Not deployed.** This sandbox has no Vercel token and no `GITHUB_TOKEN`, so there is no live URL rather than a dead one. `vercel.json` and the serverless entrypoint are in the repo and the deploy steps are in [docs/setup.md](docs/setup.md#deploying). If you want it live in five minutes, `docker compose up` is the fastest honest path.

---

## Run it

```bash
git clone https://github.com/mohdabrarbaloch-arch/day-6-markaz.git
cd day-6-markaz

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# generate a real key — the app generates one for you if you skip this, but then
# every restart invalidates existing sessions
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))" >> .env

uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>, register, and you're in. API docs at `/api/docs`.

With Docker:

```bash
docker compose up --build
```

Postgres is wired up in `docker-compose.yml`. Locally it falls back to SQLite with WAL enabled, so you can ignore the database entirely on day one.

---

## Features

- **Admission control** — GPU arithmetic with a 25% VRAM safety margin, per-replica and multiplied by replicas
- **Residency enforcement** — a model that requires `sovereign` is refused in a region that doesn't offer it
- **Headroom warnings** — accepted, but flagged when a deployment eats more than 80% of remaining capacity
- **Cost forecasting** — hourly, daily, and monthly at 730 hours; budget overage computed, not estimated
- **Drift detection** — five states, with `unreported` never masquerading as `in_sync`
- **Pipeline DAG validation** — cycle detection with the offending nodes named, deterministic run order, critical path
- **Region capacity** — committed GPUs summed from real rows, never seeded
- **Auth** — bcrypt at cost 12, JWT with expiry, uniform login timing so response time doesn't leak whether an account exists
- **Security** — CSP, `X-Frame-Options: DENY`, nosniff, per-client sliding-window rate limiting, request IDs on every response
- **422 vs 409, used deliberately** — a malformed request is 422; a well-formed request the world refuses is 409

---

## The API in one screen

```
POST   /api/auth/register          → 201 + JWT
POST   /api/auth/login             → 200 + JWT
GET    /api/auth/me                → 200

GET    /api/regions                → capacity per region, live
GET    /api/regions/catalog        → regions, SKUs, models
GET    /api/regions/{code}/models  → what may run there, and what may not

POST   /api/deployments/plan       → 200, verdict + cost, writes nothing
POST   /api/deployments            → 201, or 409 with reasons
GET    /api/deployments            → list
PATCH  /api/deployments/{id}/scale → 200, or 409 if the region can't absorb it
POST   /api/deployments/{id}/observe → record what the cluster actually shows
GET    /api/deployments/drift/report → desired vs observed
DELETE /api/deployments/{id}       → 204

POST   /api/pipelines              → 201, or 422 with cycle nodes named
GET    /api/pipelines/{id}         → includes run order and critical path
```

`/plan` writes nothing on purpose. You can call it in a loop from the UI as someone drags a slider. Between a plan and an apply, another request can take the headroom — so `POST /api/deployments` re-runs the whole evaluation inside the same transaction rather than trusting the plan the client is echoing back.

---

## Tests

```bash
pytest        # 76 tests
ruff check .  # clean
black --check .
```

76 tests, most of them on the engine — because every number the product shows is computed there, so that's where a regression would be silent. Several assertions exist specifically to stop a future "improvement": `test_scaled_in_is_only_a_warning` fails if someone makes a shrinking deployment page an on-call engineer, and `test_unreported_does_not_claim_a_sync` fails if `unreported` ever gets folded into the healthy bucket.

---

## Stack

Python 3.11 · FastAPI · SQLAlchemy 2.0 · Pydantic v2 · SQLite (WAL) or Postgres · vanilla JS SPA, no build step · bcrypt · Docker

The frontend is deliberately buildless. A control plane's UI is a form and a table; adding a bundler to that buys nothing and costs a `node_modules`.

---

## Project layout

```
app/
  engine/          pure functions, no I/O — all the arithmetic
    catalog.py     regions, SKUs, models
    placement.py   admission control
    dag.py         cycle detection, critical path
    drift.py       desired vs observed
    costing.py     cost forecast
  routers/         auth, regions, deployments, pipelines, health
  static/          the SPA
tests/             76 tests
docs/              setup, usage, api reference
```

---

## License

MIT — see [LICENSE](LICENSE).

Built as Day 6 of a 30-day autonomous build challenge. Every number in the architecture doc came from running the code, not from estimating it.
