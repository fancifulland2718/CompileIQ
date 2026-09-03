import json
import pathlib

import pytest
from pydantic import ValidationError

from compileiq.forge_report import (
    OpaqueOptimizationReportV1,
    opaque_optimization_report_json_schema,
)
from compileiq.forge_search_v2 import (
    ForgeOpaqueEvaluationContextV1,
    ForgeOpaqueSearchBudgetV2,
    ForgeOpaqueSearchFinalizationV1,
    ForgeOpaqueSearchSessionV2,
    TrialCleanupV2,
    TrialFailureV2,
    TrialOutcomeV2,
)
from compileiq.forge_support import (
    ForgeOpaqueObjectiveV1,
    ForgeOpaqueTargetContractV1,
    forge_recipe_search_capability,
)
from compileiq.recipes import (
    OpaqueDynamicRecipeDomainV2,
    OpaqueRecipeBatchV2,
    OpaqueRecipeFidelityV2,
    OpaqueRecipeLineageV2,
)


def _contract(*objectives):
    return ForgeOpaqueTargetContractV1(
        objectives=tuple(
            ForgeOpaqueObjectiveV1(name=name, direction=direction)
            for name, direction in objectives
        )
    )


def _domain():
    capability = forge_recipe_search_capability().as_dict()
    return OpaqueDynamicRecipeDomainV2(
        provider_namespace="tests.complete_recipe",
        domain_version="complete-recipe.v1",
        generation_domain_id="semantic:test-graph",
        provider_registry_id="providers:test-graph",
        assembly_protocols=("tests.graph-recipe.v1",),
        recipe_schema="tests.complete-recipe.v1",
        search_strategy_id="tests.report.v1",
        compileiq_capability_id=capability["capability_id"],
        compileiq_core_commit=capability["core_commit"],
        compileiq_core_lock=capability["core_lock"],
    )


def _batch(recipe_ids, *, repeats=2, terminal=True):
    capability = forge_recipe_search_capability().as_dict()
    return OpaqueRecipeBatchV2(
        provider_namespace="tests.complete_recipe",
        domain_version="complete-recipe.v1",
        provider_semantic_fingerprint="semantic:test-graph",
        compileiq_capability_id=capability["capability_id"],
        compileiq_core_commit=capability["core_commit"],
        compileiq_core_lock=capability["core_lock"],
        stage_index=0,
        stage_fingerprint="tests.report-stage.v1",
        fidelity=OpaqueRecipeFidelityV2(
            name="production-shape",
            ordinal=0,
            repeat_count=repeats,
            work_scale=1.0,
            terminal=terminal,
        ),
        recipes=tuple(
            OpaqueRecipeLineageV2(
                recipe_id=recipe_id,
                planned_physical_id=f"planned:{recipe_id}",
            )
            for recipe_id in recipe_ids
        ),
    )


def _session(objective, contract, *, budget=16, checkpoint=None):
    return ForgeOpaqueSearchSessionV2(
        objective_function=objective,
        dynamic_domain=_domain(),
        evaluation_context=ForgeOpaqueEvaluationContextV1(
            reuse_scope="portable",
            workload_context_id="workload:report-test",
            evaluation_contract_id="evaluation:report-test",
            backend_environment_id="backend:cuda-test",
        ),
        baseline_recipe_id="baseline",
        target_contract=contract,
        budget=ForgeOpaqueSearchBudgetV2(
            evaluation_limit=budget,
            time_limit_seconds=60.0,
            materialized_memory_limit_bytes=1 << 20,
        ),
        deterministic_seed=17,
        checkpoint=checkpoint,
    )


def _outcome(request, metrics, *, failure=None):
    return TrialOutcomeV2(
        metrics={} if failure is not None else metrics,
        planned_physical_id=f"planned:{request.recipe_id}",
        materialized_physical_id=(
            None if failure is not None else f"materialized:{request.recipe_id}"
        ),
        materialized_memory_bytes=0 if failure is not None else 4096,
        provenance={"backend": "cuda", "driver": "test-driver"},
        cleanup=TrialCleanupV2(
            status="complete",
            released_resources=True,
            detail_code="provider_release_complete",
        ),
        failure=failure,
    )


def _finalize(session):
    return session.finalize(
        ForgeOpaqueSearchFinalizationV1(
            generation_status="strategy_complete",
            terminal_fidelity_status="complete",
            reason="frontier_exhausted",
        )
    )


def test_complete_single_objective_report_is_deterministic_and_round_trips():
    values = {"baseline": 10.0, "candidate": 6.0}

    def objective(request):
        return _outcome(request, {"latency_ms": values[request.recipe_id]})

    session = _session(objective, _contract(("latency_ms", "min")))
    session.submit_batch(_batch(("baseline", "candidate")))
    result = _finalize(session)

    full = result.report()
    repeated = result.report()
    summary = result.report(detail="summary")

    assert full.status.overall == "complete"
    assert full.report_id == repeated.report_id == summary.report_id
    assert full.to_json() == repeated.to_json()
    assert len(full.trials) == 4
    assert summary.trials == ()
    assert summary.checkpoint.embedded is False
    assert OpaqueOptimizationReportV1.from_json(full.to_json()) == full
    assert OpaqueOptimizationReportV1.from_dict(summary.to_dict()) == summary
    comparison = next(
        item
        for item in full.baseline_comparison
        if item.recipe_id == "candidate" and item.metric == "latency_ms"
    )
    assert comparison.directional_improvement == pytest.approx(4.0)
    assert comparison.relative_directional_improvement == pytest.approx(0.4)
    assert "candidate" in full.to_markdown()


