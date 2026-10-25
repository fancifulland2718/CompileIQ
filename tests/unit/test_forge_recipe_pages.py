import pytest

from compileiq.forge_search_v2 import ForgeOpaqueSearchCheckpointV2, ForgeOpaqueSearchFinalizationV1
from compileiq.recipes import OpaqueRecipeBatchV2
from tests.unit.test_forge_search_v2 import _batch, _budget, _contract, _outcome, _session


def page(ids, index, *, parent=None, final=False, plans=None):
    raw = _batch(ids, fidelity_name="full", terminal=True, planned_ids=plans).model_dump(
        by_alias=True
    )
    raw.update(
        stage_index=index,
        parent_batch_fingerprint=None if parent is None else parent.batch_fingerprint,
        page_group_fingerprint="logical-stage:first",
        page_index=index,
        page_final=final,
    )
    return OpaqueRecipeBatchV2(**raw)


def finish(session):
    return session.finalize(
        ForgeOpaqueSearchFinalizationV1(
            generation_status="exhaustive",
            terminal_fidelity_status="complete",
            reason="pages_complete",
        )
    )


def test_pages_compare_earlier_winners_and_reuse_baseline_observation():
    calls = []

    def objective(request):
        calls.append(request.recipe_id)
        return _outcome(request, {"time": {"base": 10, "early": 1, "late": 3}[request.recipe_id]})

    session = _session(
        objective_function=objective,
        baseline_recipe_id="base",
        target_contract=_contract(("time", "min")),
        budget=_budget(3),
    )
    first = page(("base", "early"), 0)
    result = session.submit_batch(first)
    assert result.status.terminal_fidelity_status == "partial"
    with pytest.raises(ValueError, match="contradicts"):
        finish(session)
    second = page(("base", "late"), 1, parent=first, final=True)
    session.submit_batch(second)
    result = finish(session)
    assert result.get_best_result()["recipe_id"] == "early"
    assert sorted(calls) == ["base", "early", "late"]
    assert {item["recipe_id"] for item in result.get_results()} == set(calls)
    report = result.report()
    assert {item.recipe_id for item in report.baseline_comparison} == {"base", "early", "late"}
    assert report.pareto_front[0].recipe_id == "early"
    assert report.stages[0].page_final is False
    assert report.stages[1].page_final is True
    assert not any(item.code == "incomplete_candidates" for item in report.warnings)


def test_partial_page_resumes_without_repeating_completed_measurements():
    calls = []

    def objective(request):
        calls.append(request.recipe_id)
        return _outcome(request, {"time": float(len(request.recipe_id))})

    options = dict(
        objective_function=objective,
        baseline_recipe_id="base",
        target_contract=_contract(("time", "min")),
    )
    session = _session(**options, budget=_budget(3))
    first = page(("base", "a"), 0)
    second = page(("base", "long", "longer"), 1, parent=first, final=True)
    session.submit_batch(first)
    session.submit_batch(second)
    saved = ForgeOpaqueSearchCheckpointV2.model_validate_json(
        session.checkpoint().model_dump_json()
    )
    assert saved.status.evaluation_status == "partial"
    resumed = _session(**options, budget=_budget(4), checkpoint=saved)
    resumed.submit_batch(second)
    assert finish(resumed).get_best_result()["recipe_id"] == "a"
    assert len(calls) == len(set(calls)) == 4
    damaged = saved.model_dump(by_alias=True)
    damaged["batches"] = damaged["batches"][1:]
    with pytest.raises(ValueError, match="first submitted batch|inconsistent batch/stage"):
        _session(**options, budget=_budget(4), checkpoint=damaged)


def test_page_chain_rejects_drift_and_duplicate_recipes():
    session = _session(
        objective_function=lambda request: _outcome(request, {"time": 1.0}),
        baseline_recipe_id="base",
        target_contract=_contract(("time", "min")),
        budget=_budget(10),
    )
    first = page(("base", "a"), 0)
    session.submit_batch(first)
    second = page(("base", "b"), 1, parent=first, final=True)
    for changed in (
        dict(page_index=2),
        dict(page_group_fingerprint="other"),
        dict(fidelity=second.fidelity.model_copy(update={"repeat_count": 2})),
    ):
        with pytest.raises(ValueError, match="continuity|fidelity"):
            session.submit_batch(second.model_copy(update=changed))
    with pytest.raises(ValueError, match="more than one page"):
        session.submit_batch(page(("base", "a"), 1, parent=first, final=True))
    session.submit_batch(second)
    assert finish(session).status.evaluation_status == "complete"


def test_alias_on_later_page_does_not_force_another_measurement():
    calls = []

    def objective(request):
        calls.append(request.recipe_id)
        return _outcome(
            request, {"time": 1.0}, planned_id="shared" if request.recipe_id != "base" else None
        )

    session = _session(
        objective_function=objective,
        baseline_recipe_id="base",
        target_contract=_contract(("time", "min")),
        budget=_budget(3),
    )
    first = page(("base", "z"), 0, plans={"z": "shared"})
    second = page(("base", "a"), 1, parent=first, final=True, plans={"a": "shared"})
    session.submit_batch(first)
    session.submit_batch(second)
    result = finish(session)
    assert set(calls) == {"base", "z"}
    assert result.status.evaluation_status == "complete"
    assert any(
        item.alias_recipe_id == "a" and item.representative_recipe_id == "z"
        for item in result.checkpoint().stages[-1].physical_duplicates
    )


def test_protocol_bounds_each_page_not_the_total_domain():
    # Exercise the transport boundary without thousands of measurement trials.
    first = page(("base", *(f"r:{i}" for i in range(4095))), 0)
    second = page(("base", "r:4095", "r:4096"), 1, parent=first, final=True)
    assert len(set(first.recipe_ids) | set(second.recipe_ids)) == 4098
    assert len(first.to_domain_v1().recipe_ids) == 4096
    assert len(second.to_domain_v1().recipe_ids) == 3


def test_pages_keep_lineage_from_previous_logical_stage():
    session = _session(
        objective_function=lambda request: _outcome(request, {"time": 1.0}),
        baseline_recipe_id="base",
        target_contract=_contract(("time", "min")),
        budget=_budget(10),
    )
    screen = _batch(("base", "a"))
    session.submit_batch(screen)
    first = _batch(
        ("base", "a"), stage_index=1, parent=screen,
        parent_ids={"base": ("base",), "a": ("a",)},
        fidelity_name="full", fidelity_ordinal=1, terminal=True,
    ).model_copy(update={"page_group_fingerprint": "terminal", "page_final": False})
    second = _batch(
        ("base", "b"), stage_index=2, parent=first,
        parent_ids={"base": ("base",), "b": ("a",)},
        fidelity_name="full", fidelity_ordinal=1, terminal=True,
    ).model_copy(update={"page_group_fingerprint": "terminal", "page_index": 1})
    session.submit_batch(first)
    session.submit_batch(second)
    assert finish(session).status.evaluation_status == "complete"


def test_incomplete_page_cannot_be_skipped_or_declared_another_stage():
    session = _session(
        objective_function=lambda request: _outcome(request, {"time": 1.0}),
        baseline_recipe_id="base",
        target_contract=_contract(("time", "min")),
        budget=_budget(1),
    )
    first = page(("base", "a"), 0)
    session.submit_batch(first)
    second = page(("base", "b"), 1, parent=first, final=True)
    with pytest.raises(ValueError, match="incomplete recipe page"):
        session.submit_batch(second)
    with pytest.raises(ValueError, match="unfinished recipe page group"):
        session.submit_batch(second.model_copy(update={"page_index": 0}))
