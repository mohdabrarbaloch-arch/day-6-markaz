# API reference

Base URL: `/api`. Interactive docs at `/api/docs`, OpenAPI JSON at `/api/openapi.json`.

All authenticated endpoints expect `Authorization: Bearer <jwt>`. Every response carries `X-Request-ID`; pass your own in the request header and it's echoed back.

Rate limits: **60 mutating requests/minute** per client overall, **10/minute** on register and login. Exceeding either returns `429` with a `Retry-After` header.

---

## Status codes, and why each one

| Code | Means | Example |
|---|---|---|
| `200` | Fine | A plan that was evaluated |
| `201` | Created | Registration, deployment, pipeline |
| `204` | Done, no body | Delete |
| `401` | No or invalid token | Missing `Authorization` |
| `404` | Not found **or not yours** | Someone else's deployment |
| `409` | Well-formed request, world says no | Deploy rejected by admission control |
| `422` | Malformed body or invalid graph | Negative replicas, cycle in a DAG |
| `429` | Rate limited | 61st write in a minute |

**404 instead of 403 for other people's resources** is deliberate. A 403 confirms the ID exists, which is an existence oracle — you can enumerate IDs and learn which customers you share an instance with. There's a test that fails if this becomes 403.

**409 vs 422** for deployments: a request for 7 replicas when only 12 GPUs exist is syntactically perfect. The request is valid; reality refuses it. That's 409. A request for `-1` replicas is 422, because it's wrong before it reaches any policy.

---

## Auth

### `POST /api/auth/register` → `201`

```json
{
  "email": "you@example.com",
  "password": "at-least-8-chars-mixed",
  "display_name": "Your Name"
}
```

Password must be 8–128 chars and mix letters with digits or symbols. `aaaaaaaa` is rejected; `correct-horse-9` passes. `display_name` is trimmed.

Response:

```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 43200,
  "user": { "id": 1, "email": "you@example.com", "display_name": "Your Name", "created_at": "..." }
}
```

Errors: `409` email taken · `422` validation · `429` too many attempts.

### `POST /api/auth/login` → `200`

Same payload minus `display_name`. Response shape identical to register.

`401` on failure — with the **same message and the same timing** whether the email doesn't exist or the password is wrong. The handler verifies against a dummy bcrypt hash when the user is missing, so a failed login costs the same ~250ms either way. Without that, response time tells an attacker which emails are registered.

### `GET /api/auth/me` → `200`

Returns the current user. `401` if the token is missing, malformed, expired, or the account is disabled.

---

## Regions

### `GET /api/regions` → `200`

One entry per region, with capacity **derived from your own deployments** — never seeded.

```json
[
  {
    "code": "ap-south",
    "display_name": "Mumbai",
    "gpu_capacity": 12,
    "committed_gpus": 2,
    "headroom_gpus": 10,
    "utilisation": 0.1667,
    "data_residency": ["apac", "dpdp"],
    "skus": ["t4-16", "a10-24", "l4-24", "a100-80"],
    "active_deployments": 1,
    "monthly_cost_usd": 1606.0
  }
]
```

### `GET /api/regions/catalog` → `200`

The static reference data: every region, every SKU, every model, with their constraints. Useful for building a deploy form.

### `GET /api/regions/{code}/models` → `200`

```json
{
  "region": "us-east",
  "display_name": "N. Virginia",
  "models": [ { "code": "llama-3-8b", "min_vram_gb": 16, "required_residency": [] }, ... ],
  "excluded": ["med-llm-sovereign"]
}
```

`excluded` is the list that matters — it's the models this region is **not allowed** to run. `404` for an unknown region.

---

## Deployments

### `POST /api/deployments/plan` → `200`

Evaluate a candidate. **Writes nothing.** Safe to call on every keystroke.

```json
{
  "name": "embedder",
  "model_code": "llama-3-8b",
  "region_code": "ap-south",
  "sku_code": "l4-24",
  "replicas": 2,
  "monthly_budget_usd": 2000
}
```

Always `200` when the body parses — a rejection is a **verdict**, not an error:

```json
{
  "verdict": {
    "accepted": false,
    "region_code": "ap-south",
    "model_code": "llama-3-8b",
    "sku_code": "l4-24",
    "replicas": 2,
    "required_gpus": 2,
    "committed_gpus": 0,
    "region_capacity": 12,
    "headroom_before": 12,
    "headroom_after": 10,
    "reasons": [
      { "code": "insufficient_capacity", "needed": 14, "available": 12,
        "detail": "Needs 14 GPU-equivalents but only 12 are free in Mumbai." }
    ],
    "warnings": []
  },
  "cost": { "hourly_usd": 2.2, "daily_usd": 52.8, "monthly_usd": 1606.0,
            "within_budget": true, "overage_usd": 0.0, "budget_used_ratio": 0.803 },
  "model": { ... }, "sku": { ... }, "region": { ... }
}
```

Reason codes: `insufficient_capacity` · `residency_violation` · `sku_unavailable` · `unknown_model` · `unknown_region` · `unknown_sku`.

