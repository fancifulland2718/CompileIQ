"""Versioned capability contract for Taichi Forge opaque recipe searches."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import threading
from typing import ClassVar, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from compileiq.core.verify_core import MANIFEST_PATH, load_manifest, validate_core_lock
from compileiq.recipes import (
    OPAQUE_RECIPE_SELECTION_AUDIT_SCHEMA,
    OpaqueRecipeDomainV1,
)
from compileiq.types import Worker
from compileiq.utils.validation import Score


FORGE_RECIPE_SEARCH_CAPABILITY_SCHEMA = "compileiq.taichi-forge-recipe-search-capability.v1"
FORGE_RECIPE_SEARCH_FORK_BUILD_ID = "compileiq-taichi-forge-opaque-recipes.v1.2"
FORGE_RECIPE_SEARCH_PACKAGE_VERSION = "1.0.0dev3+taichiforge.opaque1"
FORGE_RECIPE_SEARCH_PROTOCOL_REVISION = 2
FORGE_RECIPE_SEARCH_CAPABILITY_ID_PREFIX = "ciq-forge-cap-v1:"


def _capability_identity(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return FORGE_RECIPE_SEARCH_CAPABILITY_ID_PREFIX + hashlib.sha256(canonical).hexdigest()


class ForgeRecipeSearchCapabilityV1(BaseModel):
    """Immutable proof that this CompileIQ build supports Forge recipe domains."""

    SCHEMA: ClassVar[str] = FORGE_RECIPE_SEARCH_CAPABILITY_SCHEMA

    schema_id: Literal["compileiq.taichi-forge-recipe-search-capability.v1"] = Field(
        default=SCHEMA, alias="schema"
    )
    protocol_revision: Literal[2] = FORGE_RECIPE_SEARCH_PROTOCOL_REVISION
    fork_build_id: Literal["compileiq-taichi-forge-opaque-recipes.v1.2"] = (
        FORGE_RECIPE_SEARCH_FORK_BUILD_ID
    )
    package_version: Literal["1.0.0dev3+taichiforge.opaque1"] = FORGE_RECIPE_SEARCH_PACKAGE_VERSION
    opaque_recipe_domain_schema: Literal["compileiq.opaque-recipe-domain.v1"] = (
        OpaqueRecipeDomainV1.SCHEMA
    )
    selection_audit_schema: Literal["compileiq.opaque-recipe-selection.v1"] = (
        OPAQUE_RECIPE_SELECTION_AUDIT_SCHEMA
    )
    max_recipe_ids: int = OpaqueRecipeDomainV1.MAX_RECIPE_IDS
    max_field_utf8_bytes: int = OpaqueRecipeDomainV1.MAX_FIELD_UTF8_BYTES
    max_canonical_bytes: int = OpaqueRecipeDomainV1.MAX_CANONICAL_BYTES
    provider_recipe_ids_cross_core_boundary: Literal[False] = False
    core_verification: Literal[
        "bundled_manifest_lock_and_platform_hashes_at_search_start_no_override"
    ] = "bundled_manifest_lock_and_platform_hashes_at_search_start_no_override"
    opaque_domain_binding: Literal["capability_id_core_commit_core_lock"] = (
        "capability_id_core_commit_core_lock"
    )
    objective_worker: Literal["forge_main_thread_serial_v1"] = "forge_main_thread_serial_v1"
    opaque_recipe_search: Literal["bounded_exhaustive_main_thread_v1"] = (
        "bounded_exhaustive_main_thread_v1"
    )
    core_manifest_schema_version: int
    core_commit: str
    core_lock: str
    capability_id: str

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )

    @model_validator(mode="after")
    def _validate_identity(self) -> "ForgeRecipeSearchCapabilityV1":
        if isinstance(self.core_manifest_schema_version, bool) or (
            self.core_manifest_schema_version < 1
        ):
            raise ValueError("core manifest schema version must be a positive integer")
        if not self.core_commit:
            raise ValueError("core manifest must record a core commit")
        if not self.core_lock.startswith("sha256:"):
            raise ValueError("core lock must be a sha256 identity")
        if self.max_recipe_ids != OpaqueRecipeDomainV1.MAX_RECIPE_IDS:
            raise ValueError("opaque recipe count limit does not match the implementation")
        if self.max_field_utf8_bytes != OpaqueRecipeDomainV1.MAX_FIELD_UTF8_BYTES:
            raise ValueError("opaque recipe field limit does not match the implementation")
        if self.max_canonical_bytes != OpaqueRecipeDomainV1.MAX_CANONICAL_BYTES:
            raise ValueError("opaque recipe byte limit does not match the implementation")

        identity_payload = self.model_dump(
            by_alias=True,
            exclude={"capability_id"},
        )
        if self.capability_id != _capability_identity(identity_payload):
            raise ValueError("CompileIQ Forge capability identity mismatch")
        return self

    def as_dict(self) -> dict[str, object]:
        return self.model_dump(by_alias=True)


def forge_recipe_search_capability(
    manifest_path: str | Path = MANIFEST_PATH,
) -> ForgeRecipeSearchCapabilityV1:
    """Return a fail-closed capability bound to the packaged core manifest."""

    manifest = load_manifest(Path(manifest_path))
    manifest_errors = validate_core_lock(manifest)
    if manifest_errors:
        raise RuntimeError("; ".join(manifest_errors))

    schema_version = manifest.get("schema_version")
    core_commit = manifest.get("core_commit")
    core_lock = manifest.get("core_lock")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise RuntimeError("core manifest has no valid integer schema_version")
    if not isinstance(core_commit, str) or not core_commit:
        raise RuntimeError("core manifest has no valid core_commit")
    if not isinstance(core_lock, str) or not core_lock.startswith("sha256:"):
        raise RuntimeError("core manifest has no valid core_lock")

    payload: dict[str, object] = {
        "schema": FORGE_RECIPE_SEARCH_CAPABILITY_SCHEMA,
        "protocol_revision": FORGE_RECIPE_SEARCH_PROTOCOL_REVISION,
        "fork_build_id": FORGE_RECIPE_SEARCH_FORK_BUILD_ID,
        "package_version": FORGE_RECIPE_SEARCH_PACKAGE_VERSION,
        "opaque_recipe_domain_schema": OpaqueRecipeDomainV1.SCHEMA,
        "selection_audit_schema": OPAQUE_RECIPE_SELECTION_AUDIT_SCHEMA,
        "max_recipe_ids": OpaqueRecipeDomainV1.MAX_RECIPE_IDS,
        "max_field_utf8_bytes": OpaqueRecipeDomainV1.MAX_FIELD_UTF8_BYTES,
        "max_canonical_bytes": OpaqueRecipeDomainV1.MAX_CANONICAL_BYTES,
        "provider_recipe_ids_cross_core_boundary": False,
        "core_verification": (
            "bundled_manifest_lock_and_platform_hashes_at_search_start_no_override"
        ),
        "opaque_domain_binding": "capability_id_core_commit_core_lock",
        "objective_worker": "forge_main_thread_serial_v1",
        "opaque_recipe_search": "bounded_exhaustive_main_thread_v1",
        "core_manifest_schema_version": schema_version,
        "core_commit": core_commit,
        "core_lock": core_lock,
    }
    return ForgeRecipeSearchCapabilityV1(
        **payload,
        capability_id=_capability_identity(payload),
    )


class ForgeMainThreadWorker(Worker):
    """Run Forge recipe objectives serially on the Search caller's main thread."""

    PROTOCOL = "forge_main_thread_serial_v1"

    def __init__(self, cache_folder, normalize=False, tracker=None):
        if normalize:
            raise ValueError(
                "ForgeMainThreadWorker requires explicit Forge recipes and does not "
                "support CompileIQ's generic baseline normalization"
            )
        super().__init__(
            cache_folder=cache_folder,
            normalize=False,
            tracker=tracker,
            respects_num_workers=False,
            supports_timeout=False,
        )

    @classmethod
    def create(cls, cache_folder, normalize, tracker):
        return cls(cache_folder=cache_folder, normalize=normalize, tracker=tracker)

    def run(
        self,
        *,
        function,
        params_pool,
        params_ids,
        num_function_returns=1,
        num_workers=1,
        task_timeout=None,
        tracker=None,
        **kwargs,
    ):
        del num_workers
        if task_timeout is not None:
            raise ValueError("ForgeMainThreadWorker does not support task timeouts")
        if kwargs:
            raise TypeError(
                "ForgeMainThreadWorker received unsupported options " f"{sorted(kwargs)!r}"
            )
        active_tracker = self.tracker if tracker is None else tracker
        scores = []
        for parameters, param_id in zip(params_pool, params_ids):
            task_id = uuid4().hex
            active_tracker.pre_objective(parameters, task_id=task_id)
            objective_score = function(parameters)
            score = Score(
                score=objective_score,
                params=parameters,
                metadata=json.dumps(
                    {
                        "pid": os.getpid(),
                        "worker": self.PROTOCOL,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                param_id=param_id,
                num_objectives=num_function_returns,
            )
            active_tracker.post_objective(score.model_dump_json(), task_id=task_id)
            scores.append(score)
        return scores


class ForgeOpaqueRecipeExhaustiveResultV1:
    """Immutable detached observations from one complete opaque domain."""

    def __init__(self, observations, *, problem_type):
        self._observations = tuple(copy.deepcopy(item) for item in observations)
        self._problem_type = problem_type

    def get_results(self):
        return tuple(copy.deepcopy(item) for item in self._observations)

    def get_best_result(self):
        selector = min if self._problem_type == "min" else max
        selected = selector(
            self._observations,
            key=lambda item: (item["score"], item["params"]["recipe_id"]),
        )
        return copy.deepcopy(selected)


class ForgeOpaqueRecipeExhaustiveSearchV1:
    """Evaluate every safe token in one bounded opaque domain exactly once.

    This is the deterministic search route for finite Forge plan domains. The
    provider IDs are restored only after the same safe ordinal-token decoder
    used by the binary core validates domain identity and membership.
    """

    PROTOCOL = "bounded_exhaustive_main_thread_v1"

    def __init__(
        self,
        *,
        objective_function,
        search_space,
        baseline_recipe_id,
        problem_type="min",
    ):
        if not callable(objective_function):
            raise TypeError("objective_function must be callable")
        if not isinstance(search_space, OpaqueRecipeDomainV1):
            raise TypeError("search_space must be an OpaqueRecipeDomainV1")
        if baseline_recipe_id not in search_space.recipe_ids:
            raise ValueError("opaque exhaustive search must retain its baseline")
        if problem_type not in ("min", "max"):
            raise ValueError("problem_type must be 'min' or 'max'")
        self._capability = forge_recipe_search_capability().as_dict()
        required = {
            "compileiq_capability_id": self._capability["capability_id"],
            "compileiq_core_commit": self._capability["core_commit"],
            "compileiq_core_lock": self._capability["core_lock"],
        }
        if any(
            getattr(search_space, name) != value
            for name, value in required.items()
        ):
            raise RuntimeError(
                "opaque domain is not bound to this exact modified CompileIQ"
            )
        self._objective_function = objective_function
        self._search_space = search_space
        self._baseline_recipe_id = baseline_recipe_id
        self._problem_type = problem_type
        self._audit_records = ()
        self._observations = ()

    @property
    def opaque_recipe_capability(self):
        return dict(self._capability)

    @property
    def opaque_recipe_core_provenance(self):
        return {
            "core_commit": self._capability["core_commit"],
            "core_lock": self._capability["core_lock"],
            "verification": "bundled_manifest_lock_at_search_start",
        }

    @property
    def opaque_recipe_audit_records(self):
        return tuple(copy.deepcopy(item) for item in self._audit_records)

    @property
    def observations(self):
        return tuple(copy.deepcopy(item) for item in self._observations)

    def start(self):
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError(
                "opaque exhaustive search must run on the Python main thread"
            )
        observations = []
        audits = []
        for ordinal, token in enumerate(self._search_space._core_recipe_tokens):
            decoded, audit = self._search_space.decode_candidate_with_audit(
                {
                    "domain_fingerprint": self._search_space.domain_fingerprint,
                    "recipe_id": token,
                }
            )
            score = self._objective_function(decoded)
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(float(score))
            ):
                raise ValueError(
                    "opaque exhaustive objective must return one finite numeric score"
                )
            param_id = ordinal + 1
            audits.append({"param_id": param_id, **audit})
            observations.append(
                {
                    "param_id": param_id,
                    "score": float(score),
                    "params": dict(decoded),
                    "is_baseline": (
                        decoded["recipe_id"] == self._baseline_recipe_id
                    ),
                    "metadata": {
                        "worker": ForgeMainThreadWorker.PROTOCOL,
                        "search": self.PROTOCOL,
                        "compileiq_opaque_recipe": dict(audit),
                    },
                }
            )
        self._audit_records = tuple(audits)
        self._observations = tuple(observations)
        return ForgeOpaqueRecipeExhaustiveResultV1(
            observations, problem_type=self._problem_type
        )


__all__ = [
    "FORGE_RECIPE_SEARCH_CAPABILITY_SCHEMA",
    "FORGE_RECIPE_SEARCH_FORK_BUILD_ID",
    "FORGE_RECIPE_SEARCH_PACKAGE_VERSION",
    "FORGE_RECIPE_SEARCH_PROTOCOL_REVISION",
    "ForgeMainThreadWorker",
    "ForgeOpaqueRecipeExhaustiveResultV1",
    "ForgeOpaqueRecipeExhaustiveSearchV1",
    "ForgeRecipeSearchCapabilityV1",
    "forge_recipe_search_capability",
]
