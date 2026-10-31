# Usage

A walkthrough of the four things this app does, with the real numbers it produces.

---

## 1. Register and land on Regions

The first screen after login shows each region with its capacity worked out from your own deployments:

```
ap-south    Mumbai          capacity 12   committed 0   headroom 12   0%   0 deployments
eu-central  Frankfurt       capacity 16   committed 0   headroom 16   0%   0 deployments
us-east     N. Virginia     capacity 24   committed 0   headroom 24   0%   0 deployments
```

Nothing here is seeded. `committed` is `SUM(required_gpus)` over your active deployments in that region, so a fresh account shows zero everywhere — which is the honest answer, not a placeholder.

---

## 2. Plan before you deploy

**Deployments → Plan a deployment.** Fill in model, region, GPU shape, and replicas.

### A plan that fits

```
model     llama-3-8b     (needs 16GB)
region    ap-south       (12 GPU capacity)
sku       l4-24          (24GB, $1.10/hr)
replicas  2
```

Result:

```
ACCEPTED
Required         2 GPU
Headroom         12 -> 10
Utilisation after  16.7%
Cost             $2.20/hr · $52.80/day · $1,606.00/mo
```

The arithmetic: 16GB × 1.25 safety margin = 20GB, which fits one 24GB card → 1 GPU per replica → 2 replicas = 2 GPUs.

### A plan that doesn't

Change the model to `llama-3-70b` and keep 4 replicas on `ap-south`:

```
ACCEPTED
Required         8 GPU
Headroom         12 -> 4
Cost             $64.00/hr · $1,536.00/day · $46,720.00/mo
```

That one *does* fit — 92GB on an 80GB card is two cards per replica, four replicas is eight. It still warns, because it consumes 67% of headroom and the next thing you deploy has much less room.

Now push it to 7 replicas and it refuses:

```
REJECTED  ·  insufficient_capacity
Needs 14 GPU but only 12 available.

REJECTED  ·  headroom_tight
This consumes 117% of remaining headroom, leaving -2 GPU.
```

Two reasons, both with the numbers that produced them. That's the whole point: not "denied", but "denied, and here is the subtraction".

### A plan that's legally wrong

```
model     fin-llm-us     (requires residency: us)
region    ap-south       (offers: apac, dpdp)
```

```
REJECTED  ·  residency_violation
fin-llm-us requires residency [us], which Mumbai does not provide
(it offers [apac, dpdp]).
```

And `med-llm-sovereign`, which requires `eu` + `gdpr` + `sovereign`, is accepted in `eu-central` and refused in `eu-west` — because `eu-west` offers `eu` and `gdpr` but not `sovereign`. Deploying a model trained on patient data outside the jurisdiction it was approved for is the mistake this check exists to prevent.

---

## 3. Drift

Deploy something, then use **Report cluster state** on it to enter what the cluster actually shows.

| Desired | Observed | State | Severity |
|---|---|---|---|
| 2 | 2 | `in_sync` | ok |
| 2 | 5 | `scaled_out` | **critical** — 3 replicas running that nobody asked for, and you're paying for them |
| 5 | 2 | `scaled_in` | warning — capacity missing, but nothing is overspending |
| 2 | 0 | `missing` | **critical** — declared and not running |
| 2 | *(never reported)* | `unreported` | warning — **not** counted as healthy |

That last row is the one I'd argue hardest for. A deployment whose agent has never checked in is the *most* likely to be broken, so treating "no news" as "fine" is exactly backwards. The report's `in_sync` count excludes it.

The summary at the top of the drift view:

```json
{ "total": 3, "in_sync": 1, "drifted": 2, "critical": 1, "clean": false }
```

---

## 4. Pipelines

**Pipelines → New pipeline.** Define steps with a key, operation, and duration; then edges meaning "before → after".

```json
{
  "name": "rag-ingest",
  "steps": [
    {"key": "ingest", "operation": "fetch",  "duration_seconds": 45},
    {"key": "chunk",  "operation": "split",  "duration_seconds": 90},
    {"key": "embed",  "operation": "encode", "duration_seconds": 240},
    {"key": "index",  "operation": "upsert", "duration_seconds": 60},
    {"key": "eval",   "operation": "score",  "duration_seconds": 150}
  ],
  "edges": [
    {"before": "ingest", "after": "chunk"},
    {"before": "chunk",  "after": "embed"},
    {"before": "chunk",  "after": "index"},
    {"before": "embed",  "after": "eval"},
    {"before": "index",  "after": "eval"}
  ]
}
```

Result:

```
VALID
Run order          ingest -> chunk -> embed -> index -> eval   (or index/embed swapped; both are correct)
Total work         585s
Critical path      525s
Path               ingest -> chunk -> embed -> eval
```

**Total work is 585 seconds. Wall-clock time is 525.** The `index` step runs in parallel with `embed` and finishes long before `embed` does, so 60 seconds of it are free. This is the number people get wrong when they estimate: summing every step duration overstates the job, and the overstatement is exactly the amount of parallelism you already have.

### A cycle

Add `{"before": "eval", "after": "ingest"}`:

```
422  Pipeline graph is invalid.
cycle_detected: ingest -> chunk -> embed -> eval -> ingest
cycle_nodes: [ingest, chunk, embed, eval]
```

Named nodes, not "invalid graph". If you can't see the loop, you can't fix it.

### Other rejections

- `self_loop` — a step that depends on itself
- `unknown_step` — an edge pointing at a key that doesn't exist
- `duplicate_step` — two steps with the same key
- `empty_pipeline` — nothing to run

---

## Things the UI deliberately does not do

- **No delete confirmation modal.** Deleting a deployment is a 204 and it's gone. It's a control plane for a demo; a real one would have an undo window or a soft-delete with a grace period, and I'd rather say that than pretend.
- **No live polling.** Refresh to see new drift. A real one would stream cluster state over SSE; the API shape is ready for it but the transport isn't built.
- **No per-SKU capacity.** GPU capacity is modelled as fungible — you buy N cards of headroom, not N of one part number. This is accurate for quota enforcement and wrong for physical scheduling. `ARCHITECTURE.md` §7 explains why I chose that.
