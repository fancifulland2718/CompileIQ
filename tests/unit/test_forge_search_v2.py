import json

import pytest

from compileiq.forge_search_v2 import (
    ForgeOpaqueEvaluationContextV1,
    ForgeOpaqueSearchBudgetV2,
    ForgeOpaqueSearchCheckpointV2,
    ForgeOpaqueSearchFinalizationV1,
    ForgeOpaqueSearchSessionV2,
    TrialCleanupV2,
    TrialOutcomeV2,
)
from compileiq.forge_support import (
    ForgeOpaqueConstraintV1,
    ForgeOpaqueObjectiveV1,
    ForgeOpaqueRecipeExhaustiveSearchV1,
    ForgeOpaqueTargetContractV1,
    forge_recipe_search_capability,
)
from compileiq.recipes import (
    OpaqueDynamicRecipeDomainV2,
    OpaqueRecipeBatchV2,
    OpaqueRecipeDomainV1,
    OpaqueRecipeFidelityV2,
    OpaqueRecipeLineageV2,
)


def _contract(*objectives, constraints=()):
    return ForgeOpaqueTargetContractV1(
        objectives=tuple(
            ForgeOpaqueObjectiveV1(name=name, direction=direction) for name, direction in objectives
        ),
        constraints=tuple(constraints),
    )


def _batch(
    recipe_ids,
    *,
    stage_index=0,
    parent=None,
    parent_ids=None,
    fidelity_name="screen",
    fidelity_ordinal=0,
    repeats=1,
    terminal=False,
    estimates=None,
    planned_ids=None,
):
    capability = forge_recipe_search_capability().as_dict()
    estimates = estimates or {}
    parent_ids = parent_ids or {}
    planned_ids = planned_ids or {}
    return OpaqueRecipeBatchV2(
        provider_namespace="taichi_forge.graph.complete_recipe",
        domain_version="complete-graph-recipe.v2",
        provider_semantic_fingerprint="semantic:graph-a",
        compileiq_capability_id=capability["capability_id"],
        compileiq_core_commit=capability["core_commit"],
        compileiq_core_lock=capability["core_lock"],
        stage_index=stage_index,
        stage_fingerprint=f"provider-stage:{stage_index}:{fidelity_name}",
        parent_batch_fingerprint=(None if parent is None else parent.batch_fingerprint),
        fidelity=OpaqueRecipeFidelityV2(
            name=fidelity_name,
            ordinal=fidelity_ordinal,
            repeat_count=repeats,
            work_scale=float(fidelity_ordinal + 1),
            terminal=terminal,
        ),
        recipes=tuple(
            OpaqueRecipeLineageV2(
                recipe_id=recipe_id,
                planned_physical_id=planned_ids.get(
                    recipe_id,
                    f"planned:{recipe_id}",
                ),
                parent_recipe_ids=tuple(parent_ids.get(recipe_id, ())),
                estimated_materialized_bytes=estimates.get(recipe_id, 0),
            )
            for recipe_id in recipe_ids
        ),
    )


def _domain(**overrides):
    capability = forge_recipe_search_capability().as_dict()
    fields = {
        "provider_namespace": "taichi_forge.graph.complete_recipe",
        "domain_version": "complete-graph-recipe.v2",
        "generation_domain_id": "semantic:graph-a",
        "provider_registry_id": "providers:graph-a",
        "assembly_protocols": ("taichi_forge.runtime_graph_recipe.v1",),
        "recipe_schema": "taichi_forge.complete_graph_recipe.v2",
        "search_strategy_id": "tests.dynamic-batches.v1",
        "compileiq_capability_id": capability["capability_id"],
        "compileiq_core_commit": capability["core_commit"],
        "compileiq_core_lock": capability["core_lock"],
    }
    fields.update(overrides)
    return OpaqueDynamicRecipeDomainV2(**fields)


def _context(**overrides):
    fields = {
        "reuse_scope": "portable",
        "workload_context_id": "workload:test-a",
        "evaluation_contract_id": "evaluation:test-a",
        "backend_environment_id": "backend:cuda-test",
    }
    fields.update(overrides)
    return ForgeOpaqueEvaluationContextV1(**fields)


def _session(**kwargs):
    kwargs.setdefault("dynamic_domain", _domain())
    kwargs.setdefault("evaluation_context", _context())
    return ForgeOpaqueSearchSessionV2(**kwargs)


