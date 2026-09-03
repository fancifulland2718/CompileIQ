import json
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from compileiq.ciq import Search
from compileiq.core.core_types import ParameterSet, SingleCandidate
from compileiq.forge_support import forge_recipe_search_capability
from compileiq.recipes import (
    OPAQUE_RECIPE_DOMAIN_FINGERPRINT_KEY,
    OPAQUE_RECIPE_ID_KEY,
    OPAQUE_RECIPE_SELECTION_AUDIT_SCHEMA,
    OpaqueDynamicRecipeDomainV2,
    OpaqueRecipeDomainV1,
)
from compileiq.search_spaces.models import ChoiceParamConfig, LiteralParamConfig
from compileiq.types import SearchConfiguration
from compileiq.utils.helpers import _encode_for_core


def _domain(**overrides):
    capability = forge_recipe_search_capability().as_dict()
    fields = {
        "provider_namespace": "forge.taichi",
        "domain_version": "2026.08",
        "provider_semantic_fingerprint": "sha256:abc123",
        "compileiq_capability_id": capability["capability_id"],
        "compileiq_core_commit": capability["core_commit"],
        "compileiq_core_lock": capability["core_lock"],
        "recipe_ids": ("recipe:zeta", "recipe:alpha"),
    }
    fields.update(overrides)
    return OpaqueRecipeDomainV1(**fields)


def _dynamic_domain(**overrides):
    capability = forge_recipe_search_capability().as_dict()
    fields = {
        "provider_namespace": "forge.taichi.graph",
        "domain_version": "complete-recipes.v2",
        "generation_domain_id": "generation:graph-a",
        "provider_registry_id": "providers:graph-a",
        "assembly_protocols": (
            "provider_owned_whole_graph.v1",
            "taichi_forge.runtime_graph_recipe.v1",
        ),
        "recipe_schema": "taichi_forge.complete_graph_recipe.v2",
        "search_strategy_id": "exact-if-bounded-else-staged.v1",
        "compileiq_capability_id": capability["capability_id"],
        "compileiq_core_commit": capability["core_commit"],
        "compileiq_core_lock": capability["core_lock"],
    }
    fields.update(overrides)
    return OpaqueDynamicRecipeDomainV2(**fields)


def test_dynamic_domain_identity_is_stable_without_a_recipe_id_snapshot():
    forward = _dynamic_domain(
        assembly_protocols=(
            "taichi_forge.runtime_graph_recipe.v1",
            "provider_owned_whole_graph.v1",
        )
    )
    reverse = _dynamic_domain()

    assert forward == reverse
    assert forward.domain_fingerprint == reverse.domain_fingerprint
    assert "recipe_ids" not in forward.model_dump(by_alias=True)
    assert forward.domain_fingerprint.startswith("ciq-dynamic-domain-v2:")


@pytest.mark.parametrize(
    "field,value",
    [
        ("generation_domain_id", "generation:graph-b"),
        ("provider_registry_id", "providers:graph-b"),
        ("search_strategy_id", "different-strategy.v1"),
        ("recipe_schema", "different-recipe.v1"),
    ],
)
def test_dynamic_domain_fingerprint_binds_generation_contract(field, value):
    assert _dynamic_domain(**{field: value}).domain_fingerprint != (
        _dynamic_domain().domain_fingerprint
    )


def _make_search(mocker, mock_socket_listen, tmp_path, domain=None, objective=None):
    mocker.patch("compileiq.worker.multiprocessing.Manager", return_value=MagicMock())
    return Search(
        objective_function=objective or (lambda _: 1.0),
        search_space=domain or _domain(),
        search_config=SearchConfiguration(generations=1, pool_size=6),
        cache_folder=tmp_path,
        disable_progress_bar=True,
    )


def _parameter_set(candidate):
    encoded = {_encode_for_core(key): value for key, value in candidate.items()}
    return ParameterSet(
        params=[SingleCandidate(id=1, knobs=json.dumps(encoded))],
        invocation_id=2,
        generation_num=0,
    )


