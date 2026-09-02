import json
from pathlib import Path
import threading

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

import pytest
from pydantic import ValidationError

from compileiq.core.verify_core import MANIFEST_PATH, load_manifest
from compileiq.forge_support import (
    FORGE_RECIPE_SEARCH_FORK_BUILD_ID,
    FORGE_RECIPE_SEARCH_PACKAGE_VERSION,
    ForgeMainThreadWorker,
    ForgeOpaqueConstraintV1,
    ForgeOpaqueObjectiveV1,
    ForgeOpaqueRecipeExhaustiveSearchV1,
    ForgeOpaqueTargetContractV1,
    ForgeRecipeSearchCapabilityV1,
    forge_recipe_search_capability,
)
from compileiq.recipes import OpaqueRecipeDomainV1


def test_forge_recipe_capability_is_versioned_and_core_locked():
    capability = forge_recipe_search_capability()
    payload = capability.as_dict()

    assert payload["schema"] == "compileiq.taichi-forge-recipe-search-capability.v2"
    assert payload["protocol_revision"] == 4
    assert payload["fork_build_id"] == FORGE_RECIPE_SEARCH_FORK_BUILD_ID
    assert payload["package_version"] == FORGE_RECIPE_SEARCH_PACKAGE_VERSION
    assert payload["opaque_recipe_domain_schema"] == ("compileiq.opaque-recipe-domain.v1")
    assert payload["opaque_recipe_batch_schema"] == ("compileiq.opaque-recipe-batch.v2")
    assert payload["selection_audit_schema"] == ("compileiq.opaque-recipe-selection.v1")
    assert payload["opaque_target_contract_schema"] == (
        "compileiq.taichi-forge-opaque-target-contract.v1"
    )
    assert payload["opaque_target_selection"] == (
        "uncertainty_aware_pareto_layers_no_scalarization_v2"
    )
    assert payload["trial_outcome_schema"] == ("compileiq.taichi-forge-trial-outcome.v2")
    assert payload["search_checkpoint_schema"] == ("compileiq.taichi-forge-search-checkpoint.v2")
    assert payload["max_recipe_ids"] == 4096
    assert payload["provider_recipe_ids_cross_core_boundary"] is False
    assert payload["opaque_domain_binding"] == ("capability_id_core_commit_core_lock")
    assert payload["core_verification"] == (
        "bundled_manifest_lock_and_platform_hashes_at_search_start_no_override"
    )
    assert payload["objective_worker"] == "forge_main_thread_serial_v1"
    assert payload["opaque_recipe_search"] == ("budgeted_staged_pareto_racing_main_thread_v2")
    assert payload["opaque_recipe_search_v1"] == "bounded_exhaustive_main_thread_v1"
    assert payload["fork_build_id"] == ("compileiq-taichi-forge-complete-recipes.v2")
    assert payload["package_version"] == ("1.0.0dev4+taichiforge.recipe2")
    assert payload["core_lock"].startswith("sha256:")
    assert payload["capability_id"].startswith("ciq-forge-cap-v2:")
    assert ForgeRecipeSearchCapabilityV1(**payload) == capability


def test_forge_recipe_capability_fails_closed_on_manifest_drift(tmp_path):
    manifest = load_manifest(MANIFEST_PATH)
    manifest["core_commit"] = "forged"
    manifest_path = tmp_path / "core-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RuntimeError, match="core_lock mismatch"):
        forge_recipe_search_capability(manifest_path)


def test_forge_recipe_capability_identity_cannot_be_relabelled():
    payload = forge_recipe_search_capability().as_dict()
    payload["core_commit"] = "forged"

    with pytest.raises(ValidationError, match="capability identity mismatch"):
        ForgeRecipeSearchCapabilityV1(**payload)


def test_fork_package_version_matches_project_metadata():
    project = Path(__file__).resolve().parents[2] / "pyproject.toml"
    with project.open("rb") as fp:
        metadata = tomllib.load(fp)

    assert metadata["tool"]["poetry"]["version"] == (FORGE_RECIPE_SEARCH_PACKAGE_VERSION)


def test_forge_worker_executes_serially_on_calling_thread(tmp_path):
    worker = ForgeMainThreadWorker.create(tmp_path, normalize=False, tracker=None)
    calling_thread = threading.get_ident()
    observed = []

    scores = worker.run(
        function=lambda parameters: (
            observed.append((threading.get_ident(), parameters["value"]))
            or float(parameters["value"])
        ),
        params_pool=({"value": 1}, {"value": 2}),
        params_ids=(10, 11),
    )

    assert observed == [(calling_thread, 1), (calling_thread, 2)]
    assert [score.score for score in scores] == [1.0, 2.0]
    assert all("forge_main_thread_serial_v1" in score.metadata for score in scores)


def test_forge_worker_rejects_generic_baseline_normalization(tmp_path):
    with pytest.raises(ValueError, match="generic baseline normalization"):
        ForgeMainThreadWorker.create(tmp_path, normalize=True, tracker=None)


def test_forge_exhaustive_search_observes_every_safe_token_once():
    capability = forge_recipe_search_capability().as_dict()
    domain = OpaqueRecipeDomainV1(
        provider_namespace="forge.test",
        domain_version="complete-plan.v1",
        provider_semantic_fingerprint="semantic:test",
        compileiq_capability_id=capability["capability_id"],
        compileiq_core_commit=capability["core_commit"],
        compileiq_core_lock=capability["core_lock"],
        recipe_ids=("plan:candidate", "plan:baseline", "plan:other"),
    )
    observed = []
    search = ForgeOpaqueRecipeExhaustiveSearchV1(
        objective_function=lambda params: (
            observed.append(params["recipe_id"])
            or float(domain.recipe_ids.index(params["recipe_id"]))
        ),
        search_space=domain,
        baseline_recipe_id="plan:baseline",
    )
    result = search.start()

    assert observed == list(domain.recipe_ids)
    assert len(search.opaque_recipe_audit_records) == len(domain.recipe_ids)
    assert {item["recipe_id"] for item in search.opaque_recipe_audit_records} == set(
        domain.recipe_ids
    )
    assert result.get_best_result()["params"]["recipe_id"] == domain.recipe_ids[0]
    assert sum(item["is_baseline"] for item in result.get_results()) == 1


