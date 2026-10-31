"""Deployment lifecycle: plan, create, list, scale, observe, delete.

The important contract in this file: `POST /plan` writes nothing, and `POST /`
re-runs the same evaluation rather than trusting a plan the client is echoing
back. Between a plan and an apply, another request can consume the headroom.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import current_user
from ..engine import (
    REGIONS,
    CommittedDeployment,
    Observation,
    build_report,
    evaluate,
    forecast,
    get_model,
    get_sku,
)
from ..models import Deployment, User
from ..schemas import (
    DeploymentOut,
    DeploymentPlanRequest,
    ObserveRequest,
    PlanResponse,
    ScaleRequest,
)

router = APIRouter(prefix="/api/deployments", tags=["deployments"])

ACTIVE = "active"


def _resolve(payload: DeploymentPlanRequest):  # type: ignore[no-untyped-def]
    """Fetch the three catalog entries a plan needs, or 422 with the culprit."""
    model = get_model(payload.model_code)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown model_code '{payload.model_code}'.",
        )
    region = REGIONS.get(payload.region_code)
    if region is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown region_code '{payload.region_code}'.",
        )
    sku = get_sku(payload.sku_code)
    if sku is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown sku_code '{payload.sku_code}'.",
        )
    return model, region, sku


def _committed(db: Session, owner_id: int, region_code: str) -> list[CommittedDeployment]:
    """Everything already consuming capacity in a region, for one owner."""
    rows = db.scalars(
        select(Deployment).where(
            Deployment.owner_id == owner_id,
            Deployment.region_code == region_code,
            Deployment.status == ACTIVE,
        )
    ).all()
    return [
        CommittedDeployment(
            deployment_id=d.id,
            model_code=d.model_code,
            sku_code=d.sku_code,
            replicas=d.replicas,
            required_gpus=d.required_gpus,
        )
        for d in rows
    ]


def _catalog_card(model, region, sku) -> tuple[dict, dict, dict]:  # type: ignore[no-untyped-def]
    return (
        {
            "code": model.code,
            "display_name": model.display_name,
            "min_vram_gb": model.min_vram_gb,
            "required_residency": list(model.required_residency),
            "parameters_b": model.parameters_b,
        },
        {
            "code": sku.code,
            "display_name": sku.display_name,
            "vram_gb": sku.vram_gb,
            "hourly_usd": sku.hourly_usd,
        },
        {
            "code": region.code,
            "display_name": region.display_name,
            "gpu_capacity": region.gpu_capacity,
            "data_residency": list(region.data_residency),
        },
    )


@router.post("/plan", response_model=PlanResponse)
def plan(
    payload: DeploymentPlanRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> PlanResponse:
    """Evaluate a candidate deployment. Writes nothing -- safe to call in a loop."""
    model, region, sku = _resolve(payload)
    verdict = evaluate(region, model, sku, payload.replicas, _committed(db, user.id, region.code))
    cost = forecast(sku, payload.replicas, payload.monthly_budget_usd)
    m, s, r = _catalog_card(model, region, sku)
    return PlanResponse(verdict=verdict.as_dict(), cost=cost.as_dict(), model=m, sku=s, region=r)


@router.post("", response_model=DeploymentOut, status_code=status.HTTP_201_CREATED)
def create_deployment(
    payload: DeploymentPlanRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> DeploymentOut:
    """Create a deployment, re-evaluating admission inside this transaction."""
    model, region, sku = _resolve(payload)
    verdict = evaluate(region, model, sku, payload.replicas, _committed(db, user.id, region.code))

    if not verdict.accepted:
        # 409 rather than 400: the request is well-formed, the *world* says no.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Deployment rejected by admission control.",
                "reasons": [r.as_dict() for r in verdict.reasons],
                "verdict": verdict.as_dict(),
            },
        )

    cost = forecast(sku, payload.replicas, payload.monthly_budget_usd)

    deployment = Deployment(
        owner_id=user.id,
        name=payload.name,
        model_code=model.code,
        region_code=region.code,
        sku_code=sku.code,
        replicas=payload.replicas,
        observed_replicas=None,
        required_gpus=verdict.required_gpus,
        monthly_cost_usd=cost.monthly_usd,
        monthly_budget_usd=payload.monthly_budget_usd,
        status=ACTIVE,
    )
    db.add(deployment)
    db.commit()
    db.refresh(deployment)
    return DeploymentOut.model_validate(deployment)


@router.get("", response_model=list[DeploymentOut])
def list_deployments(
    db: Session = Depends(get_db), user: User = Depends(current_user)
) -> list[DeploymentOut]:
    rows = db.scalars(
        select(Deployment)
        .where(Deployment.owner_id == user.id)
        .order_by(Deployment.created_at.desc())
    ).all()
    return [DeploymentOut.model_validate(d) for d in rows]


def _owned(db: Session, deployment_id: int, user: User) -> Deployment:
    """Fetch one deployment, or 404.

    404 rather than 403 for someone else's deployment on purpose: a 403 confirms
    the id exists, which is an existence oracle.
    """
    d = db.get(Deployment, deployment_id)
    if d is None or d.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found.")
    return d


@router.get("/drift/report")
def drift_report(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """Desired vs observed across every deployment this user owns."""
    rows = db.scalars(
        select(Deployment)
        .where(Deployment.owner_id == user.id, Deployment.status == ACTIVE)
        .order_by(Deployment.created_at.desc())
    ).all()

    observations = [
        Observation(
            deployment_id=d.id,
            name=d.name,
            region_code=d.region_code,
            desired_replicas=d.replicas,
            observed_replicas=d.observed_replicas,
        )
        for d in rows
    ]
    return build_report(observations).as_dict()


@router.get("/{deployment_id}", response_model=DeploymentOut)
def get_deployment(
    deployment_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> DeploymentOut:
    return DeploymentOut.model_validate(_owned(db, deployment_id, user))


@router.patch("/{deployment_id}/scale", response_model=DeploymentOut)
def scale(
    deployment_id: int,
    payload: ScaleRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> DeploymentOut:
    """Change replica count, charging the *delta* against regional headroom."""
    d = _owned(db, deployment_id, user)
    model = get_model(d.model_code)
    region = REGIONS.get(d.region_code)
    sku = get_sku(d.sku_code)
    if model is None or region is None or sku is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This deployment references catalog entries that no longer exist.",
        )

    if payload.replicas == 0:
        d.status = "stopped"
        d.replicas = 0
        d.required_gpus = 0
        d.monthly_cost_usd = 0.0
        db.commit()
        db.refresh(d)
        return DeploymentOut.model_validate(d)

    # Only the growth is new demand. Counting the existing commitment again would
    # double-charge the deployment against a region it already occupies.
    others = [c for c in _committed(db, user.id, d.region_code) if c.deployment_id != d.id]
    verdict = evaluate(region, model, sku, payload.replicas, others)

    if not verdict.accepted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Scale rejected: the region cannot absorb the new size.",
                "reasons": [r.as_dict() for r in verdict.reasons],
                "verdict": verdict.as_dict(),
            },
        )

    cost = forecast(sku, payload.replicas, d.monthly_budget_usd)
    d.replicas = payload.replicas
    d.required_gpus = verdict.required_gpus
    d.monthly_cost_usd = cost.monthly_usd
    d.status = ACTIVE
    db.commit()
    db.refresh(d)
    return DeploymentOut.model_validate(d)


@router.post("/{deployment_id}/observe", response_model=DeploymentOut)
def observe(
    deployment_id: int,
    payload: ObserveRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> DeploymentOut:
    """Record what the cluster actually shows, for the drift report.

    In a real control plane this is written by an agent polling the cluster, not
    by the browser. Exposed over the API here so the model is complete and
    testable end to end.
    """
    d = _owned(db, deployment_id, user)
    d.observed_replicas = payload.observed_replicas
    db.commit()
    db.refresh(d)
    return DeploymentOut.model_validate(d)


@router.delete("/{deployment_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_deployment(
    deployment_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> Response:
    d = _owned(db, deployment_id, user)
    db.delete(d)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