Warning codes: `headroom_tight` (>80% of remaining headroom) · `model_spans_gpus` (needs more than one card per replica) · `over_budget`.

`422` for an unknown `model_code`, `region_code`, or `sku_code`.

### `POST /api/deployments` → `201`

Same body as `/plan`. **Re-runs the full evaluation inside the transaction** rather than trusting a plan the client echoes back — because between the plan and the apply, another request can consume the headroom.

`201` returns the deployment. `409` if rejected, with the reasons:

```json
{
  "detail": {
    "message": "Deployment rejected by admission control.",
    "reasons": [ { "code": "residency_violation", "detail": "..." } ],
    "verdict": { ... }
  }
}
```

### `GET /api/deployments` → `200`

All of your deployments, newest first. Includes `observed_replicas` (`null` until something reports).

### `GET /api/deployments/{id}` → `200` / `404`

### `PATCH /api/deployments/{id}/scale` → `200`

```json
{ "replicas": 3 }
```

Charges only the **delta** against regional headroom — scaling 2→3 checks for 3 total, not 3 on top of the 2 you already own.

`replicas: 0` is a legal call: it stops the deployment, zeroes the cost, and sets status to `stopped`.

`409` if the region can't absorb the new size, with the same reason shape as create.

### `POST /api/deployments/{id}/observe` → `200`

```json
{ "observed_replicas": 5 }
```

Records what the cluster reports. In a real system this is written by an agent polling the cluster, not by a browser; it's exposed here so the drift model is complete and testable end to end.

### `GET /api/deployments/drift/report` → `200`

```json
{
  "summary": { "total": 3, "in_sync": 1, "drifted": 2, "critical": 1, "clean": false },
  "findings": [
    {
      "deployment_id": 2,
      "name": "second-deploy",
      "region": "ap-south",
      "state": "missing",
      "severity": "critical",
      "desired_replicas": 2,
      "observed_replicas": 0,
      "detail": "Declared 2 replica(s) but nothing is running."
    }
  ]
}
```

States: `in_sync` · `scaled_out` · `scaled_in` · `missing` · `unreported`. Findings are sorted critical-first.

Note that `unreported` is **not** in `in_sync`. A deployment that has never checked in is excluded from healthy, not counted as healthy.

### `DELETE /api/deployments/{id}` → `204`

Gone immediately, no soft-delete, no grace period.

---

## Pipelines

### `POST /api/pipelines` → `201`

```json
{
  "name": "rag-ingest",
  "description": "optional",
  "steps": [
    { "key": "ingest", "operation": "fetch", "duration_seconds": 45 },
    { "key": "chunk",  "operation": "split", "duration_seconds": 90, "model_code": "llama-3-8b" }
  ],
  "edges": [ { "before": "ingest", "after": "chunk" } ]
}
```

Constraints: `key` matches `^[a-z0-9_-]+$`, 1–64 chars, max 64 steps, max 256 edges, `duration_seconds` 1–86400, `model_code` must exist in the catalog.

`422` for a structurally invalid graph:

```json
{
  "detail": {
    "message": "Pipeline graph is invalid.",
    "issues": [ { "code": "cycle_detected", "detail": "..." } ],
    "cycle_nodes": ["ingest", "chunk", "embed", "eval"]
  }
}
```

Issue codes: `cycle_detected` · `self_loop` · `unknown_step` · `duplicate_step` · `empty_pipeline`.

### `GET /api/pipelines` → `200`

List without the graph body — just `is_valid` and `critical_path_seconds` so the list view needs no recompute.

### `GET /api/pipelines/{id}` → `200`

Detail, including the full validation:

```json
{
  "id": 1, "name": "rag-ingest", "is_valid": true,
  "critical_path_seconds": 525,
  "steps": [ ... ], "edges": [ ... ],
  "validation": {
    "valid": true,
    "order": ["ingest", "chunk", "embed", "index", "eval"],
    "total_seconds": 585,
    "critical_path_seconds": 525,
    "critical_path": ["ingest", "chunk", "embed", "eval"],
    "issues": [],
    "cycle_nodes": []
  }
}
```

`total_seconds` (585) is the sum of all work. `critical_path_seconds` (525) is the wall-clock floor — the longest chain, since parallel branches overlap. Two different questions; the docs show both so nobody confuses them.

### `DELETE /api/pipelines/{id}` → `204`

---

## Health

### `GET /api/health` → `200`

```json
{ "status": "ok", "version": "1.0.0", "database": "ok", "environment": "development" }
```

`database` reflects an actual `SELECT 1`. If the probe fails the field reads `unreachable` and `status` becomes `degraded`. A health check that always returns `ok` is worse than none, because it teaches you to ignore it.

Exempt from rate limiting so a monitoring loop can't lock itself out.

---

## Security headers

Every response carries:

```
Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline';
                         script-src 'self'; connect-src 'self'; frame-ancestors 'none'
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: same-origin
Permissions-Policy: geolocation=(), microphone=(), camera=()
X-Request-ID: <16 hex chars>
```

`style-src` needs `'unsafe-inline'` because the SPA sets a couple of custom properties; `script-src` does not, and there is no inline script anywhere in the app.
