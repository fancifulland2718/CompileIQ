import os
from pathlib import Path

import compileiq.ciq as ciq_module
import compileiq.search_spaces.base as ss
from compileiq.ciq import Search
from compileiq.forge_support import (
    ForgeOpaqueEvaluationContextV1,
    ForgeOpaqueObjectiveV1,
    ForgeOpaqueSearchBudgetV2,
    ForgeOpaqueSearchFinalizationV1,
    ForgeOpaqueSearchSessionV2,
    ForgeOpaqueTargetContractV1,
    OpaqueOptimizationReportV1,
    TrialCleanupV2,
    TrialOutcomeV2,
    forge_recipe_search_capability,
    opaque_optimization_report_json_schema,
)
from compileiq.recipes import (
    OpaqueDynamicRecipeDomainV2,
    OpaqueRecipeBatchV2,
    OpaqueRecipeFidelityV2,
    OpaqueRecipeLineageV2,
)
from compileiq.types import SearchConfiguration


def objective(config):
    return config["x"] ** 2 + config["y"]


def assert_imported_from_wheel():
    workspace = os.environ.get("GITHUB_WORKSPACE")
    if not workspace:
        return

    workspace_path = Path(workspace).resolve()
    ciq_path = Path(ciq_module.__file__).resolve()

    if ciq_path == workspace_path or workspace_path in ciq_path.parents:
        raise AssertionError(f"compileiq.ciq was imported from checkout path: {ciq_path}")


def assert_forge_v2_checkpoint_resume():
    capability = forge_recipe_search_capability().as_dict()
    batch = OpaqueRecipeBatchV2(
        provider_namespace="wheel.smoke",
        domain_version="complete-recipes.v2",
        provider_semantic_fingerprint="wheel-smoke-semantics-v2",
        compileiq_capability_id=capability["capability_id"],
        compileiq_core_commit=capability["core_commit"],
        compileiq_core_lock=capability["core_lock"],
        stage_index=0,
        stage_fingerprint="wheel-smoke-stage-v2",
        fidelity=OpaqueRecipeFidelityV2(
            name="package",
            ordinal=0,
            repeat_count=1,
            terminal=True,
        ),
        recipes=(
            OpaqueRecipeLineageV2(
                recipe_id="baseline",
                planned_physical_id="planned:baseline",
            ),
            OpaqueRecipeLineageV2(
                recipe_id="candidate",
                planned_physical_id="planned:candidate",
            ),
        ),
    )
    domain = OpaqueDynamicRecipeDomainV2(
        provider_namespace="wheel.smoke",
        domain_version="complete-recipes.v2",
        generation_domain_id="wheel-smoke-semantics-v2",
        provider_registry_id="wheel-smoke-provider-registry-v2",
        assembly_protocols=("provider_owned_whole_graph.v1",),
        recipe_schema="wheel-smoke-complete-recipe.v2",
        search_strategy_id="wheel-smoke-exact.v2",
        compileiq_capability_id=capability["capability_id"],
        compileiq_core_commit=capability["core_commit"],
        compileiq_core_lock=capability["core_lock"],
    )
    evaluation_context = ForgeOpaqueEvaluationContextV1(
        reuse_scope="portable",
        workload_context_id="wheel-smoke-workload-v1",
        evaluation_contract_id="wheel-smoke-evaluator-v1",
        backend_environment_id="wheel-smoke-environment-v1",
    )
    target = ForgeOpaqueTargetContractV1(
        objectives=(ForgeOpaqueObjectiveV1(name="score", direction="min"),)
    )
    observed = []

    def evaluate(request):
        observed.append(request.measurement_key)
        return TrialOutcomeV2(
            metrics={"score": 0.0 if request.recipe_id == "candidate" else 1.0},
            planned_physical_id=f"planned:{request.recipe_id}",
            materialized_physical_id=f"materialized:{request.recipe_id}",
            materialized_memory_bytes=0,
            provenance={"source": "installed-wheel"},
            cleanup=TrialCleanupV2(
                status="complete",
                released_resources=True,
                detail_code="released",
            ),
        )

    partial_session = ForgeOpaqueSearchSessionV2(
        objective_function=evaluate,
        dynamic_domain=domain,
        evaluation_context=evaluation_context,
        baseline_recipe_id="baseline",
        target_contract=target,
        budget=ForgeOpaqueSearchBudgetV2(
            evaluation_limit=1,
            time_limit_seconds=30.0,
            materialized_memory_limit_bytes=0,
        ),
        deterministic_seed=17,
    )
    partial = partial_session.submit_batch(batch)
    assert partial.termination_reason == "evaluation_budget_exhausted"

    resumed_session = ForgeOpaqueSearchSessionV2(
        objective_function=evaluate,
        dynamic_domain=domain,
        evaluation_context=evaluation_context,
        baseline_recipe_id="baseline",
        target_contract=target,
        budget=ForgeOpaqueSearchBudgetV2(
            evaluation_limit=2,
            time_limit_seconds=30.0,
            materialized_memory_limit_bytes=0,
        ),
        deterministic_seed=17,
        checkpoint=partial.checkpoint(),
    )
    resumed_session.submit_batch(batch)
    completed = resumed_session.finalize(
        ForgeOpaqueSearchFinalizationV1(
            generation_status="exhaustive",
            terminal_fidelity_status="complete",
            reason="wheel_smoke_complete",
        )
    )
    assert completed.termination_reason == "wheel_smoke_complete"
    assert completed.status.terminal_state == "complete"
    assert completed.get_best_result()["recipe_id"] == "candidate"
    assert len(observed) == len(set(observed)) == 2

    full_report = completed.report(detail="full")
    summary_report = full_report.summary()
    assert full_report.schema_id == "compileiq.opaque-optimization-report.v1"
    assert full_report.report_id == summary_report.report_id
    assert full_report.checkpoint.embedded is True
    assert len(full_report.trials) == 2
    assert summary_report.checkpoint.embedded is False
    assert summary_report.trials == ()
    assert OpaqueOptimizationReportV1.from_json(full_report.to_json()) == full_report
    assert OpaqueOptimizationReportV1.from_dict(summary_report.to_dict()) == (
        summary_report
    )
    assert "candidate" in summary_report.to_markdown()
    schema = opaque_optimization_report_json_schema()
    assert schema["$id"].endswith("opaque-optimization-report-v1.schema.json")
    assert schema["properties"]["schema"]["const"] == (
        "compileiq.opaque-optimization-report.v1"
    )


def main():
    assert_imported_from_wheel()
    assert_forge_v2_checkpoint_resume()

    result = Search(
        objective_function=objective,
        search_space={
            "x": ss.range(start=1.0, end=20.0, step=0.5),
            "y": ss.choice([1, 2, 3]),
        },
        search_config=SearchConfiguration(
            generations=1,
            pool_size=8,
            cull_size=4,
            problem_type="min",
            num_objectives=1,
        ),
        cache_folder=Path.cwd() / "compileiq-wheel-smoke-cache",
        disable_progress_bar=True,
    ).start()

    df = result.get_results()
    best = result.get_best_result()

    assert len(df) > 0
    assert "score_1" in df.columns
    assert "params" in df.columns
    assert isinstance(best, dict)
    assert "score_1" in best
    assert "params" in best


if __name__ == "__main__":
    main()
