"""Engine tests: pure arithmetic, no HTTP, no database.

These are the tests that matter most, because every verdict the product shows is
computed here. Each assertion names a number, so a regression is a failing number
rather than a changed string.
"""

from __future__ import annotations

import math

import pytest

from app.engine import (
    HOURS_PER_MONTH,
    REGIONS,
    VRAM_SAFETY_FACTOR,
    CommittedDeployment,
    Edge,
    Step,
    best_regions,
    build_report,
    classify,
    evaluate,
    forecast,
    gpus_required,
    validate_dag,
)
from app.engine.catalog import get_model, get_region, get_sku, models_allowed_in
from app.engine.drift import DriftState, Observation, Severity


class TestGpuArithmetic:
    def test_exact_fit_does_not_fit(self) -> None:
        """A model that exactly fills a card is rejected, not accepted-then-OOM."""
        sku = get_sku("l4-24")
        assert sku is not None
        # 24GB model into a 24GB card: 24 * 1.25 = 30, ceil(30/24) = 2 GPUs.
        assert gpus_required(1, 24, sku) == 2

    def test_small_model_fits_one_card(self) -> None:
        sku = get_sku("a100-80")
        assert sku is not None
        assert gpus_required(1, 8, sku) == 1

    def test_sixteen_gb_fits_a_twenty_four_gb_card(self) -> None:
        # 16 * 1.25 = 20, which is under 24 -- so one card, not two.
        sku = get_sku("l4-24")
        assert sku is not None
        assert gpus_required(1, 16, sku) == 1

    def test_seventy_b_does_not_fit_one_eighty_gb_card(self) -> None:
        """92GB needed vs 80GB available -- this is the case that matters."""
        sku = get_sku("a100-80")
        assert sku is not None
        assert gpus_required(1, 92, sku) == 2

    def test_scales_linearly_with_replicas(self) -> None:
        sku = get_sku("a100-80")
        assert sku is not None
        assert gpus_required(3, 92, sku) == 6
        assert gpus_required(4, 92, sku) == 8

    def test_zero_replicas_needs_nothing(self) -> None:
        sku = get_sku("a100-80")
        assert sku is not None
        assert gpus_required(0, 92, sku) == 0

    def test_safety_factor_is_applied(self) -> None:
        """Locks in the 1.25 multiplier; changing it should fail this test."""
        assert VRAM_SAFETY_FACTOR == 1.25
        sku = get_sku("h100-80")
        assert sku is not None
        # ceil(40 * 1.25 / 80) == ceil(0.625) == 1
        assert gpus_required(1, 40, sku) == math.ceil(40 * 1.25 / 80)


