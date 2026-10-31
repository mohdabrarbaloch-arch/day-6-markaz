"""Pydantic request/response schemas.

Validation lives here rather than in the routers so that a malformed request is
rejected before any handler code runs, and so the OpenAPI schema is accurate.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


# ---------------------------------------------------------------- auth -------
class UserCreate(BaseModel):
    email: EmailStr
    password: Annotated[str, Field(min_length=8, max_length=128)]
    display_name: Annotated[str, Field(min_length=1, max_length=120)]

    @field_validator("password")
    @classmethod
    def password_has_variety(cls, v: str) -> str:
        # A length floor alone lets "aaaaaaaa" through. Requiring two character
        # classes is a cheap, defensible bar that still allows passphrases.
        if v.isdigit() or v.isalpha():
            raise ValueError("password must mix letters with digits or symbols")
        return v

    @field_validator("display_name")
    @classmethod
    def name_is_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("display_name must not be blank")
        return v.strip()


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    display_name: str
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


# --------------------------------------------------------- deployments ------
class DeploymentPlanRequest(BaseModel):
    """A candidate deployment. Used by both /plan and /."""

    name: Annotated[str, Field(min_length=1, max_length=120)]
    model_code: str
    region_code: str
    sku_code: str
    replicas: Annotated[int, Field(ge=1, le=64)]
    monthly_budget_usd: Annotated[float | None, Field(ge=0)] = None

    @field_validator("name")
    @classmethod
    def trim_name(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped


class DeploymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    model_code: str
    region_code: str
    sku_code: str
    replicas: int
    observed_replicas: int | None
    required_gpus: int
    monthly_cost_usd: float
    monthly_budget_usd: float | None
    status: str
    created_at: datetime
    updated_at: datetime


class ScaleRequest(BaseModel):
    replicas: Annotated[int, Field(ge=0, le=64)]


class ObserveRequest(BaseModel):
    """Report what the cluster actually shows, for drift purposes."""

    observed_replicas: Annotated[int, Field(ge=0, le=512)]


class PlanResponse(BaseModel):
    """A plan is a verdict plus the cost of acting on it."""

    verdict: dict
    cost: dict
    model: dict
    sku: dict
    region: dict


# ----------------------------------------------------------- pipelines ------
class StepIn(BaseModel):
    key: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")]
    operation: Annotated[str, Field(min_length=1, max_length=64)]
    duration_seconds: Annotated[int, Field(ge=1, le=86_400)]
    model_code: str | None = None

    @model_validator(mode="after")
    def model_exists_if_named(self) -> StepIn:
        if self.model_code is not None:
            from .engine import get_model  # noqa: PLC0415

            if get_model(self.model_code) is None:
                raise ValueError(f"unknown model_code '{self.model_code}'")
        return self


class EdgeIn(BaseModel):
    before: str
    after: str


class PipelineCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    description: Annotated[str | None, Field(max_length=2000)] = None
    steps: Annotated[list[StepIn], Field(min_length=1, max_length=64)]
    edges: Annotated[list[EdgeIn], Field(max_length=256)] = []


class PipelineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    is_valid: bool
    critical_path_seconds: int
    created_at: datetime
    updated_at: datetime


class PipelineDetail(PipelineOut):
    steps: list[dict]
    edges: list[dict]
    validation: dict


# --------------------------------------------------------------- misc ------
class HealthOut(BaseModel):
    status: str
    version: str
    database: str
    environment: str


class RegionCapacityOut(BaseModel):
    code: str
    display_name: str
    gpu_capacity: int
    committed_gpus: int
    headroom_gpus: int
    utilisation: float
    data_residency: list[str]
    skus: list[str]
    active_deployments: int
    monthly_cost_usd: float
