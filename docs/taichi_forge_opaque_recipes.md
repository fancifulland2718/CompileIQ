# Taichi Forge complete-recipe extension

This maintained fork gives Taichi Forge a bounded, provider-neutral scheduler
for complete Graph recipes. Forge owns Graph semantics, candidate generation,
legality, materialization, physical identity, and final product policy.
CompileIQ sees only opaque recipe identities, lineage, fidelity, budgets, and
named measurements.

This is not a second path for ordinary PTXAS/NVCC controls. A raw block size,
workgroup shape, backend route, tile, or single-kernel parameter is not a valid
V2 recipe. Each submitted identity must already describe one complete physical
alternative that Forge can materialize and measure.

## V2 protocol

The current protocol is composed from these public models:

- `OpaqueDynamicRecipeDomainV2` freezes the provider registry, generation
  domain, assembly protocols, recipe schema, search strategy, capability, and
  bundled-core identity for one session.
- `OpaqueRecipeBatchV2` submits one canonical stage. Every entry is an
  `OpaqueRecipeLineageV2` with a complete recipe ID, planned physical ID,
  immediate parents, and optional materialized-memory estimate.
- `OpaqueRecipeFidelityV2` gives each stage a non-decreasing fidelity ordinal,
  repeat count, work scale, and explicit terminal flag.
- `ForgeOpaqueEvaluationContextV1` binds observations to the caller's workload,
  evaluation, and backend-environment identities. `reuse_scope="portable"`
  permits checkpoint reuse only when that entire session contract matches.
- `ForgeOpaqueSearchSessionV2` evaluates batches serially on the caller's main
  thread, keeps the baseline in every stage, enforces evaluation/time/memory
  budgets, and returns deterministic survivor lineage and a Pareto frontier.
- `ForgeOpaqueSearchCheckpointV2` is the only supported resume payload. It
  contains batches, measurement keys, structured outcomes, stage results,
  status, and optional finalization.

Provider recipe identifiers never cross the encrypted core boundary. Each
batch is compiled through the existing `OpaqueRecipeDomainV1` ordinal-token
codec; the Python layer validates the token and restores the provider-owned ID
before the objective runs.

The V1 fixed-domain exhaustive API remains available for compatibility. New
Forge integrations should use V2 because V1 cannot express survivor-driven
batches, fidelity changes, portable checkpoints, or the complete report
contract.

## Minimal staged session

```python
from compileiq.forge_support import (
    ForgeOpaqueEvaluationContextV1,
    ForgeOpaqueObjectiveV1,
    ForgeOpaqueSearchBudgetV2,
    ForgeOpaqueSearchFinalizationV1,
    ForgeOpaqueSearchSessionV2,
    ForgeOpaqueTargetContractV1,
    TrialCleanupV2,
    TrialOutcomeV2,
    forge_recipe_search_capability,
)
from compileiq.recipes import (
    OpaqueDynamicRecipeDomainV2,
    OpaqueRecipeBatchV2,
    OpaqueRecipeFidelityV2,
    OpaqueRecipeLineageV2,
)

capability = forge_recipe_search_capability().as_dict()
domain = OpaqueDynamicRecipeDomainV2(
    provider_namespace="my_forge_provider",
    domain_version="complete-recipes.v1",
    generation_domain_id="semantic-graph-and-provider-fingerprint",
    provider_registry_id="provider-registry-fingerprint",
    assembly_protocols=("provider_owned_whole_graph.v1",),
    recipe_schema="my-complete-graph-recipe.v1",
    search_strategy_id="my-staged-strategy.v1",
    compileiq_capability_id=capability["capability_id"],
    compileiq_core_commit=capability["core_commit"],
    compileiq_core_lock=capability["core_lock"],
)
batch = OpaqueRecipeBatchV2(
    provider_namespace=domain.provider_namespace,
    domain_version=domain.domain_version,
    provider_semantic_fingerprint=domain.generation_domain_id,
    compileiq_capability_id=capability["capability_id"],
    compileiq_core_commit=capability["core_commit"],
    compileiq_core_lock=capability["core_lock"],
    stage_index=0,
    stage_fingerprint="stage-zero-fingerprint",
    fidelity=OpaqueRecipeFidelityV2(
        name="screen",
        ordinal=0,
        repeat_count=3,
        terminal=False,
    ),
    recipes=(
        OpaqueRecipeLineageV2(
            recipe_id="baseline",
            planned_physical_id="planned:baseline",
        ),
        OpaqueRecipeLineageV2(
            recipe_id="candidate-a",
            planned_physical_id="planned:candidate-a",
        ),
    ),
)

def evaluate(request):
    metrics, physical_id, peak_bytes = measure_complete_recipe(request.recipe_id)
    return TrialOutcomeV2(
        metrics=metrics,
        planned_physical_id=batch.recipe(request.recipe_id).planned_physical_id,
        materialized_physical_id=physical_id,
        materialized_memory_bytes=peak_bytes,
        provenance={"source": "application-evaluator"},
        cleanup=TrialCleanupV2(
            status="complete",
            released_resources=True,
            detail_code="released",
        ),
    )

session = ForgeOpaqueSearchSessionV2(
    objective_function=evaluate,
    dynamic_domain=domain,
    evaluation_context=ForgeOpaqueEvaluationContextV1(
        reuse_scope="portable",
        workload_context_id="workload-fingerprint",
        evaluation_contract_id="measurement-contract-fingerprint",
        backend_environment_id="driver-device-runtime-fingerprint",
    ),
    baseline_recipe_id="baseline",
    target_contract=ForgeOpaqueTargetContractV1(
        objectives=(
            ForgeOpaqueObjectiveV1(name="device_time_ns", direction="min"),
        ),
    ),
    budget=ForgeOpaqueSearchBudgetV2(
        evaluation_limit=12,
        time_limit_seconds=300.0,
        materialized_memory_limit_bytes=1 << 30,
    ),
    deterministic_seed=17,
)
partial = session.submit_batch(batch)
checkpoint = partial.checkpoint()
```

