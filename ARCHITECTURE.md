# Markaz — Architecture

**مرکز** (markaz) means *centre* — the place things are gathered. This is the control plane
for an enterprise AI platform: a place to declare what should be running, in which region, at
what cost, and to find out — before you press deploy — whether it is actually possible.

The name matters less than the one design decision everything else hangs off:

> **Every action in Markaz is a claim about the future that the system can check right now.**

A deployment plan is not a form submission. It is a set of resource claims (GPU hours, VRAM,
replicas, budget) that either fit inside the region's remaining capacity or do not. Markaz
evaluates those claims against real arithmetic and refuses the ones that break, with a number
attached to the refusal. Nothing is allowed to "look fine" and fail at 3 a.m.

---

## 1. The problem this solves

Enterprise AI teams routinely hit four failures that are all the same failure wearing
different hats — **a decision was made without the arithmetic**:

| Failure | What it looks like | Why dashboards don't catch it |
|---|---|---|
| **Overcommit** | 12 models scheduled onto a region with 8 GPU-equivalents of headroom | The dashboard shows *current* utilisation, not *committed* utilisation |
| **Budget surprise** | A pipeline that costs $4,100/month deployed against a $1,000 cap | Cost is reported monthly, after the fact |
| **Region mismatch** | A model legal in `eu-west` deployed to `ap-south`, where the data-residency tag forbids it | Compliance lives in a spreadsheet |
| **Drift** | Someone scaled a replica by hand at 2 a.m.; the declared state says otherwise | Nobody diffs declared state against actual state |

Markaz makes all four **checkable at declaration time**, and keeps checking them afterwards.

---

## 2. System diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│  BROWSER — single-page app, vanilla JS, no build step                    │
│                                                                          │
│   ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐           │
│   │  Regions   │ │ Deployments│ │ Pipelines  │ │   Drift    │           │
│   │  capacity  │ │  + plan    │ │  DAG +     │ │  report    │           │
│   │  math      │ │  verdict   │ │  critical  │ │  table     │           │
│   └────────────┘ └────────────┘ │  path      │ └────────────┘           │
│                                 └────────────┘                          │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │  fetch() + JWT in Authorization header
                                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  FASTAPI                                                                 │
│                                                                          │
│   middleware:  request-id → security headers → CORS → rate limit         │
│                                                                          │
│   routers/                                                               │
│     auth.py         register / login / me                                │
│     regions.py      list capacity, read-only (catalog-derived)           │
│     deployments.py  plan (dry-run) → create → list → scale → delete      │
│     pipelines.py    validate DAG → create → run-order → critical path    │
│     health.py       liveness + honest backend report                     │
│                                                                          │
│   ┌───────────────────── ENGINE (pure functions, no I/O) ──────────────┐  │
│   │  catalog.py    regions, SKUs, model registry (static reference)   │  │
│   │  placement.py  admission control — does this fit? by how much?    │  │
│   │  dag.py        cycle detection, topo order, critical path         │  │
│   │  drift.py      desired vs actual divergence                       │  │
│   │  costing.py    hourly → monthly forecast, budget verdict          │  │
│   └───────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│   models.py / schemas.py   SQLAlchemy ORM + Pydantic validation          │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
            ┌──────────────┐        ┌──────────────┐
            │ SQLite (dev) │        │ PostgreSQL   │
            │ WAL          │        │ (compose)    │
            └──────────────┘        └──────────────┘
```

**The engine is deliberately pure.** `placement`, `dag`, `drift` and `costing` take plain
dataclasses and return plain dataclasses. No database handle, no network, no clock. That is
why the whole admission-control layer is testable in milliseconds and why the API layer can
do a dry-run without writing anything.

---

## 3. Data flow — the plan/apply split

The single most important flow, because it is where Markaz differs from a CRUD form:

```
POST /api/deployments/plan          ← dry run, writes nothing
        │
        │  body: { model, region, replicas, sku, monthly_budget_usd }
        ▼
   ┌────────────────────────────────────────────┐
   │ 1. resolve region  → capacity snapshot      │
   │ 2. resolve model   → vram, residency rules  │
   │ 3. resolve sku     → vram_gb, $/hr, region  │
   │ 4. compute committed load (existing deploys)│
   │ 5. placement.evaluate(...)                  │
   └────────────────────────────────────────────┘
        │
        ├── REJECTED → 200 with verdict="rejected", reasons[], each with a number
        │
        └── ACCEPTED → 200 with verdict="accepted",
                        headroom_after, monthly_cost, warnings[]
                              │
                              ▼
                POST /api/deployments          ← the actual write
                        │
                        └── re-runs the SAME evaluation
                            inside one transaction, so a plan that
                            was valid 5 minutes ago cannot be applied
                            if someone else filled the region meanwhile