def _outcome(request, metrics, *, memory=0, materialized_id=None, planned_id=None):
    return TrialOutcomeV2(
        metrics=metrics,
        planned_physical_id=planned_id or f"planned:{request.recipe_id}",
        materialized_physical_id=materialized_id or f"materialized:{request.recipe_id}",
        materialized_memory_bytes=memory,
        provenance={
            "backend": "cuda",
            "batch": request.batch_fingerprint,
        },
        cleanup=TrialCleanupV2(
            status="complete",
            released_resources=True,
            detail_code="provider_release_complete",
        ),
    )


def _budget(evaluations, *, seconds=60.0, memory=1 << 20):
    return ForgeOpaqueSearchBudgetV2(
        evaluation_limit=evaluations,
        time_limit_seconds=seconds,
        materialized_memory_limit_bytes=memory,
    )


def test_batch_fingerprint_is_canonical_and_binds_stage_fidelity_and_lineage():
    forward = _batch(("recipe:z", "recipe:a"), repeats=3)
    reverse = _batch(("recipe:a", "recipe:z"), repeats=3)

    assert forward.recipe_ids == reverse.recipe_ids == ("recipe:a", "recipe:z")
    assert forward.batch_fingerprint == reverse.batch_fingerprint
    assert forward.to_domain_v1().recipe_ids == forward.recipe_ids
    assert all(
        token.isascii() for token in forward.to_domain_v1().to_search_space()["recipe_id"].vals
    )

    changed_fidelity = _batch(("recipe:a", "recipe:z"), repeats=4)
    assert changed_fidelity.batch_fingerprint != forward.batch_fingerprint

    with pytest.raises(ValueError, match="stage-zero recipes must not declare parents"):
        _batch(("recipe:a",), parent_ids={"recipe:a": ("parent",)})


def test_evaluation_budget_returns_partial_frontier_and_resume_reuses_measurements():
    batch = _batch(("baseline", "candidate-a", "candidate-b", "candidate-c"), repeats=2)
    contract = _contract(("latency_ms", "min"))
    first_calls = []

    def first_objective(request):
        first_calls.append(request.measurement_key)
        values = {
            "baseline": 10.0,
            "candidate-a": 8.0,
            "candidate-b": 7.0,
            "candidate-c": 6.0,
        }
        return _outcome(request, {"latency_ms": values[request.recipe_id]})

    first = _session(
        objective_function=first_objective,
        baseline_recipe_id="baseline",
        target_contract=contract,
        budget=_budget(3),
        deterministic_seed=19,
    )
    partial = first.submit_batch(batch)

    assert partial.termination_reason == "evaluation_budget_exhausted"
    assert first.evaluation_count == 3
    assert len(first_calls) == 3
    assert partial.get_results()[0]["recipe_id"] == "baseline"
    assert partial.get_results()[0]["complete"] is True

    encoded = first.checkpoint().model_dump_json()
    checkpoint = ForgeOpaqueSearchCheckpointV2.model_validate_json(encoded)
    resumed_calls = []

    def resumed_objective(request):
        resumed_calls.append(request.measurement_key)
        values = {
            "baseline": 10.0,
            "candidate-a": 8.0,
            "candidate-b": 7.0,
            "candidate-c": 6.0,
        }
        return _outcome(request, {"latency_ms": values[request.recipe_id]})

    resumed = _session(
        objective_function=resumed_objective,
        baseline_recipe_id="baseline",
        target_contract=contract,
        budget=_budget(8),
        deterministic_seed=19,
        checkpoint=checkpoint,
    )
    complete = resumed.submit_batch(batch)

    assert complete.termination_reason == "batch_complete"
    assert resumed.evaluation_count == 8
    assert len(resumed_calls) == 5
    assert not set(first_calls).intersection(resumed_calls)
    assert all(item["complete"] for item in complete.get_results())


