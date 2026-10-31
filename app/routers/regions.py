"""Region capacity, computed live from the catalog and actual deployments.

Nothing here is seeded. Committed capacity is a SUM over the caller's own active
deployments, so the number on screen is derived from rows that exist.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import current_user
from ..engine import MODELS, REGIONS, catalog_snapshot, models_allowed_in
from ..models import Deployment, User
from ..schemas import RegionCapacityOut

router = APIRouter(prefix="/api/regions", tags=["regions"])


@router.get("/catalog")
def catalog(_: User = Depends(current_user)) -> dict:
    """The static reference data: regions, SKUs, and models."""
    return catalog_snapshot()


@router.get("", response_model=list[RegionCapacityOut])
def list_regions(
    db: Session = Depends(get_db), user: User = Depends(current_user)
) -> list[RegionCapacityOut]:
    rows = db.execute(
        select(
            Deployment.region_code,
            func.coalesce(func.sum(Deployment.required_gpus), 0),
            func.count(Deployment.id),
            func.coalesce(func.sum(Deployment.monthly_cost_usd), 0.0),
        )
        .where(Deployment.owner_id == user.id, Deployment.status == "active")
        .group_by(Deployment.region_code)
    ).all()

    by_region = {r[0]: (int(r[1]), int(r[2]), float(r[3])) for r in rows}

    out: list[RegionCapacityOut] = []
    for code, region in REGIONS.items():
        committed, active, cost = by_region.get(code, (0, 0, 0.0))
        headroom = region.gpu_capacity - committed
        out.append(
            RegionCapacityOut(
                code=region.code,
                display_name=region.display_name,
                gpu_capacity=region.gpu_capacity,
                committed_gpus=committed,
                headroom_gpus=headroom,
                utilisation=(
                    round(committed / region.gpu_capacity, 4) if region.gpu_capacity else 0.0
                ),
                data_residency=list(region.data_residency),
                skus=list(region.skus),
                active_deployments=active,
                monthly_cost_usd=round(cost, 2),
            )
        )
    return out


@router.get("/{region_code}/models")
def models_in_region(region_code: str, _: User = Depends(current_user)) -> dict:
    region = REGIONS.get(region_code)
    if region is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown region '{region_code}'.",
        )

    allowed = models_allowed_in(region_code)
    return {
        "region": region_code,
        "display_name": region.display_name,
        "models": [
            {
                "code": m.code,
                "display_name": m.display_name,
                "min_vram_gb": m.min_vram_gb,
                "required_residency": list(m.required_residency),
            }
            for m in allowed
        ],
        "excluded": [m.code for m in MODELS.values() if not region.permits(m.required_residency)],
    }