class TestAdmissionControl:
    def test_accepts_when_capacity_available(self) -> None:
        # llama-3-8b needs 16GB -> one 24GB L4 per replica. 2 replicas = 2 GPUs.
        v = evaluate(get_region("ap-south"), get_model("llama-3-8b"), get_sku("l4-24"), 2)
        assert v.accepted is True
        assert v.required_gpus == 2
        assert v.headroom_before == 12
        assert v.headroom_after == 10

    def test_seventy_b_fits_inside_large_region(self) -> None:
        # 92GB model on an 80GB card needs 2 cards per replica; 4 replicas = 8.
        v = evaluate(get_region("ap-south"), get_model("llama-3-70b"), get_sku("a100-80"), 4)
        assert v.accepted is True
        assert v.required_gpus == 8
        assert v.headroom_before == 12
        assert v.headroom_after == 4

    def test_capacity_rejection_reports_needed_and_available(self) -> None:
        region = get_region("ap-south")  # 12 GPUs
        # Ten of the twelve are already spoken for by another workload.
        committed = [CommittedDeployment(1, "existing", "a100-80", 1, 10)]
        v = evaluate(region, get_model("llama-3-70b"), get_sku("a100-80"), 2, committed)
        assert v.accepted is False
        codes = [r.code for r in v.reasons]
        assert "insufficient_capacity" in codes
        reason = next(r for r in v.reasons if r.code == "insufficient_capacity")
        assert reason.needed == 4
        assert reason.available == 2

    def test_residency_blocks_cross_border_model(self) -> None:
        """Fin-LLM is US-only; deploying it to Asia must be refused."""
        v = evaluate(get_region("ap-south"), get_model("fin-llm-us"), get_sku("a100-80"), 1)
        assert v.accepted is False
        assert any(r.code == "residency_violation" for r in v.reasons)

    def test_residency_allows_permitted_region(self) -> None:
        v = evaluate(get_region("us-east"), get_model("fin-llm-us"), get_sku("a100-80"), 1)
        assert v.accepted is True

    def test_sovereign_model_limited_to_sovereign_region(self) -> None:
        med = get_model("med-llm-sovereign")
        assert evaluate(get_region("eu-central"), med, get_sku("h100-80"), 1).accepted is True
        # eu-west has eu+gdpr but not "sovereign"
        assert evaluate(get_region("eu-west"), med, get_sku("a100-80"), 1).accepted is False
        assert evaluate(get_region("us-east"), med, get_sku("a100-80"), 1).accepted is False

    def test_sku_not_offered_in_region(self) -> None:
        # h100-80 is not sold in ap-south
        v = evaluate(get_region("ap-south"), get_model("llama-3-8b"), get_sku("h100-80"), 1)
        assert v.accepted is False
        assert any(r.code == "sku_unavailable" for r in v.reasons)

    def test_headroom_warning_fires_at_eighty_percent(self) -> None:
        region = get_region("ap-south")  # 12 GPU
        committed = [CommittedDeployment(1, "x", "a100-80", 1, 8)]  # 4 left
        # qwen-14b needs 32GB -> 2 cards per replica on a 24GB L4; 2 replicas = 4.
        v = evaluate(region, get_model("qwen-14b"), get_sku("l4-24"), 2, committed)
        assert v.accepted is True
        assert v.required_gpus == 4
        # Consuming 100% of the remaining headroom must warn. Spilling across
        # cards is a separate fact (32GB model on 24GB cards), so both fire.
        codes = [w.code for w in v.warnings]
        assert "headroom_tight" in codes
        assert "model_spans_gpus" in codes

    def test_no_warning_when_under_the_threshold(self) -> None:
        v = evaluate(get_region("ap-south"), get_model("phi-3-mini"), get_sku("t4-16"), 1)
        assert v.accepted is True
        assert v.warnings == ()

    def test_utilisation_after_is_a_fraction(self) -> None:
        v = evaluate(get_region("ap-south"), get_model("llama-3-8b"), get_sku("l4-24"), 2)
        assert v.utilisation_after == pytest.approx(2 / 12, abs=1e-4)

    def test_best_regions_sorts_accepted_first_then_roomiest(self) -> None:
        verdicts = best_regions(
            get_model("llama-3-8b"), get_sku("l4-24"), 1, list(REGIONS.values()), {}
        )
        assert verdicts[0].accepted is True
        accepted = [v for v in verdicts if v.accepted]
        assert accepted == sorted(accepted, key=lambda v: -v.headroom_after)


class TestPipelineDag:
    def _steps(self, *keys: str) -> list[Step]:
        return [Step(key=k, operation="op", duration_seconds=10) for k in keys]

    def test_linear_chain_orders_correctly(self) -> None:
        result = validate_dag(self._steps("a", "b", "c"), [Edge("a", "b"), Edge("b", "c")])
        assert result.valid is True
        assert result.order == ("a", "b", "c")
        assert result.critical_path_seconds == 30

    def test_cycle_is_detected_and_named(self) -> None:
        result = validate_dag(self._steps("a", "b"), [Edge("a", "b"), Edge("b", "a")])
        assert result.valid is False
        assert set(result.cycle_nodes) == {"a", "b"}
        assert result.issues[0].code == "cycle_detected"

    def test_self_loop_is_rejected(self) -> None:
        result = validate_dag(self._steps("a"), [Edge("a", "a")])
        assert result.valid is False
        assert result.issues[0].code == "self_loop"

    def test_unknown_edge_endpoint_is_rejected(self) -> None:
        result = validate_dag(self._steps("a"), [Edge("a", "ghost")])
        assert result.valid is False
        assert result.issues[0].code == "unknown_step"

    def test_duplicate_keys_are_rejected(self) -> None:
        result = validate_dag(self._steps("a", "a"), [])
        assert result.valid is False
        assert result.issues[0].code == "duplicate_step"

    def test_critical_path_is_the_longest_branch_not_the_sum(self) -> None:
        """The whole point: total work != wall-clock time."""
        steps = [
            Step("ingest", "fetch", 45),
            Step("chunk", "split", 90),
            Step("embed", "encode", 240),
            Step("index", "upsert", 60),
            Step("eval", "score", 150),
        ]
        edges = [
            Edge("ingest", "chunk"),
            Edge("chunk", "embed"),
            Edge("chunk", "index"),
            Edge("embed", "eval"),
            Edge("index", "eval"),
        ]
        result = validate_dag(steps, edges)
        assert result.valid is True
        assert result.total_seconds == 585
        # ingest(45) -> chunk(90) -> embed(240) -> eval(150) = 525
        assert result.critical_path_seconds == 525
        assert result.critical_path == ("ingest", "chunk", "embed", "eval")

    def test_empty_pipeline_is_invalid(self) -> None:
        result = validate_dag([], [])
        assert result.valid is False
        assert result.issues[0].code == "empty_pipeline"

    def test_order_is_deterministic_for_parallel_independent_nodes(self) -> None:
        steps = self._steps("z", "a", "m")
        first = validate_dag(steps, [])
        second = validate_dag(list(reversed(steps)), [])
        assert first.order == second.order == ("a", "m", "z")