def test_two_fidelity_pareto_racing_retains_baseline_and_exact_survivor_lineage():
    stage0 = _batch(
        ("baseline", "low-latency", "low-memory", "dominated"),
        repeats=3,
    )
    contract = _contract(("latency_ms", "min"), ("memory_mib", "min"))
    measurements = {
        "baseline": (10.0, 100.0),
        "low-latency": (5.0, 120.0),
        "low-memory": (7.0, 80.0),
        "dominated": (12.0, 140.0),
    }

    def objective(request):
        latency, memory = measurements[request.recipe_id]
        jitter = (-0.1, 0.0, 0.1)[request.observation_index % 3]
        return _outcome(
            request,
            {"latency_ms": latency + jitter, "memory_mib": memory},
            memory=int(memory * 1024 * 1024),
        )

    session = _session(
        objective_function=objective,
        baseline_recipe_id="baseline",
        target_contract=contract,
        budget=_budget(32, memory=256 << 20),
        deterministic_seed=7,
        halving_factor=2,
    )
    screened = session.submit_batch(stage0)
    survivors = screened.survivor_lineage()[0]["survivor_recipe_ids"]

    assert set(survivors) == {"baseline", "low-latency", "low-memory"}

    stage1 = _batch(
        survivors,
        stage_index=1,
        parent=stage0,
        parent_ids={recipe_id: (recipe_id,) for recipe_id in survivors},
        fidelity_name="full",
        fidelity_ordinal=1,
        repeats=2,
    )
    final = session.submit_batch(stage1)

    assert session.evaluation_count == 18
    assert len(final.survivor_lineage()) == 2
    assert final.survivor_lineage()[1]["batch_fingerprint"] == stage1.batch_fingerprint
    assert {item["recipe_id"] for item in final.pareto_front()} == {
        "low-latency",
        "low-memory",
    }


def test_dynamic_recipe_stage_terminal_finalization_and_resume_are_deterministic():
    stage0 = _batch(("baseline", "single-a", "single-b"))
    values = {
        "baseline": 10.0,
        "single-a": 8.0,
        "single-b": 12.0,
        "combined": 5.0,
    }

    def objective(request):
        return _outcome(request, {"latency_ms": values[request.recipe_id]})

    initial = _session(
        objective_function=objective,
        baseline_recipe_id="baseline",
        target_contract=_contract(("latency_ms", "min")),
        budget=_budget(3),
        deterministic_seed=41,
    )
    screened = initial.submit_batch(stage0)
    assert screened.status.terminal_state == "active"
    assert screened.status.decision_status == "incomplete_evidence"
    assert set(screened.survivor_lineage()[0]["survivor_recipe_ids"]) == {
        "baseline",
        "single-a",
    }
    checkpoint = ForgeOpaqueSearchCheckpointV2.model_validate_json(
        initial.checkpoint().model_dump_json()
    )

    resumed = _session(
        objective_function=objective,
        baseline_recipe_id="baseline",
        target_contract=_contract(("latency_ms", "min")),
        budget=_budget(8),
        deterministic_seed=41,
        checkpoint=checkpoint,
    )
    stage1 = _batch(
        ("baseline", "combined"),
        stage_index=1,
        parent=stage0,
        parent_ids={
            "baseline": ("baseline",),
            "combined": ("single-a",),
        },
        fidelity_name="production-shape",
        fidelity_ordinal=1,
        terminal=True,
    )
    resumed.submit_batch(stage1)
    final = resumed.finalize(
        ForgeOpaqueSearchFinalizationV1(
            generation_status="strategy_complete",
            terminal_fidelity_status="complete",
            reason="frontier_exhausted",
        )
    )

    assert final.status.terminal_state == "complete"
    assert final.status.decision_status == "selected"
    assert final.get_best_result()["recipe_id"] == "combined"
    assert resumed.evaluation_count == 5
    assert len({record.request.measurement_key for record in resumed.observations}) == 5
    assert final.survivor_lineage()[1]["survivor_recipe_ids"] == (
        "baseline",
        "combined",
    )


def test_physical_dedup_uses_bytewise_minimum_recipe_representative():
    planned = _batch(
        ("z-baseline", "a-alias", "other"),
        planned_ids={
            "z-baseline": "planned:same",
            "a-alias": "planned:same",
        },
    )
    calls = []

    def objective(request):
        calls.append(request.recipe_id)
        materialized = (
            "materialized:same"
            if request.recipe_id in ("a-alias", "other")
            else f"materialized:{request.recipe_id}"
        )
        return _outcome(
            request,
            {"latency_ms": 1.0},
            materialized_id=materialized,
            planned_id=planned.recipe(request.recipe_id).planned_physical_id,
        )

    session = _session(
        objective_function=objective,
        baseline_recipe_id="z-baseline",
        target_contract=_contract(("latency_ms", "min")),
        budget=_budget(8),
    )
    result = session.submit_batch(planned)
    aliases = {
        (item.kind, item.alias_recipe_id): item.representative_recipe_id
        for item in result.checkpoint().stages[0].physical_duplicates
    }

    assert calls == ["a-alias", "other"]
    assert aliases[("planned", "z-baseline")] == "a-alias"
    assert aliases[("materialized", "other")] == "a-alias"
    assert tuple(item["recipe_id"] for item in result.get_results()) == ("a-alias",)
    assert result.status.baseline_status == "available"


