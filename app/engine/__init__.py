"""The Markaz engine: pure functions, no I/O.

Import from here rather than from the individual modules, so the public surface
of the engine is explicit and stays small.
"""

from .catalog import (
    MODELS,
    REGIONS,
    SKUS,
    Model,
    Region,
    Sku,
    catalog_snapshot,
    get_model,
    get_region,
    get_sku,
    models_allowed_in,
    regions_for_sku,
)
from .costing import HOURS_PER_MONTH, CostForecast, forecast, region_monthly_total
from .dag import DagResult, Edge, Step, ValidationIssue, validate_dag
from .drift import (
    DriftFinding,
    DriftReport,
    DriftState,
    Observation,
    Severity,
    build_report,
    classify,
)
from .placement import (
    HEADROOM_WARNING_RATIO,
    VRAM_SAFETY_FACTOR,
    CommittedDeployment,
    Reason,
    Verdict,
    best_regions,
    committed_in_region,
    evaluate,
    gpus_required,
)

__all__ = [
    "MODELS",
    "REGIONS",
    "SKUS",
    "Model",
    "Region",
    "Sku",
    "catalog_snapshot",
    "get_model",
    "get_region",
    "get_sku",
    "models_allowed_in",
    "regions_for_sku",
    "HOURS_PER_MONTH",
    "CostForecast",
    "forecast",
    "region_monthly_total",
    "DagResult",
    "Edge",
    "Step",
    "ValidationIssue",
    "validate_dag",
    "DriftFinding",
    "DriftReport",
    "DriftState",
    "Observation",
    "Severity",
    "build_report",
    "classify",
    "HEADROOM_WARNING_RATIO",
    "VRAM_SAFETY_FACTOR",
    "CommittedDeployment",
    "Reason",
    "Verdict",
    "best_regions",
    "committed_in_region",
    "evaluate",
    "gpus_required",
]
