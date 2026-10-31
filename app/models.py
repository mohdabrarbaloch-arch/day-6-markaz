"""ORM models.

Two aggregate roots: `User` owns everything, and `Deployment` carries both the
declared and the observed shape so drift can be computed without a second table.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    deployments: Mapped[list[Deployment]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )
    pipelines: Mapped[list[Pipeline]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )


class Deployment(Base):
    __tablename__ = "deployments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    model_code: Mapped[str] = mapped_column(String(64), nullable=False)
    region_code: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    sku_code: Mapped[str] = mapped_column(String(32), nullable=False)

    # Declared intent vs last known cluster reading. Their difference is drift.
    replicas: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_replicas: Mapped[int | None] = mapped_column(Integer, nullable=True)
    required_gpus: Mapped[int] = mapped_column(Integer, nullable=False)

    # Snapshot of the cost at deploy time, so a price change in the catalog does
    # not silently rewrite history.
    monthly_cost_usd: Mapped[float] = mapped_column(nullable=False, default=0.0)
    monthly_budget_usd: Mapped[float | None] = mapped_column(nullable=True)

    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    owner: Mapped[User] = relationship(back_populates="deployments")

    __table_args__ = (Index("ix_deployments_owner_created", "owner_id", "created_at"),)


class Pipeline(Base):
    __tablename__ = "pipelines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Steps and edges are stored as JSON. They are validated by the engine on
    # write and never queried by field, so normalising them into two more tables
    # would add joins to every read in exchange for nothing.
    steps_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    edges_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    # Denormalised from the last validation so the list view needs no recompute.
    is_valid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    critical_path_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    owner: Mapped[User] = relationship(back_populates="pipelines")

    __table_args__ = (Index("ix_pipelines_owner_created", "owner_id", "created_at"),)

    @property
    def steps(self) -> list[dict[str, Any]]:
        return json.loads(self.steps_json or "[]")

    @steps.setter
    def steps(self, value: list[dict[str, Any]]) -> None:
        self.steps_json = json.dumps(value)

    @property
    def edges(self) -> list[dict[str, Any]]:
        return json.loads(self.edges_json or "[]")

    @edges.setter
    def edges(self, value: list[dict[str, Any]]) -> None:
        self.edges_json = json.dumps(value)