def test_checkpoint_rejects_evaluation_or_generation_domain_drift():
    batch = _batch(("baseline",))
    contract = _contract(("latency_ms", "min"))
    session = _session(
        objective_function=lambda request: _outcome(request, {"latency_ms": 1.0}),
        baseline_recipe_id="baseline",
        target_contract=contract,
        budget=_budget(1),
    )
    session.submit_batch(batch)
    checkpoint = session.checkpoint()

    with pytest.raises(ValueError, match="different search session contract"):
        _session(
            objective_function=lambda request: _outcome(request, {"latency_ms": 1.0}),
            baseline_recipe_id="baseline",
            target_contract=contract,
            budget=_budget(2),
            checkpoint=checkpoint,
            evaluation_context=_context(evaluation_contract_id="evaluation:changed"),
        )
    with pytest.raises(ValueError, match="different search session contract"):
        _session(
            objective_function=lambda request: _outcome(request, {"latency_ms": 1.0}),
            baseline_recipe_id="baseline",
            target_contract=contract,
            budget=_budget(2),
            checkpoint=checkpoint,
            dynamic_domain=_domain(generation_domain_id="semantic:changed"),
        )


def test_recipe_physical_identity_cannot_change_at_terminal_fidelity():
    stage0 = _batch(("baseline", "candidate"))

    def objective(request):
        materialized = f"materialized:{request.recipe_id}"
        if request.recipe_id == "candidate" and request.fidelity_name == "terminal":
            materialized += ":changed"
        return _outcome(
            request,
            {"latency_ms": 1.0},
            materialized_id=materialized,
        )

    session = _session(
        objective_function=objective,
        baseline_recipe_id="baseline",
        target_contract=_contract(("latency_ms", "min")),
        budget=_budget(8),
        minimum_survivors=2,
    )
    screened = session.submit_batch(stage0)
    survivors = screened.survivor_lineage()[0]["survivor_recipe_ids"]
    stage1 = _batch(
        survivors,
        stage_index=1,
        parent=stage0,
        parent_ids={recipe_id: (recipe_id,) for recipe_id in survivors},
        fidelity_name="terminal",
        fidelity_ordinal=1,
        terminal=True,
    )
    result = session.submit_batch(stage1)
    candidate = next(item for item in result.get_results() if item["recipe_id"] == "candidate")

    assert candidate["feasible"] is False
    assert candidate["failures"][0]["failure"]["code"] == "physical_identity_drift"


def test_memory_preflight_and_identity_drift_are_not_scores():
    batch = _batch(
        ("baseline", "too-large", "drifts"),
        repeats=2,
        estimates={"too-large": 257},
    )
    contract = _contract(
        ("latency_ms", "min"),
        constraints=(
            ForgeOpaqueConstraintV1(
                metric="latency_ms",
                relation="<=",
                bound=20.0,
            ),
        ),
    )
    called = []

    def objective(request):
        called.append(request.recipe_id)
        identity = None
        if request.recipe_id == "drifts":
            identity = f"materialized:drifts:{request.observation_index}"
        return _outcome(
            request,
            {"latency_ms": 10.0},
            memory=64,
            materialized_id=identity,
        )

    session = _session(
        objective_function=objective,
        baseline_recipe_id="baseline",
        target_contract=contract,
        budget=_budget(16, memory=256),
    )
    result = session.submit_batch(batch)
    by_recipe = {item["recipe_id"]: item for item in result.get_results()}

    assert "too-large" not in called
    assert session.evaluation_count == 4
    assert by_recipe["baseline"]["feasible"] is True
    assert by_recipe["too-large"]["failures"][0]["failure"]["category"] == "budget"
    assert by_recipe["drifts"]["feasible"] is False
    assert by_recipe["drifts"]["failures"][0]["failure"]["code"] == ("physical_identity_drift")