def test_opaque_recipe_domain_v1_has_stable_golden_fingerprint():
    domain = _domain()

    assert domain.recipe_ids == ("recipe:alpha", "recipe:zeta")
    assert domain.domain_fingerprint == (
        "ciq-domain-v1:f9bf45b9c26a1f465e632c6c147c08cf7da93ffca160d64b76c7655858b5adfd"
    )
    assert domain.model_dump(by_alias=True)["schema"] == domain.SCHEMA


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider_namespace": "forge.other"},
        {"domain_version": "2026.09"},
        {"provider_semantic_fingerprint": "sha256:def456"},
        {"compileiq_capability_id": "ciq-forge-cap-v1:" + "0" * 64},
        {"compileiq_core_commit": "different-core"},
        {"compileiq_core_lock": "sha256:" + "0" * 64},
        {"recipe_ids": ("recipe:zeta", "recipe:beta")},
    ],
)
def test_every_provider_domain_identity_field_changes_fingerprint(overrides):
    assert _domain(**overrides).domain_fingerprint != _domain().domain_fingerprint


def test_recipe_input_order_does_not_change_domain_or_choice_order():
    forward = _domain(recipe_ids=("opaque:b", "opaque:a"))
    reverse = _domain(recipe_ids=("opaque:a", "opaque:b"))

    assert forward.recipe_ids == reverse.recipe_ids == ("opaque:a", "opaque:b")
    assert forward.domain_fingerprint == reverse.domain_fingerprint
    search_space = forward.to_search_space()
    assert search_space[OPAQUE_RECIPE_ID_KEY].vals == [
        "ciq-recipe-v1-0000",
        "ciq-recipe-v1-0001",
    ]


def test_recipe_identifiers_are_opaque_text_not_parsed():
    recipe_ids = ("  ", '{"not":"parsed"}', "provider://recipe?x=1", "火山/recipe")
    domain = _domain(recipe_ids=recipe_ids)

    assert set(domain.recipe_ids) == set(recipe_ids)


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"recipe_ids": ()}, "at least one"),
        ({"recipe_ids": ("same", "same")}, "duplicate"),
        ({"recipe_ids": tuple(str(i) for i in range(4097))}, "4096 item limit"),
        ({"provider_namespace": ""}, "must not be empty"),
        ({"domain_version": "x" * 4097}, "4096 byte limit"),
        ({"recipe_ids": ("x" * 4097,)}, "4096 byte limit"),
    ],
)
def test_recipe_domain_rejects_empty_duplicate_and_oversized_fields(overrides, match):
    with pytest.raises(ValidationError, match=match):
        _domain(**overrides)


def test_recipe_domain_rejects_aggregate_over_budget():
    recipe_ids = tuple(("x" * 4090) + f"{i:04d}" for i in range(1025))

    with pytest.raises(ValidationError, match="4194304 byte limit"):
        _domain(recipe_ids=recipe_ids)


def test_recipe_domain_is_strict_frozen_and_forbids_extra_fields():
    with pytest.raises(ValidationError):
        _domain(recipe_ids=(1,))
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _domain(unexpected=True)

    domain = _domain()
    with pytest.raises(ValidationError, match="Instance is frozen"):
        domain.domain_version = "changed"


def test_search_compiles_domain_to_existing_literal_and_choice(
    mocker, mock_socket_listen, tmp_path
):
    domain = _domain()
    search = _make_search(mocker, mock_socket_listen, tmp_path, domain)

    assert search._opaque_recipe_domain is domain
    assert isinstance(search.search_space, dict)
    fingerprint = search.search_space[OPAQUE_RECIPE_DOMAIN_FINGERPRINT_KEY]
    recipe_id = search.search_space[OPAQUE_RECIPE_ID_KEY]
    assert isinstance(fingerprint, LiteralParamConfig)
    assert fingerprint.value == domain.domain_fingerprint
    assert isinstance(recipe_id, ChoiceParamConfig)
    assert recipe_id.vals == ["ciq-recipe-v1-0000", "ciq-recipe-v1-0001"]
    assert all(token.isascii() for token in recipe_id.vals)
    assert not set(recipe_id.vals) & set(domain.recipe_ids)
    assert search.opaque_recipe_capability["capability_id"] == (domain.compileiq_capability_id)


def test_search_rejects_opaque_domain_bound_to_another_compileiq_core(
    mocker, mock_socket_listen, tmp_path
):
    domain = _domain(compileiq_core_commit="different-core")

    with pytest.raises(RuntimeError, match="exact modified CompileIQ"):
        _make_search(mocker, mock_socket_listen, tmp_path, domain)