def _opaque_domain(recipe_ids):
    capability = forge_recipe_search_capability().as_dict()
    return OpaqueRecipeDomainV1(
        provider_namespace="forge.test.targets",
        domain_version="complete-plan.v1",
        provider_semantic_fingerprint="semantic:target-test",
        compileiq_capability_id=capability["capability_id"],
        compileiq_core_commit=capability["core_commit"],
        compileiq_core_lock=capability["core_lock"],
        recipe_ids=recipe_ids,
    )


def test_forge_opaque_target_selects_best_feasible_single_objective():
    domain = _opaque_domain(("baseline", "fast-over-budget", "balanced"))
    measurements = {
        "baseline": {"steady_ms": 10.0, "persistent_vram_mib": 100.0},
        "fast-over-budget": {"steady_ms": 5.0, "persistent_vram_mib": 200.0},
        "balanced": {"steady_ms": 7.0, "persistent_vram_mib": 120.0},
    }
    contract = ForgeOpaqueTargetContractV1(
        objectives=(ForgeOpaqueObjectiveV1(name="steady_ms", direction="min"),),
        constraints=(
            ForgeOpaqueConstraintV1(metric="persistent_vram_mib", relation="<=", bound=150.0),
        ),
    )
    result = ForgeOpaqueRecipeExhaustiveSearchV1(
        objective_function=lambda params: measurements[params["recipe_id"]],
        search_space=domain,
        baseline_recipe_id="baseline",
        target_contract=contract,
    ).start()

    assert result.target_contract == contract.as_dict()
    assert result.get_best_result()["params"]["recipe_id"] == "balanced"
    assert {item["params"]["recipe_id"] for item in result.get_feasible_results()} == {
        "baseline",
        "balanced",
    }
    rejected = next(
        item for item in result.get_results() if item["params"]["recipe_id"] == "fast-over-budget"
    )
    assert rejected["feasible"] is False
    assert rejected["constraint_violations"] == (
        {
            "metric": "persistent_vram_mib",
            "relation": "<=",
            "bound": 150.0,
            "actual": 200.0,
        },
    )


def test_forge_opaque_target_returns_mixed_direction_pareto_without_scalar_winner():
    domain = _opaque_domain(("baseline", "low-latency", "low-memory", "dominated", "bad"))
    measurements = {
        "baseline": {"steady_ms": 10.0, "throughput": 10.0, "vram_mib": 100.0},
        "low-latency": {"steady_ms": 5.0, "throughput": 12.0, "vram_mib": 120.0},
        "low-memory": {"steady_ms": 7.0, "throughput": 15.0, "vram_mib": 80.0},
        "dominated": {"steady_ms": 8.0, "throughput": 9.0, "vram_mib": 90.0},
        "bad": {"steady_ms": 4.0, "throughput": 20.0, "vram_mib": 180.0},
    }
    contract = ForgeOpaqueTargetContractV1(
        objectives=(
            ForgeOpaqueObjectiveV1(name="steady_ms", direction="min"),
            ForgeOpaqueObjectiveV1(name="throughput", direction="max"),
        ),
        constraints=(ForgeOpaqueConstraintV1(metric="vram_mib", relation="<=", bound=150.0),),
    )
    result = ForgeOpaqueRecipeExhaustiveSearchV1(
        objective_function=lambda params: measurements[params["recipe_id"]],
        search_space=domain,
        baseline_recipe_id="baseline",
        target_contract=contract,
    ).start()

    with pytest.raises(ValueError, match="no scalar winner"):
        result.get_best_result()
    assert [item["params"]["recipe_id"] for item in result.pareto_front()] == [
        "low-latency",
        "low-memory",
    ]


@pytest.mark.parametrize(
    ("returned", "message"),
    [
        ({"latency": 1.0}, "metrics mismatch"),
        ({"latency": 1.0, "vram": 2.0, "extra": 3.0}, "metrics mismatch"),
        ({"latency": float("nan"), "vram": 2.0}, "finite numeric"),
        ({"latency": True, "vram": 2.0}, "finite numeric"),
    ],
)
def test_forge_opaque_target_rejects_malformed_observations(returned, message):
    domain = _opaque_domain(("baseline",))
    contract = ForgeOpaqueTargetContractV1(
        objectives=(ForgeOpaqueObjectiveV1(name="latency"),),
        constraints=(ForgeOpaqueConstraintV1(metric="vram", relation="<=", bound=4.0),),
    )
    search = ForgeOpaqueRecipeExhaustiveSearchV1(
        objective_function=lambda _: returned,
        search_space=domain,
        baseline_recipe_id="baseline",
        target_contract=contract,
    )

    with pytest.raises(ValueError, match=message):
        search.start()


def test_forge_opaque_target_rejects_duplicate_objective_names():
    with pytest.raises(ValidationError, match="objective names must be unique"):
        ForgeOpaqueTargetContractV1(
            objectives=(
                ForgeOpaqueObjectiveV1(name="latency"),
                ForgeOpaqueObjectiveV1(name="latency", direction="max"),
            )
        )