def test_unknown_objective_cleanup_poison_stops_future_evaluation():
    batch = _batch(("baseline", "poison"))

    def objective(request):
        if request.recipe_id == "poison":
            raise RuntimeError("objective lost resource ownership")
        return _outcome(request, {"latency_ms": 10.0})

    session = _session(
        objective_function=objective,
        baseline_recipe_id="baseline",
        target_contract=_contract(("latency_ms", "min")),
        budget=_budget(8),
    )
    result = session.submit_batch(batch)

    assert result.status.terminal_state == "poisoned"
    assert result.termination_reason == "poisoned"
    with pytest.raises(RuntimeError, match="poisoned"):
        session.submit_batch(batch)


def test_time_budget_stops_before_next_recipe_without_converting_timeout_to_score():
    batch = _batch(("baseline", "candidate"), repeats=1)
    contract = _contract(("latency_ms", "min"))

    class Clock:
        value = 0.0

        def __call__(self):
            return self.value

    clock = Clock()

    def objective(request):
        clock.value += 2.0
        return _outcome(request, {"latency_ms": 1.0})

    session = _session(
        objective_function=objective,
        baseline_recipe_id="baseline",
        target_contract=contract,
        budget=_budget(8, seconds=1.0),
        clock=clock,
    )
    result = session.submit_batch(batch)

    assert session.evaluation_count == 1
    assert result.termination_reason == "time_budget_exhausted"
    assert [item["recipe_id"] for item in result.get_results()] == ["baseline"]


def test_v1_and_v2_return_same_pareto_front_for_one_complete_frozen_batch():
    recipe_ids = ("baseline", "fast", "small", "dominated")
    contract = _contract(("latency_ms", "min"), ("memory_mib", "min"))
    measurements = {
        "baseline": {"latency_ms": 10.0, "memory_mib": 100.0},
        "fast": {"latency_ms": 5.0, "memory_mib": 120.0},
        "small": {"latency_ms": 7.0, "memory_mib": 80.0},
        "dominated": {"latency_ms": 12.0, "memory_mib": 130.0},
    }
    capability = forge_recipe_search_capability().as_dict()
    domain = OpaqueRecipeDomainV1(
        provider_namespace="taichi_forge.graph.complete_recipe",
        domain_version="complete-graph-recipe.v2",
        provider_semantic_fingerprint="semantic:graph-a",
        compileiq_capability_id=capability["capability_id"],
        compileiq_core_commit=capability["core_commit"],
        compileiq_core_lock=capability["core_lock"],
        recipe_ids=recipe_ids,
    )
    v1 = ForgeOpaqueRecipeExhaustiveSearchV1(
        objective_function=lambda params: measurements[params["recipe_id"]],
        search_space=domain,
        baseline_recipe_id="baseline",
        target_contract=contract,
    ).start()

    batch = _batch(recipe_ids)
    v2_session = _session(
        objective_function=lambda request: _outcome(
            request,
            measurements[request.recipe_id],
        ),
        baseline_recipe_id="baseline",
        target_contract=contract,
        budget=_budget(len(recipe_ids)),
    )
    v2 = v2_session.submit_batch(batch)

    assert {item["params"]["recipe_id"] for item in v1.pareto_front()} == {
        item["recipe_id"] for item in v2.pareto_front()
    }


def test_stage_chain_rejects_non_survivor_parent_and_checkpoint_contract_drift():
    stage0 = _batch(("baseline", "candidate"))
    contract = _contract(("latency_ms", "min"))
    session = _session(
        objective_function=lambda request: _outcome(
            request,
            {"latency_ms": 1.0 if request.recipe_id == "candidate" else 2.0},
        ),
        baseline_recipe_id="baseline",
        target_contract=contract,
        budget=_budget(4),
    )
    session.submit_batch(stage0)
    invalid_stage = _batch(
        ("baseline", "new"),
        stage_index=1,
        parent=stage0,
        parent_ids={"baseline": ("baseline",), "new": ("not-a-survivor",)},
        fidelity_name="full",
        fidelity_ordinal=1,
    )

    with pytest.raises(ValueError, match="non-surviving parent"):
        session.submit_batch(invalid_stage)

    payload = json.loads(session.checkpoint().model_dump_json())
    payload["session_fingerprint"] = "ciq-forge-session-v2:" + "0" * 64
    tampered = ForgeOpaqueSearchCheckpointV2.model_validate(payload)
    with pytest.raises(ValueError, match="different search session contract"):
        _session(
            objective_function=lambda request: _outcome(
                request,
                {"latency_ms": 1.0},
            ),
            baseline_recipe_id="baseline",
            target_contract=contract,
            budget=_budget(8),
            checkpoint=tampered,
        )