```

**Why plan is a POST that writes nothing:** the plan body is a candidate resource claim, and
claims are validated the same way whether they're saved or not. Making `plan` a `GET` with
query params would have forced the same logic into two shapes.

**Why the write re-evaluates instead of trusting the plan:** a dry-run that returns `accepted`
is a statement about the state at *that moment*. Between plan and apply, another user can
consume the headroom. Re-binding the check to the write is the difference between a
demonstration and a system.

---

## 4. The engine, module by module

### `catalog.py` — reference data

Six regions, five GPU SKUs, eight models. Static, versioned in code, immutable at runtime.

Regions carry `gpu_capacity` (GPU-equivalents), `data_residency` (a list of tags), and
`$/kwh` energy cost. SKUs carry `vram_gb` and `hourly_usd`. Models carry `min_vram_gb` and
`allowed_residency` tags.

> **Why static and not a database table?** A control plane's catalog is configuration, not
> data. If it's editable through the API, "which regions exist" becomes a mutable fact that
> deployments silently depend on. Keeping it in code makes it reviewable in a diff — the same
> reason infrastructure teams keep region lists in Terraform rather than in a UI.

### `placement.py` — admission control

The core question: *given what's already committed in this region, does this deployment fit?*

```
required_gpus  = ceil(replicas × vram_gb_required / sku.vram_gb)
committed      = Σ existing.required_gpus  (same region, same sku class)
headroom       = region.gpu_capacity − committed
fits           = required_gpus ≤ headroom
```

Three independent rejections, each emitted with its number:

1. **Capacity** — `need 6, have 2` — inclusive of a 1.25 safety multiplier on VRAM so a
   deployment that *exactly* fills a GPU is rejected rather than accepted-then-OOM.
2. **Residency** — every tag in `model.allowed_residency` must be present in
   `region.data_residency`. Set-shaped, not string-matched, so adding a region tag never
   requires touching model definitions.
3. **SKU availability** — the SKU must be offered in that region at all.

Non-fatal findings become `warnings`, not rejections: a deployment that consumes >80% of
remaining headroom warns, because the *next* deployment will fail and the operator should know
that before they're surprised.

**The safety multiplier is a deliberate asymmetry.** Over-rejecting costs a conversation;
under-rejecting costs an outage. `1.25` is a judgement call, stated in code, not hidden.

### `dag.py` — pipeline validation

`compute` is a DAG, so the two things that can be wrong are structural:

- **Cycles** — found with Kahn's algorithm. When it fails, the residual node set is exactly
  the cycle participants, which is far more useful than "cycle detected".
- **Topological order** — the order Kahn's pops nodes. Deterministic given sorted input, so
  the same pipeline always shows the same run order.

Plus **critical path**: longest weighted path through the DAG by declared step duration.
That converts "how long will this pipeline take" from a guess into a longest-path computation.

Edges referencing unknown nodes are rejected explicitly rather than silently dropped.

### `drift.py` — declared vs actual

Deployments store both `desired_replicas` and `observed_replicas`. Divergence is classified:

| State | Condition |
|---|---|
| `in_sync` | desired == observed |
| `scaled_out` | observed > desired — someone bypassed the control plane |
| `scaled_in` | observed < desired — a node died, or a scale-down wasn't recorded |
| `missing` | observed == 0 while desired > 0 — the deployment is not running |

Reported with a `severity` so the UI can sort the real problems to the top. `scaled_out` is
*more* alarming than `scaled_in` in a cost-controlled environment, which is a domain opinion
encoded as a rule.

### `costing.py` — forecast

```
hourly   = replicas × sku.hourly_usd
monthly  = hourly × 730          ← average month, stated explicitly
```

730 is the arithmetic mean of 28–31-day months, not 30. Using 30 flatters forecasts by ~1.5%.

Budget comparison against an optional `monthly_budget_usd` returns `within_budget` plus the
overage. The UI shows the monthly figure before the user commits, so the surprise happens in
a form field rather than an invoice.

---

## 5. Tech stack

| Layer | Choice | Why |
|---|---|---|
| API | **FastAPI 0.115** | Type-driven validation; `response_model` makes the schema the contract |
| ORM | **SQLAlchemy 2.0** | Typed `Mapped[]` columns; works on SQLite and Postgres unchanged |
| Validation | **Pydantic v2** | Two-layer: field constraints *and* cross-field rules |
| Auth | **JWT (HS256) + bcrypt** | Stateless; no session store; bcrypt cost 12 |
| DB | **SQLite WAL (dev) / Postgres (prod)** | Same models, one env var to switch |
| Frontend | **Vanilla JS SPA** | No build step — the whole app is three static files |
| Tests | **pytest + httpx** | In-process ASGI client, no port binding |

**On the vanilla frontend:** a control plane's UI is four tables and a form. A framework
would add a build step, a lockfile, and a dependency tree in exchange for nothing this app
needs. The SPA is ~900 lines of readable JS with an explicit render function per view.

---

## 6. Security

| Control | Implementation |
|---|---|
| Password storage | bcrypt, cost factor 12, per-user salt (passlib) |
| Tokens | JWT HS256, 24h expiry, `sub` = user id |
| Secret handling | `SECRET_KEY` from env; random per-process if absent, with a loud warning |
| Rate limiting | Sliding window per IP on auth routes (30/min) and writes (120/min) |
| Input validation | Pydantic field bounds + explicit cross-field validators |
| Ownership | Every query filtered by `owner_id`; foreign resources return **404, not 403** |
| Headers | `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, CSP |
| CORS | Explicit origin list from env; wildcard is never the default |
| SQL injection | Not possible — ORM parameterises everything; no string-built SQL exists |
| Request tracing | `X-Request-ID` generated per request, echoed in the response |