class TestDrift:
    def test_in_sync(self) -> None:
        state, severity, _ = classify(3, 3)
        assert state is DriftState.IN_SYNC
        assert severity is Severity.OK

    def test_scaled_out_is_critical_and_names_the_excess(self) -> None:
        state, severity, detail = classify(2, 5)
        assert state is DriftState.SCALED_OUT
        assert severity is Severity.CRITICAL
        assert "3" in detail

    def test_scaled_in_is_only_a_warning(self) -> None:
        state, severity, _ = classify(5, 2)
        assert state is DriftState.SCALED_IN
        assert severity is Severity.WARNING

    def test_missing_is_critical(self) -> None:
        state, severity, _ = classify(2, 0)
        assert state is DriftState.MISSING
        assert severity is Severity.CRITICAL

    def test_unreported_does_not_claim_a_sync(self) -> None:
        """An unknown observed count must never render as 'in sync'."""
        state, severity, _ = classify(2, None)
        assert state is DriftState.UNREPORTED
        assert state is not DriftState.IN_SYNC

    def test_report_sorts_critical_first(self) -> None:
        report = build_report(
            [
                Observation(1, "zeta", "us-east", 2, 2),
                Observation(2, "alpha", "us-east", 1, 4),
                Observation(3, "beta", "us-east", 3, 1),
            ]
        )
        assert report.total == 3
        assert report.drifted == 2
        assert report.in_sync == 1
        assert report.critical == 1
        assert report.findings[0].name == "alpha"
        assert report.findings[-1].severity is Severity.OK


class TestCosting:
    def test_monthly_uses_730_hours_not_720(self) -> None:
        sku = get_sku("a100-80")
        assert sku is not None
        c = forecast(sku, 2)
        assert HOURS_PER_MONTH == 730.0
        assert c.hourly_usd == 16.0
        assert c.daily_usd == 384.0
        assert c.monthly_usd == 11680.0

    def test_budget_verdict_with_overage(self) -> None:
        sku = get_sku("a100-80")
        assert sku is not None
        c = forecast(sku, 2, 5000.0)
        assert c.within_budget is False
        assert c.overage_usd == 6680.0
        assert c.budget_used_ratio == pytest.approx(2.336, abs=1e-3)

    def test_budget_verdict_when_within(self) -> None:
        sku = get_sku("t4-16")
        assert sku is not None
        c = forecast(sku, 1, 1000.0)
        assert c.within_budget is True
        assert c.overage_usd == 0.0

    def test_no_budget_means_no_overage(self) -> None:
        sku = get_sku("l4-24")
        assert sku is not None
        c = forecast(sku, 1, None)
        assert c.monthly_budget_usd is None
        assert c.budget_used_ratio is None
        assert c.within_budget is True


class TestCatalogIntegrity:
    def test_every_region_sku_exists(self) -> None:
        for region in REGIONS.values():
            for code in region.skus:
                assert get_sku(code) is not None, f"{region.code} sells unknown SKU {code}"

    def test_open_weights_are_allowed_everywhere(self) -> None:
        """A model with no residency requirement must not be blocked anywhere."""
        for region in REGIONS.values():
            allowed = {m.code for m in models_allowed_in(region.code)}
            assert "llama-3-8b" in allowed, region.code

    def test_sovereign_model_excluded_from_non_sovereign_regions(self) -> None:
        allowed = {m.code for m in models_allowed_in("us-east")}
        assert "med-llm-sovereign" not in allowed

    def test_region_capacities_are_positive(self) -> None:
        assert all(r.gpu_capacity > 0 for r in REGIONS.values())
