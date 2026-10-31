"""Pipeline DAG validation: cycles, run order, and the critical path.

Kahn's algorithm does double duty here. When it succeeds it hands back a valid
topological order; when it fails, the set of unprocessed nodes is *exactly* the
cycle participants -- far more useful to an operator than "cycle detected".
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Step:
    """One node in a pipeline graph."""

    key: str
    operation: str
    duration_seconds: int
    model_code: str | None = None


@dataclass(frozen=True)
class Edge:
    """`before` must complete before `after` may start."""

    before: str
    after: str


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail}


@dataclass(frozen=True)
class DagResult:
    valid: bool
    order: tuple[str, ...] = field(default_factory=tuple)
    issues: tuple[ValidationIssue, ...] = field(default_factory=tuple)
    cycle_nodes: tuple[str, ...] = field(default_factory=tuple)
    critical_path: tuple[str, ...] = field(default_factory=tuple)
    critical_path_seconds: int = 0
    total_seconds: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "order": list(self.order),
            "issues": [i.as_dict() for i in self.issues],
            "cycle_nodes": list(self.cycle_nodes),
            "critical_path": list(self.critical_path),
            "critical_path_seconds": self.critical_path_seconds,
            "total_seconds": self.total_seconds,
        }


def validate_dag(steps: list[Step], edges: list[Edge]) -> DagResult:
    """Validate structure, then compute order and critical path if it holds."""
    issues: list[ValidationIssue] = []

    if not steps:
        issues.append(
            ValidationIssue(code="empty_pipeline", detail="A pipeline needs at least one step.")
        )
        return DagResult(valid=False, issues=tuple(issues))

    by_key: dict[str, Step] = {}
    for step in steps:
        if step.key in by_key:
            issues.append(
                ValidationIssue(
                    code="duplicate_step",
                    detail=f"Step key '{step.key}' appears more than once.",
                )
            )
        by_key[step.key] = step

    if issues:
        return DagResult(valid=False, issues=tuple(issues))

    # --- Edge integrity ---------------------------------------------------
    for edge in edges:
        if edge.before not in by_key:
            issues.append(
                ValidationIssue(
                    code="unknown_step",
                    detail=f"Edge references unknown step '{edge.before}'.",
                )
            )
        if edge.after not in by_key:
            issues.append(
                ValidationIssue(
                    code="unknown_step",
                    detail=f"Edge references unknown step '{edge.after}'.",
                )
            )
        if edge.before == edge.after:
            issues.append(
                ValidationIssue(
                    code="self_loop",
                    detail=f"Step '{edge.before}' depends on itself.",
                )
            )

    if issues:
        return DagResult(valid=False, issues=tuple(issues))

    # --- Kahn's algorithm -------------------------------------------------
    indegree: dict[str, int] = dict.fromkeys(by_key, 0)
    adjacency: dict[str, list[str]] = {key: [] for key in by_key}
    for edge in edges:
        adjacency[edge.before].append(edge.after)
        indegree[edge.after] += 1

    # Sorted queue keeps the run order deterministic: the same pipeline always
    # displays the same sequence, which makes diffs and tests meaningful.
    ready = sorted(k for k, deg in indegree.items() if deg == 0)
    order: list[str] = []

    while ready:
        key = ready.pop(0)
        order.append(key)
        for nxt in adjacency[key]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
                ready.sort()

    if len(order) != len(by_key):
        # Whatever never reached indegree 0 is inside a cycle -- or downstream of
        # one, which is the same thing for an operator trying to fix it.
        stuck = sorted(k for k, deg in indegree.items() if deg > 0)
        issues.append(
            ValidationIssue(
                code="cycle_detected",
                detail=(
                    f"{len(stuck)} step(s) could not be ordered because of a dependency "
                    f"cycle: {', '.join(stuck)}."
                ),
            )
        )
        return DagResult(valid=False, issues=tuple(issues), cycle_nodes=tuple(stuck))

    # --- Critical path ----------------------------------------------------
    # Longest weighted path through the DAG. Straightforward DP because `order`
    # is already topological, so every predecessor is final by the time we visit.
    longest: dict[str, int] = {}
    parent: dict[str, str | None] = {}
    for key in order:
        longest[key] = by_key[key].duration_seconds
        parent[key] = None
    for key in order:
        for nxt in adjacency[key]:
            candidate = longest[key] + by_key[nxt].duration_seconds
            if candidate > longest[nxt]:
                longest[nxt] = candidate
                parent[nxt] = key

    end_key = max(order, key=lambda k: (longest[k], k))
    path: list[str] = []
    cursor: str | None = end_key
    while cursor is not None:
        path.append(cursor)
        cursor = parent[cursor]
    path.reverse()

    return DagResult(
        valid=True,
        order=tuple(order),
        issues=(),
        cycle_nodes=(),
        critical_path=tuple(path),
        critical_path_seconds=longest[end_key],
        total_seconds=sum(s.duration_seconds for s in steps),
    )
