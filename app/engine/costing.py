"""Cost forecasting.

The only opinionated number here is 730: the average number of hours in a month
(365.25 x 24 / 12). Using 30 days understates a monthly bill by about 1.5%, which
is the kind of error that turns a "within budget" verdict into an invoice.
"""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import Sku

HOURS_PER_MONTH = 730.0


@dataclass(frozen=True)
class CostForecast:
    replicas: int
    sku_code: str
    hourly_usd: float
    daily_usd: float
    monthly_usd: float
    monthly_budget_usd: float | None
    within_budget: bool
    overage_usd: float
    budget_used_ratio: float | None

    def as_dict(self) -> dict[str, object]:
        return {
            "replicas": self.replicas,
            "sku": self.sku_code,
            "hourly_usd": self.hourly_usd,
            "daily_usd": self.daily_usd,
            "monthly_usd": self.monthly_usd,
            "monthly_budget_usd": self.monthly_budget_usd,
            "within_budget": self.within_budget,
            "overage_usd": self.overage_usd,
            "budget_used_ratio": self.budget_used_ratio,
        }


def _money(value: float) -> float:
    return round(value, 2)


def forecast(
    sku: Sku,
    replicas: int,
    monthly_budget_usd: float | None = None,
) -> CostForecast:
    """Linear cost model. Honest about being linear -- see ARCHITECTURE.md §7."""
    hourly = _money(sku.hourly_usd * max(replicas, 0))
    daily = _money(hourly * 24)
    monthly = _money(hourly * HOURS_PER_MONTH)

    if monthly_budget_usd is None:
        return CostForecast(
            replicas=replicas,
            sku_code=sku.code,
            hourly_usd=hourly,
            daily_usd=daily,
            monthly_usd=monthly,
            monthly_budget_usd=None,
            within_budget=True,
            overage_usd=0.0,
            budget_used_ratio=None,
        )

    overage = _money(max(monthly - monthly_budget_usd, 0.0))
    ratio = round(monthly / monthly_budget_usd, 4) if monthly_budget_usd > 0 else None
    return CostForecast(
        replicas=replicas,
        sku_code=sku.code,
        hourly_usd=hourly,
        daily_usd=daily,
        monthly_usd=monthly,
        monthly_budget_usd=monthly_budget_usd,
        within_budget=monthly <= monthly_budget_usd,
        overage_usd=overage,
        budget_used_ratio=ratio,
    )


def region_monthly_total(
    sku_costs: list[tuple[Sku, int]],
) -> float:
    """Sum a set of (sku, replicas) pairs into one monthly figure."""
    return _money(sum(s.hourly_usd * r * HOURS_PER_MONTH for s, r in sku_costs))
