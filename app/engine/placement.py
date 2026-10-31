"""Admission control: does this deployment fit, and if not, by how much?

The whole module is pure. Given a region, a model, a SKU and a list of what is
already committed there, it returns a verdict with numbers attached. No database
handle, no clock, no network -- which is why the interesting cases are cheap to test.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .catalog import Model, Region, Sku

# A deployment that exactly fills a GPU gets rejected. Over-rejecting costs a
# conversation; under-rejecting costs an outage, so the asymmetry is intentional.
VRAM_SAFETY_FACTOR = 1.25

# Above this share of remaining headroom we warn rather than reject: the current
# deployment is fine, the *next* one of this size will not be.
HEADROOM_WARNING_RATIO = 0.80


@dataclass(frozen=True)
class CommittedDeployment:
    """The part of an existing deployment that consumes regional capacity."""

    deployment_id: int
    model_code: str
    sku_code: str
    replicas: int
    required_gpus: int


@dataclass(frozen=True)
class Reason:
    """A single, machine-readable refusal with its arithmetic exposed."""

    code: str
    detail: str
    needed: int | float | None = None
    available: int | float | None = None

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {"code": self.code, "detail": self.detail}
        if self.needed is not None:
            out["needed"] = self.needed
        if self.available is not None:
            out["available"] = self.available
        return out


@dataclass(frozen=True)
class Verdict:
    """The result of evaluating one candidate deployment."""

    accepted: bool
    region_code: str
    model_code: str
    sku_code: str
    replicas: int
    required_gpus: int
    committed_gpus: int
    region_capacity: int
    headroom_before: int
    headroom_after: int
    reasons: tuple[Reason, ...] = field(default_factory=tuple)
    warnings: tuple[Reason, ...] = field(default_factory=tuple)

    @property
    def utilisation_after(self) -> float:
        """Fraction of regional capacity committed once this lands."""
        if self.region_capacity == 0:
            return 1.0
        used = self.committed_gpus + self.required_gpus
        return round(used / self.region_capacity, 4)

    def as_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "verdict": "accepted" if self.accepted else "rejected",
            "region": self.region_code,
            "model": self.model_code,
            "sku": self.sku_code,
            "replicas": self.replicas,
            "required_gpus": self.required_gpus,
            "committed_gpus": self.committed_gpus,
            "region_capacity": self.region_capacity,
            "headroom_before": self.headroom_before,
            "headroom_after": self.headroom_after,
            "utilisation_after": self.utilisation_after,
            "reasons": [r.as_dict() for r in self.reasons],
            "warnings": [w.as_dict() for w in self.warnings],
        }


def gpus_required(replicas: int, min_vram_gb: int, sku: Sku) -> int:
    """GPU-equivalents needed to host `replicas` copies of a model on one SKU.

    A model needing 92GB does not fit in an 80GB card, so it needs two -- and
    with the safety factor that becomes three at the margin. Ceiling division
    is the correct operator here, not rounding.
    """
    if replicas <= 0:
        return 0
    vram_per_replica = min_vram_gb * VRAM_SAFETY_FACTOR
    gpus_per_replica = math.ceil(vram_per_replica / sku.vram_gb)
    return gpus_per_replica * replicas


def committed_in_region(
    deployments: list[CommittedDeployment],
) -> tuple[int, int]:
    """Total GPU-equivalents committed, plus how many deployments contribute.

    Deliberately counts every SKU in the region together. Capacity is modelled as
    fungible across shapes because that is how a quota works: you buy N cards of
    headroom, not N cards of one specific part number. A per-SKU capacity model
    would be more accurate and much less useful for a control plane.
    """
    total = sum(d.required_gpus for d in deployments)
    return total, len(deployments)


def evaluate(
    region: Region,
    model: Model,
    sku: Sku,
    replicas: int,
    existing: list[CommittedDeployment] | None = None,
) -> Verdict:
    """Evaluate a candidate deployment against current regional commitment."""
    existing = existing or []
    committed, _ = committed_in_region(existing)
    headroom_before = region.gpu_capacity - committed
    required = gpus_required(replicas, model.min_vram_gb, sku)
    headroom_after = headroom_before - required

    reasons: list[Reason] = []
    warnings: list[Reason] = []

    # --- 1. Is the SKU sold here at all? -----------------------------------
    if not region.offers(sku.code):
        reasons.append(
            Reason(
                code="sku_unavailable",
                detail=(
                    f"{sku.display_name} is not offered in {region.display_name}. "
                    f"Available: {', '.join(region.skus)}."
                ),
            )
        )

    # --- 2. Residency ------------------------------------------------------
    missing = tuple(t for t in model.required_residency if t not in region.data_residency)
    if missing:
        reasons.append(
            Reason(
                code="residency_violation",
                detail=(
                    f"{model.display_name} requires residency "
                    f"[{', '.join(missing)}], which {region.display_name} does not "
                    f"provide (it offers [{', '.join(region.data_residency)}])."
                ),
            )
        )

    # --- 3. Capacity -------------------------------------------------------
    if required > headroom_before:
        reasons.append(
            Reason(
                code="insufficient_capacity",
                detail=(
                    f"Needs {required} GPU-equivalents but only {max(headroom_before, 0)} "
                    f"remain in {region.display_name} "
                    f"({committed} of {region.gpu_capacity} already committed)."
                ),
                needed=required,
                available=max(headroom_before, 0),
            )
        )

    # --- Non-fatal findings ------------------------------------------------
    accepted = not reasons

    if accepted and headroom_before > 0:
        share = required / headroom_before
        if share >= HEADROOM_WARNING_RATIO:
            warnings.append(
                Reason(
                    code="headroom_tight",
                    detail=(
                        f"This consumes {share:.0%} of the remaining headroom, leaving "
                        f"{headroom_after} GPU-equivalent(s). The next deployment of this "
                        f"size will be rejected."
                    ),
                    needed=required,
                    available=headroom_before,
                )
            )

    if accepted and replicas > 8:
        warnings.append(
            Reason(
                code="large_replica_count",
                detail=(
                    f"{replicas} replicas is a large single deployment. Consider splitting "
                    f"across regions for blast-radius reasons."
                ),
            )
        )

    if accepted and model.min_vram_gb > sku.vram_gb:
        warnings.append(
            Reason(
                code="model_spans_gpus",
                detail=(
                    f"{model.display_name} needs {model.min_vram_gb}GB but a single "
                    f"{sku.display_name} has {sku.vram_gb}GB, so each replica spans "
                    f"{math.ceil(model.min_vram_gb * VRAM_SAFETY_FACTOR / sku.vram_gb)} GPUs. "
                    f"Expect inter-GPU transfer overhead."
                ),
            )
        )

    return Verdict(
        accepted=accepted,
        region_code=region.code,
        model_code=model.code,
        sku_code=sku.code,
        replicas=replicas,
        required_gpus=required,
        committed_gpus=committed,
        region_capacity=region.gpu_capacity,
        headroom_before=headroom_before,
        headroom_after=headroom_after,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
    )


def best_regions(
    model: Model,
    sku: Sku,
    replicas: int,
    regions: list[Region],
    existing_by_region: dict[str, list[CommittedDeployment]],
) -> list[Verdict]:
    """Every candidate region, accepted ones first and roomiest first.

    Used by the deploy form to answer "where can this actually go?" instead of
    making the operator rediscover the answer by trial and error.
    """
    verdicts = [
        evaluate(r, model, sku, replicas, existing_by_region.get(r.code, [])) for r in regions
    ]
    return sorted(
        verdicts,
        key=lambda v: (not v.accepted, -v.headroom_after, v.region_code),
    )
