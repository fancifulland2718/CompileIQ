"""The optional V1 proposal adapter preserves opaque IDs on the real 1.1 engine."""

import pytest

from compileiq.ciq import Search, SearchSpaceExhaustedWarning
from compileiq.forge_support import ForgeMainThreadWorker, forge_recipe_search_capability
from compileiq.recipes import OpaqueRecipeDomainV1
from compileiq.types import SearchConfiguration


@pytest.mark.requires_amber
def test_amber_opaque_proposals_preserve_audit_and_do_not_repeat(tmp_path):
    capability = forge_recipe_search_capability()
    domain = OpaqueRecipeDomainV1(
        provider_namespace="test.forge",
        domain_version="v1",
        provider_semantic_fingerprint="whole-graph",
        compileiq_capability_id=capability.capability_id,
        compileiq_core_commit=capability.core_commit,
        compileiq_core_lock=capability.core_lock,
        recipe_ids=tuple(f"graph/完整方案/{index}" for index in range(6)),
    )
    seen = []

    def objective(params):
        assert params["domain_fingerprint"] == domain.domain_fingerprint
        recipe = params["recipe_id"]
        assert recipe in domain.recipe_ids
        seen.append(recipe)
        return float(domain.recipe_ids.index(recipe) + 1)

    search = Search(
        objective_function=objective,
        search_space=domain,
        search_config=SearchConfiguration(generations=3, pool_size=6),
        worker_type=ForgeMainThreadWorker,
        cache_folder=tmp_path,
        disable_progress_bar=True,
    )
    with pytest.warns(SearchSpaceExhaustedWarning):
        result = search.start()
    assert set(seen) == set(domain.recipe_ids)
    assert len(seen) == len(domain.recipe_ids)
    assert len(result.get_results()) == len(seen)
    assert {record["recipe_id"] for record in search.opaque_recipe_audit_records} == set(seen)
    assert search.opaque_recipe_core_provenance["proposal_engine"] == "amber"
    assert search.opaque_recipe_core_provenance["execution_engine"] == capability.execution_engine
