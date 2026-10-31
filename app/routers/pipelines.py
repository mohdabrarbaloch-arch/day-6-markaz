"""Pipeline DAGs: create with validation, read back with run order and critical path.

A pipeline is rejected at write time if its graph is broken. Storing an invalid
graph and discovering it at run time is how a "pipeline builder" becomes a source
of 3 a.m. pages -- so validation is a precondition of the row existing at all.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import current_user
from ..engine import Edge, Step, validate_dag
from ..models import Pipeline, User
from ..schemas import PipelineCreate, PipelineDetail, PipelineOut

router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])


def _to_engine(payload: PipelineCreate) -> tuple[list[Step], list[Edge]]:
    steps = [
        Step(
            key=s.key,
            operation=s.operation,
            duration_seconds=s.duration_seconds,
            model_code=s.model_code,
        )
        for s in payload.steps
    ]
    edges = [Edge(before=e.before, after=e.after) for e in payload.edges]
    return steps, edges


def _from_stored(pipeline: Pipeline) -> tuple[list[Step], list[Edge]]:
    """Rebuild engine objects from the stored JSON."""
    steps = [
        Step(
            key=s["key"],
            operation=s["operation"],
            duration_seconds=int(s["duration_seconds"]),
            model_code=s.get("model_code"),
        )
        for s in pipeline.steps
    ]
    edges = [Edge(before=e["before"], after=e["after"]) for e in pipeline.edges]
    return steps, edges


def _detail(pipeline: Pipeline) -> PipelineDetail:
    steps, edges = _from_stored(pipeline)
    result = validate_dag(steps, edges)
    return PipelineDetail(
        id=pipeline.id,
        name=pipeline.name,
        description=pipeline.description,
        is_valid=pipeline.is_valid,
        critical_path_seconds=pipeline.critical_path_seconds,
        created_at=pipeline.created_at,
        updated_at=pipeline.updated_at,
        steps=pipeline.steps,
        edges=pipeline.edges,
        validation=result.as_dict(),
    )


@router.post("", response_model=PipelineDetail, status_code=status.HTTP_201_CREATED)
def create_pipeline(
    payload: PipelineCreate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> PipelineDetail:
    steps, edges = _to_engine(payload)
    result = validate_dag(steps, edges)

    if not result.valid:
        # 422: the body parsed, but the graph is structurally unsound. Each issue
        # names the offending step, so the client can point at it.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Pipeline graph is invalid.",
                "issues": [i.as_dict() for i in result.issues],
                "cycle_nodes": list(result.cycle_nodes),
            },
        )

    pipeline = Pipeline(
        owner_id=user.id,
        name=payload.name,
        description=payload.description,
        is_valid=True,
        critical_path_seconds=result.critical_path_seconds,
    )
    pipeline.steps = [s.model_dump() for s in payload.steps]
    pipeline.edges = [e.model_dump() for e in payload.edges]

    db.add(pipeline)
    db.commit()
    db.refresh(pipeline)
    return _detail(pipeline)


@router.get("", response_model=list[PipelineOut])
def list_pipelines(
    db: Session = Depends(get_db), user: User = Depends(current_user)
) -> list[PipelineOut]:
    rows = db.scalars(
        select(Pipeline).where(Pipeline.owner_id == user.id).order_by(Pipeline.created_at.desc())
    ).all()
    return [PipelineOut.model_validate(p) for p in rows]


def _owned(db: Session, pipeline_id: int, user: User) -> Pipeline:
    p = db.get(Pipeline, pipeline_id)
    if p is None or p.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found.")
    return p


@router.get("/{pipeline_id}", response_model=PipelineDetail)
def get_pipeline(
    pipeline_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> PipelineDetail:
    return _detail(_owned(db, pipeline_id, user))


@router.delete("/{pipeline_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_pipeline(
    pipeline_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> Response:
    p = _owned(db, pipeline_id, user)
    db.delete(p)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