def test_search_accepts_exact_domain_candidate(mocker, mock_socket_listen, tmp_path):
    domain = _domain()
    search = _make_search(mocker, mock_socket_listen, tmp_path, domain)
    candidate = {
        OPAQUE_RECIPE_DOMAIN_FINGERPRINT_KEY: domain.domain_fingerprint,
        OPAQUE_RECIPE_ID_KEY: "ciq-recipe-v1-0000",
    }

    assert search._load_params(_parameter_set(candidate)) == [
        {
            OPAQUE_RECIPE_DOMAIN_FINGERPRINT_KEY: domain.domain_fingerprint,
            OPAQUE_RECIPE_ID_KEY: "recipe:alpha",
        }
    ]
    assert search.opaque_recipe_audit_records == (
        {
            "param_id": 1,
            "schema": OPAQUE_RECIPE_SELECTION_AUDIT_SCHEMA,
            "provider_namespace": domain.provider_namespace,
            "domain_version": domain.domain_version,
            "provider_semantic_fingerprint": domain.provider_semantic_fingerprint,
            "compileiq_capability_id": domain.compileiq_capability_id,
            "compileiq_core_commit": domain.compileiq_core_commit,
            "compileiq_core_lock": domain.compileiq_core_lock,
            "domain_fingerprint": domain.domain_fingerprint,
            "core_recipe_token": "ciq-recipe-v1-0000",
            "recipe_id": "recipe:alpha",
        },
    )


@pytest.mark.parametrize(
    "candidate,match",
    [
        (
            {
                "domain_fingerprint": "ciq-domain-v1:" + ("0" * 64),
                "recipe_id": "ciq-recipe-v1-0000",
            },
            "fingerprint mismatch",
        ),
        (
            {"domain_fingerprint": None, "recipe_id": "ciq-recipe-v1-0000"},
            "fingerprint mismatch",
        ),
        (
            {"domain_fingerprint": "valid", "recipe_id": "ciq-recipe-v1-9999"},
            "recipe token is outside the domain",
        ),
        (
            {"domain_fingerprint": "valid", "recipe_id": "recipe:alpha"},
            "recipe token is outside the domain",
        ),
        (
            {"domain_fingerprint": "valid", "recipe_id": "ciq-recipe-v1-000A"},
            "recipe token is outside the domain",
        ),
        (
            {"domain_fingerprint": "valid", "recipe_id": 0},
            "recipe token is outside the domain",
        ),
        ({"recipe_id": "ciq-recipe-v1-0000"}, "missing or unexpected fields"),
        (
            {
                "domain_fingerprint": "valid",
                "recipe_id": "ciq-recipe-v1-0000",
                "extra": True,
            },
            "missing or unexpected fields",
        ),
    ],
)
def test_candidate_domain_is_revalidated_before_objective(
    mocker, mock_socket_listen, tmp_path, candidate, match
):
    domain = _domain()
    if candidate.get("domain_fingerprint") == "valid":
        candidate["domain_fingerprint"] = domain.domain_fingerprint
    search = _make_search(mocker, mock_socket_listen, tmp_path, domain)

    with pytest.raises(RuntimeError, match=match):
        search._load_params(_parameter_set(candidate))


def test_invalid_domain_candidate_never_reaches_worker_or_objective(
    mocker, mock_socket_listen, tmp_path
):
    domain = _domain()
    objective = MagicMock(return_value=1.0)
    search = _make_search(mocker, mock_socket_listen, tmp_path, domain, objective)
    candidate = {
        OPAQUE_RECIPE_DOMAIN_FINGERPRINT_KEY: "ciq-domain-v1:" + ("0" * 64),
        OPAQUE_RECIPE_ID_KEY: "ciq-recipe-v1-0000",
    }
    mocker.patch(
        "compileiq.ciq.CoreIPC.receive_from_core",
        return_value=_parameter_set(candidate),
    )
    worker_run = mocker.patch.object(search._worker, "run")
    search._core_socket = MagicMock()

    with pytest.raises(RuntimeError, match="domain fingerprint mismatch"):
        search._process_candidates(num_workers=1)

    worker_run.assert_not_called()
    objective.assert_not_called()