def test_multiobjective_report_preserves_pareto_without_scalar_winner():
    values = {
        "baseline": (10.0, 10.0),
        "fast": (5.0, 14.0),
        "small": (8.0, 6.0),
        "dominated": (12.0, 12.0),
    }

    def objective(request):
        latency, memory = values[request.recipe_id]
        return _outcome(request, {"latency_ms": latency, "memory_mib": memory})

    session = _session(
        objective,
        _contract(("latency_ms", "min"), ("memory_mib", "min")),
    )
    session.submit_batch(_batch(tuple(values), repeats=1))
    report = _finalize(session).report(detail="summary")

    assert report.status.decision_status == "pareto_only"
    assert {item.recipe_id for item in report.pareto_front} == {"fast", "small"}
    assert "pareto_not_scalarized" in {item.code for item in report.warnings}
    assert "without a scalar winner" in report.to_markdown()


def test_budget_partial_report_resumes_without_repeating_measurements():
    batch = _batch(("baseline", "candidate"), repeats=2)
    first_keys = []

    def first_objective(request):
        first_keys.append(request.measurement_key)
        return _outcome(request, {"latency_ms": 10.0 if request.is_baseline else 7.0})

    first = _session(
        first_objective,
        _contract(("latency_ms", "min")),
        budget=2,
    )
    partial_result = first.submit_batch(batch)
    partial = partial_result.report(detail="summary")

    assert partial.status.overall == "partial"
    assert partial.status.termination_reason == "evaluation_budget_exhausted"
    assert "search_not_complete" in {item.code for item in partial.warnings}

    resumed_keys = []

    def resumed_objective(request):
        resumed_keys.append(request.measurement_key)
        return _outcome(request, {"latency_ms": 10.0 if request.is_baseline else 7.0})

    resumed = _session(
        resumed_objective,
        _contract(("latency_ms", "min")),
        budget=4,
        checkpoint=partial_result.checkpoint(),
    )
    resumed.submit_batch(batch)
    complete = _finalize(resumed).report()

    assert complete.status.overall == "complete"
    assert not set(first_keys).intersection(resumed_keys)
    assert len(first_keys) + len(resumed_keys) == 4
    assert complete.checkpoint.digest != partial.checkpoint.digest


def test_partial_failure_and_all_failed_reports_retain_structured_failures():
    failure = TrialFailureV2(
        category="execution",
        code="launch_failed",
        message="provider reported a launch failure",
        retryable=False,
    )

    def partial_objective(request):
        if request.recipe_id == "candidate":
            return _outcome(request, {}, failure=failure)
        return _outcome(request, {"latency_ms": 10.0})

    partial_session = _session(
        partial_objective,
        _contract(("latency_ms", "min")),
    )
    partial_session.submit_batch(_batch(("baseline", "candidate"), repeats=1))
    partial = _finalize(partial_session).report()

    failed_candidate = next(
        item for item in partial.candidates if item.recipe_id == "candidate"
    )
    assert failed_candidate.feasible is False
    assert failed_candidate.failures[0].failure["code"] == "launch_failed"
    assert "failed_observations" in {item.code for item in partial.warnings}

    def failed_objective(request):
        return _outcome(request, {}, failure=failure)

    failed_session = _session(
        failed_objective,
        _contract(("latency_ms", "min")),
    )
    failed_session.submit_batch(_batch(("baseline", "candidate"), repeats=1))
    failed = _finalize(failed_session).report()

    assert failed.status.overall == "failed"
    assert failed.status.terminal_state == "all_failed"
    assert failed.pareto_front == ()
    assert len(failed.trials) == 2
    assert "No feasible Pareto candidate" in failed.to_markdown()


def test_report_identity_rejects_tampering_and_schema_is_checked_in():
    def objective(request):
        return _outcome(request, {"latency_ms": 10.0})

    session = _session(objective, _contract(("latency_ms", "min")))
    session.submit_batch(_batch(("baseline",), repeats=1))
    report = _finalize(session).report(detail="summary")
    payload = json.loads(report.to_json())
    payload["budget"]["evaluation_count"] += 1

    with pytest.raises(ValidationError, match="report identity mismatch"):
        OpaqueOptimizationReportV1.from_dict(payload)

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    checked_in = json.loads(
        (repo_root / "schemas" / "opaque-optimization-report-v1.schema.json").read_text()
    )
    assert checked_in == opaque_optimization_report_json_schema()