Forge, rather than CompileIQ, decides which survivor combinations or
provider-owned neighbours form the next complete batch. A later stage names
the previous `batch_fingerprint`, declares an immediate parent for every
recipe, and uses a strictly increasing stage index and non-decreasing fidelity
ordinal. Every stage retains the same baseline recipe.

When provider generation is complete, the caller seals the evidence with
`ForgeOpaqueSearchFinalizationV1`. An evaluation/time budget interruption is
left unfinalized so the checkpoint can resume missing measurement keys without
repeating existing observations.

## Results and reports

`ForgeOpaqueSearchResultV2.report(detail="full")` returns an immutable
`OpaqueOptimizationReportV1`. It is a measurement fact source, not an
application recommendation. The report contains:

- structured complete/partial/failed status and termination reason;
- session, target, budget, stage, survivor, and lineage facts;
- per-candidate metric bounds, feasibility, physical IDs, memory, and failures;
- the unsquashed multi-objective Pareto frontier and baseline comparisons;
- warnings for incomplete evidence, failures, and unavailable comparisons;
- the canonical checkpoint digest, with the payload embedded only in a full
  report.

`report.to_dict()` and `report.to_json()` are the agent-facing fact source.
`report.to_markdown()` is rendered deterministically from the same facts for
human review. A summary report omits trial records and the checkpoint payload,
but retains the same `report_id` because that identity includes the checkpoint
digest. A summary cannot reconstruct omitted full facts.

The checked-in JSON Schema is
`schemas/opaque-optimization-report-v1.schema.json`; the runtime equivalent is
returned by `opaque_optimization_report_json_schema()`. Consumers must reject
an unknown schema instead of coercing it. Schema evolution uses a new model and
schema ID; this fork does not silently upgrade checkpoints or reports whose
contract no longer matches.

CompileIQ does not choose a scalar winner for a multi-objective contract.
Forge or another caller may apply an explicit ordered policy to the measured
Pareto frontier, but that enrichment must not rewrite CompileIQ metrics,
failures, lineage, or provenance.

## Resume and reuse boundary

A V2 session accepts a checkpoint only when all of these still match:

- dynamic domain and provider registry;
- workload, evaluation, and backend-environment identities;
- baseline recipe and deterministic seed;
- CompileIQ capability, core commit, and core lock;
- batch lineage, planned physical identities, and the resumed budget.

A checkpoint that exceeds the new evaluation or time budget is rejected.
Duplicate measurement keys, inconsistent stage status, poisoned cleanup, or
identity drift are also rejected. CompileIQ does not serialize Forge Python
materializers or Graph executables. Cross-process recipe resolution is a Forge
responsibility: the application recreates an equivalent semantic Graph and
provider catalog, then resolves the stable recipe ID.

## Capability and package provenance

`forge_recipe_search_capability()` binds this protocol to the modified package,
the bundled core commit and manifest lock, and hashes of packaged core files.
Core binary or manifest overrides are rejected for opaque recipe search.
Taichi Forge additionally locks the fork Git commit and the complete installed
Python source manifest; a package version alone is not sufficient provenance.

The fork supports Python 3.10 through 3.14. CI builds one platform wheel per
Linux x86_64, Linux aarch64, and Windows amd64 target, then installs and runs the
same wheel under Python 3.10 and 3.14 in addition to the build interpreter.
Unit and integration matrices cover every supported minor version. Platform
wheels contain only their matching bundled core.

## Local validation and build

```powershell
python -m pytest -q tests/unit
python -m pytest -q tests/integration/test_core_integration.py -k opaque_recipe
ruff check compileiq tests .github/scripts/smoke_test_compileiq_wheel.py
python -m pip install poetry wheel
poetry build -f wheel
wheel tags --platform-tag win_amd64 --remove dist/*.whl
python -m pip install --force-reinstall dist/*.whl
python .github/scripts/smoke_test_compileiq_wheel.py
```

Run the installed-wheel smoke outside the repository checkout (as CI does) so
the import cannot fall back to local sources. Record the fork commit, platform
wheel SHA-256, capability ID, bundled-core lock, and Python-source lock with
any qualification artifact.
