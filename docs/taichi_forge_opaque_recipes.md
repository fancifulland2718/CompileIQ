# Taichi Forge opaque recipe extension

This fork extends CompileIQ with a bounded, provider-neutral recipe domain for
Taichi Forge high-level optimization plans. The extension does not interpret a
recipe identifier as a CUDA compiler flag, kernel launch attribute, or source
parameter. Forge owns recipe legality and materialization; CompileIQ only
selects among the exact opaque recipes Forge supplies.

## Contracts

- `OpaqueRecipeDomainV1` canonicalizes and fingerprints the provider namespace,
  semantic version, semantic fingerprint, exact CompileIQ capability, bundled
  core identity, and recipe set.
- Provider recipe identifiers never cross the encrypted core boundary. The
  core sees only fixed ASCII ordinal tokens; the Python layer validates and
  restores the provider-owned identifier before the objective runs.
- `forge_recipe_search_capability()` binds the domain to this modified package,
  the bundled core commit, the manifest core lock, and platform file hashes.
  Core binary or manifest overrides are rejected for opaque recipe searches.
- Every selected token-to-recipe mapping is retained in search result metadata
  as `compileiq_opaque_recipe` audit data.
- `ForgeMainThreadWorker` executes Forge objectives serially on the caller's
  main thread. This is required for process-bound GPU/runtime objects.
- `PairedIsolatedWorker` is a separate diagnostic worker for fresh-process
  balanced AB/BA measurements. It does not turn compile time into an admission
  gate and it requires `normalize=False`.

The provider must include its baseline recipe in `recipe_ids`; this fork does
not invent a baseline or decide whether a candidate is legal, correct, or safe
to adopt at runtime.

## Minimal domain construction

```python
from compileiq.forge_support import forge_recipe_search_capability
from compileiq.recipes import OpaqueRecipeDomainV1

capability = forge_recipe_search_capability().as_dict()
domain = OpaqueRecipeDomainV1(
    provider_namespace="taichi_forge.graph.map_fusion",
    domain_version="1",
    provider_semantic_fingerprint="provider-owned-semantic-fingerprint",
    compileiq_capability_id=capability["capability_id"],
    compileiq_core_commit=capability["core_commit"],
    compileiq_core_lock=capability["core_lock"],
    recipe_ids=("baseline", "map2", "map3", "map4"),
)
```

Pass the domain to `compileiq.ciq.Search` with
`worker_type=ForgeMainThreadWorker` and a search configuration whose
`normalize` field is false. The objective receives the exact
`domain_fingerprint` and restored provider `recipe_id`.

## Local validation and build

```powershell
python -m pytest -q tests/unit
python -m pytest -q tests/integration/test_core_integration.py -k opaque_recipe
ruff check compileiq tests
python -m pip wheel . --no-deps --no-build-isolation --wheel-dir dist
```

Consumers must additionally lock the fork Git commit and a hash of the Python
sources that implement the capability, rather than accepting the package
version string alone.
