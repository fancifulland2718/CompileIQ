"""Versioned capability contract for Taichi Forge opaque recipe searches."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import threading
from typing import ClassVar, Literal, Mapping
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from compileiq.core.verify_core import MANIFEST_PATH, load_manifest, validate_core_lock
from compileiq.recipes import (
    OPAQUE_RECIPE_BATCH_SCHEMA,
    OPAQUE_RECIPE_SELECTION_AUDIT_SCHEMA,
    OpaqueRecipeDomainV1,
)
from compileiq.types import Worker
from compileiq.utils.validation import Score


FORGE_RECIPE_SEARCH_CAPABILITY_SCHEMA = "compileiq.taichi-forge-recipe-search-capability.v2"
FORGE_OPAQUE_TARGET_CONTRACT_SCHEMA = "compileiq.taichi-forge-opaque-target-contract.v1"
FORGE_RECIPE_SEARCH_FORK_BUILD_ID = "compileiq-taichi-forge-complete-recipes.v2"
FORGE_RECIPE_SEARCH_PACKAGE_VERSION = "1.0.0dev4+taichiforge.recipe2"
FORGE_RECIPE_SEARCH_PROTOCOL_REVISION = 4
FORGE_RECIPE_SEARCH_CAPABILITY_ID_PREFIX = "ciq-forge-cap-v2:"


def _finite_number(value, *, field_name):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise ValueError(f"{field_name} must be one finite numeric value")
    return float(value)


def _metric_name(value):
    if not isinstance(value, str) or not value:
        raise ValueError("metric name must be nonempty text")
    if len(value.encode("utf-8")) > 128:
        raise ValueError("metric name must be at most 128 UTF-8 bytes")
    return value


class ForgeOpaqueObjectiveV1(BaseModel):
    """One named target whose direction is explicit and never scalarized."""

    name: str
    direction: Literal["min", "max"] = "min"

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value):
        return _metric_name(value)


class ForgeOpaqueConstraintV1(BaseModel):
    """One inclusive feasibility bound over a named observed metric."""

    metric: str
    relation: Literal["<=", ">="]
    bound: float

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @field_validator("metric")
    @classmethod
    def _validate_metric(cls, value):
        return _metric_name(value)

    @field_validator("bound", mode="before")
    @classmethod
    def _validate_bound(cls, value):
        return _finite_number(value, field_name="constraint bound")


class ForgeOpaqueTargetContractV1(BaseModel):
    """Explicit objectives and hard constraints for complete Forge recipes."""

    SCHEMA: ClassVar[str] = FORGE_OPAQUE_TARGET_CONTRACT_SCHEMA
    MAX_OBJECTIVES: ClassVar[int] = 16
    MAX_CONSTRAINTS: ClassVar[int] = 32

    schema_id: Literal["compileiq.taichi-forge-opaque-target-contract.v1"] = Field(
        default=SCHEMA, alias="schema"
    )
    objectives: tuple[ForgeOpaqueObjectiveV1, ...]
    constraints: tuple[ForgeOpaqueConstraintV1, ...] = ()

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )

    @model_validator(mode="after")
    def _validate_contract(self):
        if not self.objectives:
            raise ValueError("opaque target contract requires at least one objective")
        if len(self.objectives) > self.MAX_OBJECTIVES:
            raise ValueError(
                f"opaque target contract supports at most {self.MAX_OBJECTIVES} objectives"
            )
        if len(self.constraints) > self.MAX_CONSTRAINTS:
            raise ValueError(
                f"opaque target contract supports at most {self.MAX_CONSTRAINTS} constraints"
            )
        objective_names = tuple(item.name for item in self.objectives)
        if len(set(objective_names)) != len(objective_names):
            raise ValueError("opaque target objective names must be unique")
        return self

    @property
    def metric_names(self):
        return tuple(
            dict.fromkeys(
                [item.name for item in self.objectives] + [item.metric for item in self.constraints]
            )
        )

    def as_dict(self):
        return self.model_dump(by_alias=True)


def _capability_identity(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return FORGE_RECIPE_SEARCH_CAPABILITY_ID_PREFIX + hashlib.sha256(canonical).hexdigest()


class ForgeRecipeSearchCapabilityV2(BaseModel):
    """Immutable proof that this CompileIQ build supports Forge recipe domains."""

    SCHEMA: ClassVar[str] = FORGE_RECIPE_SEARCH_CAPABILITY_SCHEMA

    schema_id: Literal["compileiq.taichi-forge-recipe-search-capability.v2"] = Field(
        default=SCHEMA, alias="schema"
    )
    protocol_revision: Literal[4] = FORGE_RECIPE_SEARCH_PROTOCOL_REVISION
    fork_build_id: Literal["compileiq-taichi-forge-complete-recipes.v2"] = (
        FORGE_RECIPE_SEARCH_FORK_BUILD_ID
    )
    package_version: Literal["1.0.0dev4+taichiforge.recipe2"] = FORGE_RECIPE_SEARCH_PACKAGE_VERSION
    opaque_recipe_domain_schema: Literal["compileiq.opaque-recipe-domain.v1"] = (
        OpaqueRecipeDomainV1.SCHEMA
    )
    opaque_recipe_batch_schema: Literal["compileiq.opaque-recipe-batch.v2"] = (
        OPAQUE_RECIPE_BATCH_SCHEMA
    )
    selection_audit_schema: Literal["compileiq.opaque-recipe-selection.v1"] = (
        OPAQUE_RECIPE_SELECTION_AUDIT_SCHEMA
    )
    opaque_target_contract_schema: Literal["compileiq.taichi-forge-opaque-target-contract.v1"] = (
        FORGE_OPAQUE_TARGET_CONTRACT_SCHEMA
    )
    opaque_target_selection: Literal["uncertainty_aware_pareto_layers_no_scalarization_v2"] = (
        "uncertainty_aware_pareto_layers_no_scalarization_v2"
    )
    trial_outcome_schema: Literal["compileiq.taichi-forge-trial-outcome.v2"] = (
        "compileiq.taichi-forge-trial-outcome.v2"
    )
    search_checkpoint_schema: Literal["compileiq.taichi-forge-search-checkpoint.v2"] = (
        "compileiq.taichi-forge-search-checkpoint.v2"
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
    opaque_recipe_search: Literal["budgeted_staged_pareto_racing_main_thread_v2"] = (
        "budgeted_staged_pareto_racing_main_thread_v2"
    )
    opaque_recipe_search_v1: Literal["bounded_exhaustive_main_thread_v1"] = (
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
    def _validate_identity(self) -> "ForgeRecipeSearchCapabilityV2":
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
) -> ForgeRecipeSearchCapabilityV2:
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
        "opaque_recipe_batch_schema": OPAQUE_RECIPE_BATCH_SCHEMA,
        "selection_audit_schema": OPAQUE_RECIPE_SELECTION_AUDIT_SCHEMA,
        "opaque_target_contract_schema": FORGE_OPAQUE_TARGET_CONTRACT_SCHEMA,
        "opaque_target_selection": ("uncertainty_aware_pareto_layers_no_scalarization_v2"),
        "trial_outcome_schema": "compileiq.taichi-forge-trial-outcome.v2",
        "search_checkpoint_schema": "compileiq.taichi-forge-search-checkpoint.v2",
        "max_recipe_ids": OpaqueRecipeDomainV1.MAX_RECIPE_IDS,
        "max_field_utf8_bytes": OpaqueRecipeDomainV1.MAX_FIELD_UTF8_BYTES,
        "max_canonical_bytes": OpaqueRecipeDomainV1.MAX_CANONICAL_BYTES,
        "provider_recipe_ids_cross_core_boundary": False,
        "core_verification": (
            "bundled_manifest_lock_and_platform_hashes_at_search_start_no_override"
        ),
        "opaque_domain_binding": "capability_id_core_commit_core_lock",
        "objective_worker": "forge_main_thread_serial_v1",
        "opaque_recipe_search": "budgeted_staged_pareto_racing_main_thread_v2",
        "opaque_recipe_search_v1": "bounded_exhaustive_main_thread_v1",
        "core_manifest_schema_version": schema_version,
        "core_commit": core_commit,
        "core_lock": core_lock,
    }
    return ForgeRecipeSearchCapabilityV2(
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
                f"ForgeMainThreadWorker received unsupported options {sorted(kwargs)!r}"
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

    def __init__(self, observations, *, problem_type, target_contract=None):
        self._observations = tuple(copy.deepcopy(item) for item in observations)
        self._problem_type = problem_type
        self._target_contract = target_contract

    @property
    def target_contract(self):
        if self._target_contract is None:
            return None
        return self._target_contract.as_dict()

    def get_results(self):
        return tuple(copy.deepcopy(item) for item in self._observations)

    def get_feasible_results(self):
        if self._target_contract is None:
            return self.get_results()
        return tuple(copy.deepcopy(item) for item in self._observations if item["feasible"])

    def get_best_result(self):
        if self._target_contract is not None:
            if len(self._target_contract.objectives) != 1:
                raise ValueError(
                    "multi-objective opaque results have no scalar winner; use pareto_front()"
                )
            feasible = self.get_feasible_results()
            if not feasible:
                raise ValueError("opaque target constraints rejected every recipe")
            objective = self._target_contract.objectives[0]
            selected = min(
                feasible,
                key=lambda item: (
                    item["metrics"][objective.name]
                    if objective.direction == "min"
                    else -item["metrics"][objective.name],
                    item["params"]["recipe_id"],
                ),
            )
            return copy.deepcopy(selected)
        selector = min if self._problem_type == "min" else max
        selected = selector(
            self._observations,
            key=lambda item: (item["score"], item["params"]["recipe_id"]),
        )
        return copy.deepcopy(selected)

    def pareto_front(self):
        if self._target_contract is None:
            raise ValueError("pareto_front requires an opaque target contract")
        feasible = self.get_feasible_results()
        frontier = []
        for candidate in feasible:
            if any(
                self._dominates(other, candidate)
                for other in feasible
                if other["param_id"] != candidate["param_id"]
            ):
                continue
            frontier.append(candidate)
        return tuple(
            copy.deepcopy(item)
            for item in sorted(
                frontier,
                key=lambda item: item["params"]["recipe_id"],
            )
        )

    def _dominates(self, left, right):
        no_worse = True
        strictly_better = False
        for objective in self._target_contract.objectives:
            left_value = left["metrics"][objective.name]
            right_value = right["metrics"][objective.name]
            if objective.direction == "min":
                no_worse = no_worse and left_value <= right_value
                strictly_better = strictly_better or left_value < right_value
            else:
                no_worse = no_worse and left_value >= right_value
                strictly_better = strictly_better or left_value > right_value
        return no_worse and strictly_better


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
        target_contract=None,
    ):
        if not callable(objective_function):
            raise TypeError("objective_function must be callable")
        if not isinstance(search_space, OpaqueRecipeDomainV1):
            raise TypeError("search_space must be an OpaqueRecipeDomainV1")
        if baseline_recipe_id not in search_space.recipe_ids:
            raise ValueError("opaque exhaustive search must retain its baseline")
        if problem_type not in ("min", "max"):
            raise ValueError("problem_type must be 'min' or 'max'")
        if target_contract is not None and not isinstance(
            target_contract, ForgeOpaqueTargetContractV1
        ):
            raise TypeError("target_contract must be a ForgeOpaqueTargetContractV1")
        if target_contract is not None and problem_type != "min":
            raise ValueError(
                "problem_type is unavailable with an opaque target contract; "
                "declare each objective direction explicitly"
            )
        self._capability = forge_recipe_search_capability().as_dict()
        required = {
            "compileiq_capability_id": self._capability["capability_id"],
            "compileiq_core_commit": self._capability["core_commit"],
            "compileiq_core_lock": self._capability["core_lock"],
        }
        if any(getattr(search_space, name) != value for name, value in required.items()):
            raise RuntimeError("opaque domain is not bound to this exact modified CompileIQ")
        self._objective_function = objective_function
        self._search_space = search_space
        self._baseline_recipe_id = baseline_recipe_id
        self._problem_type = problem_type
        self._target_contract = target_contract
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

    @property
    def target_contract(self):
        if self._target_contract is None:
            return None
        return self._target_contract.as_dict()

    def _observe_target(self, value):
        if not isinstance(value, Mapping):
            raise ValueError(
                "opaque target objective must return a mapping of named finite metrics"
            )
        expected = set(self._target_contract.metric_names)
        observed = set(value)
        if observed != expected:
            raise ValueError(
                "opaque target objective metrics mismatch: "
                f"expected {sorted(expected)!r}, "
                f"got {sorted(observed, key=lambda item: str(item))!r}"
            )
        metrics = {
            name: _finite_number(value[name], field_name=f"metric {name!r}")
            for name in self._target_contract.metric_names
        }
        violations = []
        for constraint in self._target_contract.constraints:
            actual = metrics[constraint.metric]
            satisfied = (
                actual <= constraint.bound
                if constraint.relation == "<="
                else actual >= constraint.bound
            )
            if not satisfied:
                violations.append(
                    {
                        "metric": constraint.metric,
                        "relation": constraint.relation,
                        "bound": constraint.bound,
                        "actual": actual,
                    }
                )
        return metrics, tuple(violations)

    def start(self):
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("opaque exhaustive search must run on the Python main thread")
        observations = []
        audits = []
        for ordinal, token in enumerate(self._search_space._core_recipe_tokens):
            decoded, audit = self._search_space.decode_candidate_with_audit(
                {
                    "domain_fingerprint": self._search_space.domain_fingerprint,
                    "recipe_id": token,
                }
            )
            objective_value = self._objective_function(decoded)
            param_id = ordinal + 1
            audits.append({"param_id": param_id, **audit})
            observation = {
                "param_id": param_id,
                "params": dict(decoded),
                "is_baseline": decoded["recipe_id"] == self._baseline_recipe_id,
                "metadata": {
                    "worker": ForgeMainThreadWorker.PROTOCOL,
                    "search": self.PROTOCOL,
                    "compileiq_opaque_recipe": dict(audit),
                },
            }
            if self._target_contract is None:
                observation["score"] = _finite_number(
                    objective_value,
                    field_name="opaque exhaustive objective score",
                )
            else:
                metrics, violations = self._observe_target(objective_value)
                observation.update(
                    {
                        "metrics": metrics,
                        "objective_values": tuple(
                            metrics[item.name] for item in self._target_contract.objectives
                        ),
                        "feasible": not violations,
                        "constraint_violations": violations,
                    }
                )
            observations.append(observation)
        self._audit_records = tuple(audits)
        self._observations = tuple(observations)
        return ForgeOpaqueRecipeExhaustiveResultV1(
            observations,
            problem_type=self._problem_type,
            target_contract=self._target_contract,
        )


from compileiq.forge_search_v2 import (  # noqa: E402
    ForgeOpaqueSearchBudgetV2,
    ForgeOpaqueSearchCheckpointV2,
    ForgeOpaqueSearchResultV2,
    ForgeOpaqueSearchSessionV2,
    ForgeOpaqueStageResultV2,
    TrialCleanupV2,
    TrialFailureV2,
    TrialOutcomeV2,
    TrialRecordV2,
    TrialRequestV2,
)


# Source-compatible name for V1 users; the capability envelope now advertises
# both the retained exhaustive protocol and the Forge V2 staged protocol.
ForgeRecipeSearchCapabilityV1 = ForgeRecipeSearchCapabilityV2


__all__ = [
    "FORGE_RECIPE_SEARCH_CAPABILITY_SCHEMA",
    "FORGE_OPAQUE_TARGET_CONTRACT_SCHEMA",
    "FORGE_RECIPE_SEARCH_FORK_BUILD_ID",
    "FORGE_RECIPE_SEARCH_PACKAGE_VERSION",
    "FORGE_RECIPE_SEARCH_PROTOCOL_REVISION",
    "ForgeMainThreadWorker",
    "ForgeOpaqueConstraintV1",
    "ForgeOpaqueObjectiveV1",
    "ForgeOpaqueRecipeExhaustiveResultV1",
    "ForgeOpaqueRecipeExhaustiveSearchV1",
    "ForgeOpaqueSearchBudgetV2",
    "ForgeOpaqueSearchCheckpointV2",
    "ForgeOpaqueSearchResultV2",
    "ForgeOpaqueSearchSessionV2",
    "ForgeOpaqueStageResultV2",
    "ForgeOpaqueTargetContractV1",
    "ForgeRecipeSearchCapabilityV1",
    "ForgeRecipeSearchCapabilityV2",
    "TrialCleanupV2",
    "TrialFailureV2",
    "TrialOutcomeV2",
    "TrialRecordV2",
    "TrialRequestV2",
    "forge_recipe_search_capability",
]