**404 over 403 for foreign resources is deliberate.** A 403 confirms the resource exists,
which is an existence oracle. A 404 leaks nothing.

**What is *not* claimed:** this is a single-node demo control plane. It does not implement
real GPU scheduling, it does not talk to a Kubernetes API, and `observed_replicas` is
declared by the client rather than polled from a cluster. Pretending otherwise in the README
would be the dishonest thing to do, so the README says it plainly too.

---

## 7. Scaling notes

**Where this design genuinely scales:**

- **Stateless API** → horizontal replicas behind a load balancer; JWT means no shared session store.
- **Engine is pure** → no locks, no shared mutable state, trivially parallel across requests.
- **Admission control is a single read + arithmetic** → at real scale this becomes a
  `SELECT ... FOR UPDATE` on a region-capacity row, which serialises only the write path.

**Where it would break first, honestly:**

1. **The capacity read-then-write is not atomic.** Two concurrent deploys can both read
   `headroom = 6` and both be accepted. The fix is a row lock (`with_for_update()`) on a
   region table, or an advisory lock keyed on region id. This is the single most important
   gap, and it is documented here rather than glossed over.
2. **Rate limiting is in-process.** Multiple replicas each get their own window. Real
   deployment moves this to Redis.
3. **Cost is modelled as linear in replicas.** Real GPU pricing has committed-use discounts
   and egress charges that are not linear. The model is honest about being a forecast.
4. **No audit log.** Every state change should append to an append-only table. Not
   implemented; listed because an enterprise control plane without one is not shippable.

**Table growth:** `deployments` and `pipelines` grow with usage. Both are indexed on
`owner_id` and `created_at`, which is what every query filters on. At 10M rows the list
endpoint needs cursor pagination instead of offset — noted, not implemented.

---

## 8. What a day of this looks like

The numbers below are copied from an actual run of `placement.py` and `costing.py`, not
worked out by hand in a doc — which is how the first version of this section got its
arithmetic wrong and had to be corrected against the code.

Setup: `ap-south` has **12** GPU-equivalents, **6 already committed**. A 70B model needs
`92 × 1.25 = 115GB` of VRAM per replica, so on an 80GB A100 that is `ceil(115/80) = 2` GPUs
per replica, not one.

| Replicas | GPUs needed | Headroom before | Headroom after | Verdict | Monthly |
|---|---|---|---|---|---|
| **4** | 8 | 6 | **−2** | **rejected** — *"Needs 8 GPU-equivalents but only 6 remain"* | $23,360 |
| **3** | 6 | 6 | **0** | accepted, warned: 100% of headroom | $17,520 |
| **2** | 4 | 6 | **2** | accepted, warned: model spans 2 GPUs | $11,680 |

1. Operator opens **Regions** — `ap-south` shows **6 of 12** committed.
2. Fills the form: `llama-3-70b`, `ap-south`, **4** replicas, `a100-80`.
3. **Plan** → **rejected**, and the reason carries both numbers: `needed 8, available 6`.
   Monthly cost would have been **$23,360**.
4. Drops to **3** → accepted, but warns it consumes **100%** of remaining headroom, leaving
   **0**. The next deployment of this size fails. Cost **$17,520**.
5. Drops to **2** → accepted, `headroom_after = 2`, cost **$11,680**. Deploys.
6. Next morning **Drift** shows `scaled_out: desired 3, observed 5` — someone hand-scaled
   overnight, and the two extra replicas are unbudgeted spend nobody approved.

Note that step 4's warning is the entire value of the product: a naive capacity check
accepts 3 replicas happily, because it fits. Markaz tells you it fits *and* that it has just
consumed the region.
