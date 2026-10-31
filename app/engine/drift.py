"""Drift: where declared state and observed state disagree.

A deployment stores what it *should* look like (`desired_replicas`) and what the
last known cluster reading *was* (`observed_replicas`). The gap between them is
the most useful number in the whole product, because it is where on-call work
comes from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class DriftState(StrEnum):
    IN_SYNC = "in_sync"
    SCALED_OUT = "scaled_out"
    SCALED_IN = "scaled_in"
    MISSING = "missing"
    UNREPORTED = "unreported"


class Severity(StrEnum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Observation:
    """One deployment's declared vs observed shape."""

    deployment_id: int
    name: str
    region_code: str
    desired_replicas: int
    observed_replicas: int | None


@dataclass(frozen=True)
class DriftFinding:
    deployment_id: int
    name: str
    region_code: str
    state: DriftState
    severity: Severity
    desired_replicas: int
    observed_replicas: int | None
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {
            "deployment_id": self.deployment_id,
            "name": self.name,
            "region": self.region_code,
            "state": self.state.value,
            "severity": self.severity.value,
            "desired_replicas": self.desired_replicas,
            "observed_replicas": self.observed_replicas,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class DriftReport:
    findings: tuple[DriftFinding, ...] = field(default_factory=tuple)
    total: int = 0
    drifted: int = 0
    critical: int = 0

    @property
    def in_sync(self) -> int:
        return self.total - self.drifted

    def as_dict(self) -> dict[str, object]:
        return {
            "summary": {
                "total": self.total,
                "in_sync": self.in_sync,
                "drifted": self.drifted,
                "critical": self.critical,
                "clean": self.drifted == 0,
            },
            "findings": [f.as_dict() for f in self.findings],
        }


def classify(desired: int, observed: int | None) -> tuple[DriftState, Severity, str]:
    """Classify one deployment's divergence.

    `scaled_out` outranks `scaled_in` in severity on purpose. In a cost-controlled
    environment, more replicas than declared means someone bypassed the control
    plane and is billing you for it; fewer means a node died, which is noisy but
    not expensive.
    """
    if observed is None:
        return (
            DriftState.UNREPORTED,
            Severity.WARNING,
            "No cluster reading has been reported yet, so drift cannot be checked.",
        )
    if desired == observed:
        return (DriftState.IN_SYNC, Severity.OK, f"{desired} replica(s) as declared.")
    if observed == 0 and desired > 0:
        return (
            DriftState.MISSING,
            Severity.CRITICAL,
            f"Declared {desired} replica(s) but nothing is running.",
        )
    if observed > desired:
        return (
            DriftState.SCALED_OUT,
            Severity.CRITICAL,
            f"{observed - desired} replica(s) above the declared {desired}. "
            f"This is unbudgeted spend and was not applied through Markaz.",
        )
    return (
        DriftState.SCALED_IN,
        Severity.WARNING,
        f"{desired - observed} replica(s) short of the declared {desired}. "
        f"Likely a node failure or an unrecorded scale-down.",
    )


def build_report(observations: list[Observation]) -> DriftReport:
    findings = []
    drifted = 0
    critical = 0
    for obs in observations:
        state, severity, detail = classify(obs.desired_replicas, obs.observed_replicas)
        if state is not DriftState.IN_SYNC:
            drifted += 1
        if severity is Severity.CRITICAL:
            critical += 1
        findings.append(
            DriftFinding(
                deployment_id=obs.deployment_id,
                name=obs.name,
                region_code=obs.region_code,
                state=state,
                severity=severity,
                desired_replicas=obs.desired_replicas,
                observed_replicas=obs.observed_replicas,
                detail=detail,
            )
        )

    # Worst first, then by name, so the operator's eye lands on the real problem.
    rank = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.OK: 2}
    findings.sort(key=lambda f: (rank[f.severity], f.name.lower()))

    return DriftReport(
        findings=tuple(findings),
        total=len(findings),
        drifted=drifted,
        critical=critical,
    )
