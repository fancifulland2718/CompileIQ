"""Budgeted staged search for provider-owned complete Forge recipes."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from statistics import median
import threading
import time
from typing import Any, Callable, ClassVar, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from compileiq.recipes import OpaqueDynamicRecipeDomainV2, OpaqueRecipeBatchV2


FORGE_OPAQUE_SEARCH_BUDGET_SCHEMA = "compileiq.taichi-forge-search-budget.v2"
FORGE_OPAQUE_TRIAL_REQUEST_SCHEMA = "compileiq.taichi-forge-trial-request.v2"
FORGE_OPAQUE_TRIAL_OUTCOME_SCHEMA = "compileiq.taichi-forge-trial-outcome.v2"
FORGE_OPAQUE_SEARCH_CHECKPOINT_SCHEMA = "compileiq.taichi-forge-search-checkpoint.v2"
FORGE_OPAQUE_STAGE_RESULT_SCHEMA = "compileiq.taichi-forge-stage-result.v2"
FORGE_OPAQUE_EVALUATION_CONTEXT_SCHEMA = "compileiq.taichi-forge-evaluation-context.v1"
FORGE_OPAQUE_FINALIZATION_SCHEMA = "compileiq.taichi-forge-search-finalization.v1"
FORGE_OPAQUE_PHYSICAL_DUPLICATE_SCHEMA = "compileiq.taichi-forge-physical-duplicate.v2"
FORGE_OPAQUE_SEARCH_STATUS_SCHEMA = "compileiq.taichi-forge-search-status.v2"


def _canonical_identity(prefix: str, payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return prefix + hashlib.sha256(encoded).hexdigest()


def _nonempty_text(value: object, *, field_name: str, limit: int = 4096) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be nonempty text")
    if len(value.encode("utf-8")) > limit:
        raise ValueError(f"{field_name} exceeds the {limit} byte limit")
    return value


def _finite_number(value: object, *, field_name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise ValueError(f"{field_name} must be one finite numeric value")
    return float(value)


class ForgeOpaqueSearchBudgetV2(BaseModel):
    """Hard work limits for one resumable staged search."""

    SCHEMA: ClassVar[str] = FORGE_OPAQUE_SEARCH_BUDGET_SCHEMA

    schema_id: Literal["compileiq.taichi-forge-search-budget.v2"] = Field(
        default=SCHEMA,
        alias="schema",
    )
    evaluation_limit: int
    time_limit_seconds: float
    materialized_memory_limit_bytes: int

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )

    @model_validator(mode="after")
    def _validate_budget(self) -> "ForgeOpaqueSearchBudgetV2":
        if isinstance(self.evaluation_limit, bool) or self.evaluation_limit < 1:
            raise ValueError("evaluation_limit must be a positive integer")
        if (
            isinstance(self.time_limit_seconds, bool)
            or not isinstance(self.time_limit_seconds, (int, float))
            or not math.isfinite(float(self.time_limit_seconds))
            or float(self.time_limit_seconds) <= 0
        ):
            raise ValueError("time_limit_seconds must be finite and positive")
        object.__setattr__(self, "time_limit_seconds", float(self.time_limit_seconds))
        if (
            isinstance(self.materialized_memory_limit_bytes, bool)
            or not isinstance(self.materialized_memory_limit_bytes, int)
            or self.materialized_memory_limit_bytes < 0
        ):
            raise ValueError("materialized_memory_limit_bytes must be a nonnegative integer")
        return self


class ForgeOpaqueEvaluationContextV1(BaseModel):
    """Opaque identity of the workload and measurement implementation."""

    SCHEMA: ClassVar[str] = FORGE_OPAQUE_EVALUATION_CONTEXT_SCHEMA

    schema_id: Literal["compileiq.taichi-forge-evaluation-context.v1"] = Field(
        default=SCHEMA,
        alias="schema",
    )
    reuse_scope: Literal["session_only", "portable"]
    workload_context_id: str
    evaluation_contract_id: str
    backend_environment_id: str

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )

    @model_validator(mode="after")
    def _validate_context(self) -> "ForgeOpaqueEvaluationContextV1":
        for name in (
            "workload_context_id",
            "evaluation_contract_id",
            "backend_environment_id",
        ):
            _nonempty_text(getattr(self, name), field_name=name)
        return self


class ForgeOpaquePhysicalDuplicateV2(BaseModel):
    """Stable alias to the bytewise-smallest recipe for one physical plan."""

    SCHEMA: ClassVar[str] = FORGE_OPAQUE_PHYSICAL_DUPLICATE_SCHEMA

    schema_id: Literal["compileiq.taichi-forge-physical-duplicate.v2"] = Field(
        default=SCHEMA,
        alias="schema",
    )
    kind: Literal["planned", "materialized"]
    physical_id: str
    alias_recipe_id: str
    representative_recipe_id: str

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )

    @model_validator(mode="after")
    def _validate_duplicate(self) -> "ForgeOpaquePhysicalDuplicateV2":
        for name in ("physical_id", "alias_recipe_id", "representative_recipe_id"):
            _nonempty_text(getattr(self, name), field_name=name)
        if self.alias_recipe_id == self.representative_recipe_id:
            raise ValueError("physical duplicate alias must differ from its representative")
        if self.representative_recipe_id.encode("utf-8") >= self.alias_recipe_id.encode("utf-8"):
            raise ValueError("physical duplicate representative must be bytewise smallest")
        return self


class ForgeOpaqueSearchFinalizationV1(BaseModel):
    """Provider-owned statement about candidate generation completeness."""

    SCHEMA: ClassVar[str] = FORGE_OPAQUE_FINALIZATION_SCHEMA

    schema_id: Literal["compileiq.taichi-forge-search-finalization.v1"] = Field(
        default=SCHEMA,
        alias="schema",
    )
    generation_status: Literal[
        "exhaustive",
        "strategy_complete",
        "budget_limited",
        "provider_failed",
    ]
    terminal_fidelity_status: Literal["complete", "partial", "not_reached"]
    reason: str

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, value: str) -> str:
        return _nonempty_text(value, field_name="finalization reason")


class ForgeOpaqueSearchStatusV2(BaseModel):
    """Orthogonal search, evidence, baseline, and decision state."""

    SCHEMA: ClassVar[str] = FORGE_OPAQUE_SEARCH_STATUS_SCHEMA

    schema_id: Literal["compileiq.taichi-forge-search-status.v2"] = Field(
        default=SCHEMA,
        alias="schema",
    )
    terminal_state: Literal[
        "active",
        "complete",
        "budget_exhausted",
        "no_new_physical_identity",
        "all_failed",
        "poisoned",
        "provider_failed",
    ]
    generation_status: Literal[
        "not_finalized",
        "exhaustive",
        "strategy_complete",
        "budget_limited",
        "provider_failed",
    ]
    evaluation_status: Literal["not_started", "partial", "complete", "failed"]
    terminal_fidelity_status: Literal["complete", "partial", "not_reached"]
    baseline_status: Literal["available", "failed", "unavailable"]
    decision_status: Literal[
        "selected",
        "pareto_only",
        "no_feasible_candidate",
        "incomplete_evidence",
    ]
    reason: str

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )

    @field_validator("reason")
    @classmethod
    def _validate_status_reason(cls, value: str) -> str:
        return _nonempty_text(value, field_name="search status reason")


class TrialFailureV2(BaseModel):
    """Structured failure retained without converting it into a metric."""

    category: Literal[
        "budget",
        "materialization",
        "execution",
        "correctness",
        "cleanup",
        "protocol",
        "objective",
    ]
    code: str
    message: str
    retryable: bool = False

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @model_validator(mode="after")
    def _validate_failure(self) -> "TrialFailureV2":
        _nonempty_text(self.code, field_name="failure code", limit=128)
        _nonempty_text(self.message, field_name="failure message")
        return self


class TrialCleanupV2(BaseModel):
    """Provider cleanup result for all resources created by one trial."""

    status: Literal["complete", "not_required", "incomplete"]
    released_resources: bool
    detail_code: str = ""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @model_validator(mode="after")
    def _validate_cleanup(self) -> "TrialCleanupV2":
        if self.status == "complete" and not self.released_resources:
            raise ValueError("complete cleanup must report released resources")
        if self.status == "not_required" and self.released_resources:
            raise ValueError("not_required cleanup cannot report released resources")
        if self.detail_code:
            _nonempty_text(self.detail_code, field_name="cleanup detail_code", limit=128)
        return self


class TrialOutcomeV2(BaseModel):
    """One immutable named-metric observation with physical provenance."""

    SCHEMA: ClassVar[str] = FORGE_OPAQUE_TRIAL_OUTCOME_SCHEMA

    schema_id: Literal["compileiq.taichi-forge-trial-outcome.v2"] = Field(
        default=SCHEMA,
        alias="schema",
    )
    metrics: dict[str, float] = Field(default_factory=dict)
    planned_physical_id: str
    materialized_physical_id: str | None = None
    materialized_memory_bytes: int = 0
    provenance: dict[str, str]
    cleanup: TrialCleanupV2
    failure: TrialFailureV2 | None = None

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )

    @field_validator("metrics", mode="before")
    @classmethod
    def _normalize_metrics(cls, value: Any) -> dict[str, object]:
        if not isinstance(value, Mapping):
            raise ValueError("trial metrics must be a mapping")
        return dict(value)

    @field_validator("provenance", mode="before")
    @classmethod
    def _normalize_provenance(cls, value: Any) -> dict[str, object]:
        if not isinstance(value, Mapping):
            raise ValueError("trial provenance must be a mapping")
        return dict(value)

    @model_validator(mode="after")
    def _validate_outcome(self) -> "TrialOutcomeV2":
        _nonempty_text(self.planned_physical_id, field_name="planned_physical_id")
        if self.materialized_physical_id is not None:
            _nonempty_text(
                self.materialized_physical_id,
                field_name="materialized_physical_id",
            )
        if (
            isinstance(self.materialized_memory_bytes, bool)
            or not isinstance(self.materialized_memory_bytes, int)
            or self.materialized_memory_bytes < 0
        ):
            raise ValueError("materialized_memory_bytes must be a nonnegative integer")

        normalized_metrics = {}
        for name, value in self.metrics.items():
            _nonempty_text(name, field_name="metric name", limit=128)
            normalized_metrics[name] = _finite_number(
                value,
                field_name=f"metric {name!r}",
            )
        object.__setattr__(
            self,
            "metrics",
            dict(sorted(normalized_metrics.items())),
        )
        normalized_provenance = {}
        for name, value in self.provenance.items():
            _nonempty_text(name, field_name="provenance name", limit=128)
            _nonempty_text(value, field_name=f"provenance {name!r}")
            normalized_provenance[name] = value
        object.__setattr__(
            self,
            "provenance",
            dict(sorted(normalized_provenance.items())),
        )

        if self.failure is None:
            if not self.metrics:
                raise ValueError("successful trial outcome must contain named metrics")
            if self.materialized_physical_id is None:
                raise ValueError("successful trial outcome requires a materialized identity")
            if self.cleanup.status != "complete" or not self.cleanup.released_resources:
                raise ValueError("successful trial outcome requires complete cleanup")
        elif self.metrics:
            raise ValueError("failed trial outcome must not contain objective metrics")
        return self


class TrialRequestV2(BaseModel):
    """Deterministic request passed to a provider-owned trial evaluator."""

    SCHEMA: ClassVar[str] = FORGE_OPAQUE_TRIAL_REQUEST_SCHEMA

    schema_id: Literal["compileiq.taichi-forge-trial-request.v2"] = Field(
        default=SCHEMA,
        alias="schema",
    )
    measurement_key: str
    batch_fingerprint: str
    stage_index: int
    stage_fingerprint: str
    recipe_id: str
    fidelity_name: str
    fidelity_fingerprint: str
    observation_index: int
    observation_count: int
    is_baseline: bool
    deterministic_seed: int

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


class TrialRecordV2(BaseModel):
    request: TrialRequestV2
    outcome: TrialOutcomeV2
    source: Literal["objective", "budget_preflight"]
    elapsed_seconds: float
    memory_budget_exceeded: bool = False

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ForgeOpaqueStageResultV2(BaseModel):
    SCHEMA: ClassVar[str] = FORGE_OPAQUE_STAGE_RESULT_SCHEMA

    schema_id: Literal["compileiq.taichi-forge-stage-result.v2"] = Field(
        default=SCHEMA,
        alias="schema",
    )
    batch_fingerprint: str
    stage_index: int
    fidelity_fingerprint: str
    evaluated_recipe_ids: tuple[str, ...]
    survivor_recipe_ids: tuple[str, ...]
    physical_duplicates: tuple[ForgeOpaquePhysicalDuplicateV2, ...] = ()
    complete: bool

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )

    @field_validator(
        "evaluated_recipe_ids",
        "survivor_recipe_ids",
        "physical_duplicates",
        mode="before",
    )
    @classmethod
    def _normalize_recipe_ids(cls, value: Any) -> tuple[object, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("stage collections must be lists or tuples")
        return tuple(value)


class ForgeOpaqueSearchCheckpointV2(BaseModel):
    """Serializable state for exact resume and measurement reuse."""

    SCHEMA: ClassVar[str] = FORGE_OPAQUE_SEARCH_CHECKPOINT_SCHEMA

    schema_id: Literal["compileiq.taichi-forge-search-checkpoint.v2"] = Field(
        default=SCHEMA,
        alias="schema",
    )
    session_fingerprint: str
    dynamic_domain: OpaqueDynamicRecipeDomainV2
    evaluation_context: ForgeOpaqueEvaluationContextV1
    baseline_recipe_id: str
    deterministic_seed: int
    elapsed_seconds: float
    evaluation_count: int
    batches: tuple[OpaqueRecipeBatchV2, ...]
    records: tuple[TrialRecordV2, ...]
    stages: tuple[ForgeOpaqueStageResultV2, ...]
    finalization: ForgeOpaqueSearchFinalizationV1 | None = None
    status: ForgeOpaqueSearchStatusV2

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )

    @field_validator("batches", "records", "stages", mode="before")
    @classmethod
    def _normalize_sequence(cls, value: Any) -> tuple[object, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("checkpoint collections must be lists or tuples")
        return tuple(value)

    def as_dict(self) -> dict[str, object]:
        return self.model_dump(by_alias=True)


def _objective_bounds(values: list[float]) -> tuple[float, float, float]:
    return min(values), float(median(values)), max(values)


class ForgeOpaqueSearchResultV2:
    """Detached view of the current budgeted frontier and survivor lineage."""

    def __init__(self, session: "ForgeOpaqueSearchSessionV2"):
        self._checkpoint = session.checkpoint()
        self._aggregates = tuple(copy.deepcopy(session._current_aggregates()))
        self._target_contract = session._target_contract
        self._termination_reason = session.termination_reason
        self._budget = session._budget.model_dump(by_alias=True)
        self._capability = dict(session._capability)
        self._status = session.status

    @property
    def termination_reason(self) -> str:
        return self._termination_reason

    @property
    def budget(self) -> dict[str, object]:
        return dict(self._budget)

    @property
    def capability(self) -> dict[str, object]:
        return dict(self._capability)

    @property
    def status(self) -> ForgeOpaqueSearchStatusV2:
        return self._status.model_copy(deep=True)

    def checkpoint(self) -> ForgeOpaqueSearchCheckpointV2:
        return self._checkpoint.model_copy(deep=True)

    def get_results(self) -> tuple[dict[str, object], ...]:
        return tuple(copy.deepcopy(item) for item in self._aggregates)

    def get_feasible_results(self) -> tuple[dict[str, object], ...]:
        return tuple(copy.deepcopy(item) for item in self._aggregates if item["feasible"])

    def pareto_front(self) -> tuple[dict[str, object], ...]:
        feasible = self.get_feasible_results()
        return tuple(
            item
            for item in feasible
            if not any(
                self._dominates(other, item)
                for other in feasible
                if other["recipe_id"] != item["recipe_id"]
            )
        )

    def get_best_result(self) -> dict[str, object]:
        if self._status.decision_status != "selected":
            raise ValueError(
                "opaque results do not have terminal single-objective evidence; "
                f"decision_status={self._status.decision_status!r}"
            )
        if len(self._target_contract.objectives) != 1:
            raise ValueError(
                "multi-objective opaque results have no scalar winner; use pareto_front()"
            )
        feasible = self.get_feasible_results()
        if not feasible:
            raise ValueError("opaque target constraints rejected every measured recipe")
        objective = self._target_contract.objectives[0]
        return copy.deepcopy(
            min(
                feasible,
                key=lambda item: (
                    item["metrics"][objective.name]
                    if objective.direction == "min"
                    else -item["metrics"][objective.name],
                    item["recipe_id"],
                ),
            )
        )

    def survivor_lineage(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "batch_fingerprint": stage.batch_fingerprint,
                "stage_index": stage.stage_index,
                "fidelity_fingerprint": stage.fidelity_fingerprint,
                "survivor_recipe_ids": stage.survivor_recipe_ids,
            }
            for stage in self._checkpoint.stages
        )

    def _dominates(self, left: Mapping[str, object], right: Mapping[str, object]) -> bool:
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


class ForgeOpaqueSearchSessionV2:
    """Budgeted, resumable, uncertainty-aware Pareto racing over batches."""

    PROTOCOL = "dynamic_batch_pareto_racing_main_thread_v2"

    def __init__(
        self,
        *,
        objective_function: Callable[[TrialRequestV2], TrialOutcomeV2],
        dynamic_domain: OpaqueDynamicRecipeDomainV2,
        evaluation_context: ForgeOpaqueEvaluationContextV1,
        baseline_recipe_id: str,
        target_contract,
        budget: ForgeOpaqueSearchBudgetV2,
        deterministic_seed: int = 0,
        halving_factor: int = 2,
        minimum_survivors: int = 1,
        checkpoint: ForgeOpaqueSearchCheckpointV2 | Mapping[str, object] | None = None,
        clock: Callable[[], float] | None = None,
    ):
        from compileiq.forge_support import (
            ForgeOpaqueTargetContractV1,
            forge_recipe_search_capability,
        )

        if not callable(objective_function):
            raise TypeError("objective_function must be callable")
        if not isinstance(dynamic_domain, OpaqueDynamicRecipeDomainV2):
            raise TypeError("dynamic_domain must be an OpaqueDynamicRecipeDomainV2")
        if not isinstance(evaluation_context, ForgeOpaqueEvaluationContextV1):
            raise TypeError("evaluation_context must be a ForgeOpaqueEvaluationContextV1")
        _nonempty_text(baseline_recipe_id, field_name="baseline_recipe_id")
        if not isinstance(target_contract, ForgeOpaqueTargetContractV1):
            raise TypeError("target_contract must be a ForgeOpaqueTargetContractV1")
        if not isinstance(budget, ForgeOpaqueSearchBudgetV2):
            raise TypeError("budget must be a ForgeOpaqueSearchBudgetV2")
        if isinstance(deterministic_seed, bool) or not isinstance(deterministic_seed, int):
            raise TypeError("deterministic_seed must be an integer")
        if isinstance(halving_factor, bool) or halving_factor < 2:
            raise ValueError("halving_factor must be an integer of at least two")
        if isinstance(minimum_survivors, bool) or minimum_survivors < 1:
            raise ValueError("minimum_survivors must be a positive integer")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")

        self._objective_function = objective_function
        self._dynamic_domain = dynamic_domain
        self._evaluation_context = evaluation_context
        self._baseline_recipe_id = baseline_recipe_id
        self._target_contract = target_contract
        self._budget = budget
        self._seed = deterministic_seed
        self._halving_factor = halving_factor
        self._minimum_survivors = minimum_survivors
        self._clock = clock or time.monotonic
        self._capability = forge_recipe_search_capability().as_dict()
        for domain_field, capability_field in (
            ("compileiq_capability_id", "capability_id"),
            ("compileiq_core_commit", "core_commit"),
            ("compileiq_core_lock", "core_lock"),
        ):
            if getattr(dynamic_domain, domain_field) != self._capability[capability_field]:
                raise ValueError("dynamic domain is not bound to this exact CompileIQ build")
        self._session_fingerprint = _canonical_identity(
            "ciq-forge-session-v2:",
            {
                "protocol": self.PROTOCOL,
                "capability_id": self._capability["capability_id"],
                "dynamic_domain": dynamic_domain.model_dump(by_alias=True),
                "evaluation_context": evaluation_context.model_dump(by_alias=True),
                "baseline_recipe_id": baseline_recipe_id,
                "target_contract": target_contract.as_dict(),
                "deterministic_seed": deterministic_seed,
                "halving_factor": halving_factor,
                "minimum_survivors": minimum_survivors,
            },
        )
        self._batches: list[OpaqueRecipeBatchV2] = []
        self._records: dict[str, TrialRecordV2] = {}
        self._stages: list[ForgeOpaqueStageResultV2] = []
        self._evaluation_count = 0
        self._elapsed_seconds = 0.0
        self._active_started_at: float | None = None
        self._termination_reason = "not_started"
        self._finalization: ForgeOpaqueSearchFinalizationV1 | None = None
        self._status = self._derive_status("not_started")
        if checkpoint is not None:
            self._restore(checkpoint)

    @property
    def opaque_recipe_capability(self) -> dict[str, object]:
        return dict(self._capability)

    @property
    def evaluation_count(self) -> int:
        return self._evaluation_count

    @property
    def termination_reason(self) -> str:
        return self._termination_reason

    @property
    def status(self) -> ForgeOpaqueSearchStatusV2:
        return self._status.model_copy(deep=True)

    @property
    def observations(self) -> tuple[TrialRecordV2, ...]:
        return tuple(
            record.model_copy(deep=True)
            for record in sorted(
                self._records.values(),
                key=lambda item: item.request.measurement_key,
            )
        )

    @property
    def opaque_recipe_core_provenance(self) -> dict[str, object]:
        return {
            "core_commit": self._capability["core_commit"],
            "core_lock": self._capability["core_lock"],
            "verification": "bundled_manifest_lock_at_search_start",
        }

    def _terminal_stage(self) -> ForgeOpaqueStageResultV2 | None:
        by_fingerprint = {batch.batch_fingerprint: batch for batch in self._batches}
        terminal = [
            stage
            for stage in self._stages
            if by_fingerprint[stage.batch_fingerprint].fidelity.terminal
        ]
        return terminal[-1] if terminal else None

    def _derive_status(self, reason: str) -> ForgeOpaqueSearchStatusV2:
        finalization = self._finalization
        generation_status = (
            "not_finalized" if finalization is None else finalization.generation_status
        )
        terminal_stage = self._terminal_stage()
        inferred_terminal_fidelity = (
            "not_reached"
            if terminal_stage is None
            else ("complete" if terminal_stage.complete else "partial")
        )
        terminal_fidelity_status = (
            inferred_terminal_fidelity
            if finalization is None
            else finalization.terminal_fidelity_status
        )
        aggregates = self._current_aggregates()
        feasible = [item for item in aggregates if item["feasible"]]
        baseline_status = "unavailable"
        if self._batches:
            stage = self._stages[-1] if self._stages else None
            representative = self._baseline_recipe_id
            if stage is not None:
                alias = next(
                    (
                        item
                        for item in stage.physical_duplicates
                        if item.alias_recipe_id == self._baseline_recipe_id
                    ),
                    None,
                )
                if alias is not None:
                    representative = alias.representative_recipe_id
            baseline = next(
                (item for item in aggregates if item["recipe_id"] == representative),
                None,
            )
            if baseline is not None:
                baseline_status = "available" if baseline["feasible"] else "failed"

        if not self._batches:
            evaluation_status = "not_started"
        elif self._stages and self._stages[-1].complete:
            evaluation_status = "complete"
        elif aggregates:
            evaluation_status = "partial"
        else:
            evaluation_status = "failed"

        final_evidence = (
            finalization is not None
            and finalization.generation_status in ("exhaustive", "strategy_complete")
            and terminal_fidelity_status == "complete"
            and evaluation_status == "complete"
            and baseline_status == "available"
        )
        if not final_evidence:
            decision_status = "incomplete_evidence"
        elif not feasible:
            decision_status = "no_feasible_candidate"
        elif len(self._target_contract.objectives) == 1:
            decision_status = "selected"
        else:
            decision_status = "pareto_only"

        if reason == "poisoned":
            terminal_state = "poisoned"
        elif finalization is not None and finalization.generation_status == "provider_failed":
            terminal_state = "provider_failed"
        elif reason in ("evaluation_budget_exhausted", "time_budget_exhausted") or (
            finalization is not None and finalization.generation_status == "budget_limited"
        ):
            terminal_state = "budget_exhausted"
        elif finalization is not None and finalization.reason == "no_new_physical_identity":
            terminal_state = "no_new_physical_identity"
        elif finalization is not None and not feasible:
            terminal_state = "all_failed"
        elif final_evidence:
            terminal_state = "complete"
        elif finalization is not None:
            terminal_state = "budget_exhausted"
        else:
            terminal_state = "active"
        return ForgeOpaqueSearchStatusV2(
            terminal_state=terminal_state,
            generation_status=generation_status,
            evaluation_status=evaluation_status,
            terminal_fidelity_status=terminal_fidelity_status,
            baseline_status=baseline_status,
            decision_status=decision_status,
            reason=reason,
        )

    def _restore(
        self,
        value: ForgeOpaqueSearchCheckpointV2 | Mapping[str, object],
    ) -> None:
        checkpoint = (
            value
            if isinstance(value, ForgeOpaqueSearchCheckpointV2)
            else ForgeOpaqueSearchCheckpointV2(**dict(value))
        )
        if checkpoint.session_fingerprint != self._session_fingerprint:
            raise ValueError("checkpoint belongs to a different search session contract")
        if checkpoint.dynamic_domain != self._dynamic_domain:
            raise ValueError("checkpoint dynamic recipe domain mismatch")
        if checkpoint.evaluation_context != self._evaluation_context:
            raise ValueError("checkpoint evaluation context mismatch")
        if checkpoint.baseline_recipe_id != self._baseline_recipe_id:
            raise ValueError("checkpoint baseline recipe mismatch")
        if checkpoint.deterministic_seed != self._seed:
            raise ValueError("checkpoint deterministic seed mismatch")
        if checkpoint.evaluation_count > self._budget.evaluation_limit:
            raise ValueError("checkpoint already exceeds the resumed evaluation budget")
        if checkpoint.elapsed_seconds > self._budget.time_limit_seconds:
            raise ValueError("checkpoint already exceeds the resumed time budget")
        keys = tuple(item.request.measurement_key for item in checkpoint.records)
        if len(set(keys)) != len(keys):
            raise ValueError("checkpoint contains duplicate measurement keys")
        self._batches = [item.model_copy(deep=True) for item in checkpoint.batches]
        self._records = {
            item.request.measurement_key: item.model_copy(deep=True) for item in checkpoint.records
        }
        self._stages = [item.model_copy(deep=True) for item in checkpoint.stages]
        self._evaluation_count = checkpoint.evaluation_count
        self._elapsed_seconds = checkpoint.elapsed_seconds
        self._finalization = (
            None
            if checkpoint.finalization is None
            else checkpoint.finalization.model_copy(deep=True)
        )
        self._termination_reason = checkpoint.status.reason
        self._status = self._derive_status(self._termination_reason)
        if self._status != checkpoint.status:
            raise ValueError("checkpoint structured search status is inconsistent")

    def _elapsed(self) -> float:
        if self._active_started_at is None:
            return self._elapsed_seconds
        return self._elapsed_seconds + (self._clock() - self._active_started_at)

    def _validate_batch(self, batch: OpaqueRecipeBatchV2) -> None:
        if not isinstance(batch, OpaqueRecipeBatchV2):
            raise TypeError("batch must be an OpaqueRecipeBatchV2")
        if self._finalization is not None:
            raise RuntimeError("cannot submit a batch after search finalization")
        if self._termination_reason == "poisoned":
            raise RuntimeError("cannot continue a poisoned opaque search session")
        for field in (
            "compileiq_capability_id",
            "compileiq_core_commit",
            "compileiq_core_lock",
        ):
            capability_field = {
                "compileiq_capability_id": "capability_id",
                "compileiq_core_commit": "core_commit",
                "compileiq_core_lock": "core_lock",
            }[field]
            if getattr(batch, field) != self._capability[capability_field]:
                raise ValueError("opaque batch is not bound to this exact CompileIQ build")
        if (
            batch.provider_namespace != self._dynamic_domain.provider_namespace
            or batch.domain_version != self._dynamic_domain.domain_version
            or batch.provider_semantic_fingerprint != self._dynamic_domain.generation_domain_id
        ):
            raise ValueError("opaque batch drifted from the dynamic recipe domain")
        if self._baseline_recipe_id not in batch.recipe_ids:
            raise ValueError("every opaque search batch must retain the baseline recipe")

        prior_plans = {
            recipe.recipe_id: recipe.planned_physical_id
            for previous_batch in self._batches
            for recipe in previous_batch.recipes
        }
        for recipe in batch.recipes:
            previous_plan = prior_plans.get(recipe.recipe_id)
            if previous_plan is not None and previous_plan != recipe.planned_physical_id:
                raise ValueError("opaque recipe planned physical identity drifted across stages")

        if not self._batches:
            if batch.stage_index != 0 or batch.parent_batch_fingerprint is not None:
                raise ValueError("the first submitted batch must be stage zero")
            return
        previous = self._batches[-1]
        if batch.batch_fingerprint == previous.batch_fingerprint:
            return
        if previous.fidelity.terminal:
            raise ValueError("a terminal-fidelity batch must be the final search stage")
        if batch.stage_index != previous.stage_index + 1:
            raise ValueError("opaque batch stage indices must be contiguous")
        if batch.parent_batch_fingerprint != previous.batch_fingerprint:
            raise ValueError("opaque batch parent fingerprint mismatch")
        previous_stage = self._stages[-1]
        survivors = set(previous_stage.survivor_recipe_ids)
        for recipe in batch.recipes:
            if not set(recipe.parent_recipe_ids).issubset(survivors):
                raise ValueError("opaque recipe lineage refers to a non-surviving parent recipe")
        if batch.fidelity.ordinal < previous.fidelity.ordinal:
            raise ValueError("opaque batch fidelity must not move backwards")

    def _physical_duplicates(
        self,
        batch: OpaqueRecipeBatchV2,
        *,
        kind: Literal["planned", "materialized"],
    ) -> tuple[ForgeOpaquePhysicalDuplicateV2, ...]:
        groups: dict[str, list[str]] = {}
        if kind == "planned":
            for recipe in batch.recipes:
                groups.setdefault(recipe.planned_physical_id, []).append(recipe.recipe_id)
        else:
            for recipe_id in batch.recipe_ids:
                aggregate = self._aggregate_recipe(batch, recipe_id)
                if aggregate is None or not aggregate["feasible"]:
                    continue
                physical_id = aggregate.get("materialized_physical_id")
                if physical_id is not None:
                    groups.setdefault(physical_id, []).append(recipe_id)
        duplicates = []
        for physical_id, recipe_ids in groups.items():
            ordered = sorted(recipe_ids, key=lambda item: item.encode("utf-8"))
            representative = ordered[0]
            duplicates.extend(
                ForgeOpaquePhysicalDuplicateV2(
                    kind=kind,
                    physical_id=physical_id,
                    alias_recipe_id=alias,
                    representative_recipe_id=representative,
                )
                for alias in ordered[1:]
            )
        return tuple(
            sorted(
                duplicates,
                key=lambda item: (
                    item.kind,
                    item.physical_id.encode("utf-8"),
                    item.alias_recipe_id.encode("utf-8"),
                ),
            )
        )

    def _planned_representatives(self, batch: OpaqueRecipeBatchV2) -> tuple[str, ...]:
        aliases = {
            item.alias_recipe_id: item.representative_recipe_id
            for item in self._physical_duplicates(batch, kind="planned")
        }
        return tuple(recipe_id for recipe_id in batch.recipe_ids if recipe_id not in aliases)

    def _candidate_order(self, batch: OpaqueRecipeBatchV2) -> tuple[str, ...]:
        planned_aliases = {
            item.alias_recipe_id: item.representative_recipe_id
            for item in self._physical_duplicates(batch, kind="planned")
        }
        baseline_representative = planned_aliases.get(
            self._baseline_recipe_id,
            self._baseline_recipe_id,
        )
        others = [
            item for item in self._planned_representatives(batch) if item != baseline_representative
        ]
        others.sort(
            key=lambda recipe_id: (
                hashlib.sha256(
                    (f"{self._seed}\0{batch.batch_fingerprint}\0{recipe_id}").encode("utf-8")
                ).digest(),
                recipe_id,
            )
        )
        return (baseline_representative, *others)

    def _measurement_key(
        self,
        batch: OpaqueRecipeBatchV2,
        recipe_id: str,
        observation_index: int,
    ) -> str:
        return _canonical_identity(
            "ciq-measurement-v2:",
            {
                "session": self._session_fingerprint,
                "provider_semantic_fingerprint": batch.provider_semantic_fingerprint,
                "recipe_id": recipe_id,
                "fidelity": batch.fidelity.fidelity_fingerprint,
                "observation_index": observation_index,
            },
        )

    def _request(
        self,
        batch: OpaqueRecipeBatchV2,
        recipe_id: str,
        observation_index: int,
    ) -> TrialRequestV2:
        return TrialRequestV2(
            measurement_key=self._measurement_key(
                batch,
                recipe_id,
                observation_index,
            ),
            batch_fingerprint=batch.batch_fingerprint,
            stage_index=batch.stage_index,
            stage_fingerprint=batch.stage_fingerprint,
            recipe_id=recipe_id,
            fidelity_name=batch.fidelity.name,
            fidelity_fingerprint=batch.fidelity.fidelity_fingerprint,
            observation_index=observation_index,
            observation_count=batch.fidelity.repeat_count,
            is_baseline=recipe_id == self._baseline_recipe_id,
            deterministic_seed=self._seed,
        )

    def _failure_outcome(
        self,
        *,
        batch: OpaqueRecipeBatchV2,
        recipe_id: str,
        category: str,
        code: str,
        message: str,
        cleanup: TrialCleanupV2 | None = None,
    ) -> TrialOutcomeV2:
        return TrialOutcomeV2(
            metrics={},
            planned_physical_id=f"unmaterialized:{recipe_id}",
            materialized_physical_id=None,
            materialized_memory_bytes=0,
            provenance={
                "batch_fingerprint": batch.batch_fingerprint,
                "protocol": self.PROTOCOL,
            },
            cleanup=cleanup
            or TrialCleanupV2(
                status="not_required",
                released_resources=False,
                detail_code="no_materialization",
            ),
            failure=TrialFailureV2(
                category=category,
                code=code,
                message=message,
                retryable=False,
            ),
        )

    def _validate_outcome(
        self,
        batch: OpaqueRecipeBatchV2,
        request: TrialRequestV2,
        outcome: object,
    ) -> TrialOutcomeV2:
        if not isinstance(outcome, TrialOutcomeV2):
            return self._failure_outcome(
                batch=batch,
                recipe_id=request.recipe_id,
                category="protocol",
                code="invalid_outcome_type",
                message="objective must return TrialOutcomeV2",
            )
        if outcome.failure is None:
            expected_plan = batch.recipe(request.recipe_id).planned_physical_id
            if outcome.planned_physical_id != expected_plan:
                return self._failure_outcome(
                    batch=batch,
                    recipe_id=request.recipe_id,
                    category="protocol",
                    code="planned_physical_identity_mismatch",
                    message="trial outcome changed the batch planned physical identity",
                    cleanup=outcome.cleanup,
                )
            expected = set(self._target_contract.metric_names)
            if set(outcome.metrics) != expected:
                return self._failure_outcome(
                    batch=batch,
                    recipe_id=request.recipe_id,
                    category="protocol",
                    code="metric_contract_mismatch",
                    message=(
                        f"expected metrics {sorted(expected)!r}, got {sorted(outcome.metrics)!r}"
                    ),
                    cleanup=outcome.cleanup,
                )
            prior = [
                item.outcome
                for item in self._records.values()
                if item.request.recipe_id == request.recipe_id and item.outcome.failure is None
            ]
            if prior and any(
                item.planned_physical_id != outcome.planned_physical_id
                or item.materialized_physical_id != outcome.materialized_physical_id
                for item in prior
            ):
                return self._failure_outcome(
                    batch=batch,
                    recipe_id=request.recipe_id,
                    category="protocol",
                    code="physical_identity_drift",
                    message="repeated observations changed physical identity",
                    cleanup=outcome.cleanup,
                )
        return outcome

    def _record_preflight_memory_failure(
        self,
        batch: OpaqueRecipeBatchV2,
        recipe_id: str,
    ) -> None:
        estimate = batch.recipe(recipe_id).estimated_materialized_bytes
        for observation_index in range(batch.fidelity.repeat_count):
            request = self._request(batch, recipe_id, observation_index)
            if request.measurement_key in self._records:
                continue
            self._records[request.measurement_key] = TrialRecordV2(
                request=request,
                outcome=self._failure_outcome(
                    batch=batch,
                    recipe_id=recipe_id,
                    category="budget",
                    code="estimated_memory_budget_exceeded",
                    message=(
                        f"estimated materialized bytes {estimate} exceed "
                        f"budget {self._budget.materialized_memory_limit_bytes}"
                    ),
                ),
                source="budget_preflight",
                elapsed_seconds=0.0,
                memory_budget_exceeded=True,
            )

    def _run_request(
        self,
        batch: OpaqueRecipeBatchV2,
        request: TrialRequestV2,
    ) -> None:
        started = self._clock()
        try:
            raw_outcome = self._objective_function(request)
        except Exception as exc:  # provider failures are data, not session corruption
            raw_outcome = self._failure_outcome(
                batch=batch,
                recipe_id=request.recipe_id,
                category="objective",
                code=type(exc).__name__,
                message=str(exc).strip() or type(exc).__name__,
                cleanup=TrialCleanupV2(
                    status="incomplete",
                    released_resources=False,
                    detail_code="objective_exception",
                ),
            )
        elapsed = max(0.0, self._clock() - started)
        outcome = self._validate_outcome(batch, request, raw_outcome)
        memory_exceeded = (
            outcome.materialized_memory_bytes > self._budget.materialized_memory_limit_bytes
        )
        if memory_exceeded and outcome.failure is None:
            outcome = TrialOutcomeV2(
                metrics={},
                planned_physical_id=outcome.planned_physical_id,
                materialized_physical_id=outcome.materialized_physical_id,
                materialized_memory_bytes=outcome.materialized_memory_bytes,
                provenance=outcome.provenance,
                cleanup=outcome.cleanup,
                failure=TrialFailureV2(
                    category="budget",
                    code="observed_memory_budget_exceeded",
                    message=(
                        f"observed materialized bytes "
                        f"{outcome.materialized_memory_bytes} exceed budget "
                        f"{self._budget.materialized_memory_limit_bytes}"
                    ),
                    retryable=False,
                ),
            )
        self._records[request.measurement_key] = TrialRecordV2(
            request=request,
            outcome=outcome,
            source="objective",
            elapsed_seconds=elapsed,
            memory_budget_exceeded=memory_exceeded,
        )
        self._evaluation_count += 1
        if outcome.cleanup.status == "incomplete":
            self._termination_reason = "poisoned"

    def _records_for(
        self,
        batch: OpaqueRecipeBatchV2,
        recipe_id: str,
    ) -> list[TrialRecordV2]:
        fingerprint = batch.fidelity.fidelity_fingerprint
        return sorted(
            (
                item
                for item in self._records.values()
                if item.request.recipe_id == recipe_id
                and item.request.fidelity_fingerprint == fingerprint
            ),
            key=lambda item: item.request.observation_index,
        )

    def _aggregate_recipe(
        self,
        batch: OpaqueRecipeBatchV2,
        recipe_id: str,
    ) -> dict[str, object] | None:
        records = self._records_for(batch, recipe_id)
        if not records:
            return None
        successful = [
            item
            for item in records
            if item.outcome.failure is None and not item.memory_budget_exceeded
        ]
        failures = [
            {
                "source": item.source,
                "memory_budget_exceeded": item.memory_budget_exceeded,
                "failure": (
                    None if item.outcome.failure is None else item.outcome.failure.model_dump()
                ),
            }
            for item in records
            if item.outcome.failure is not None or item.memory_budget_exceeded
        ]
        if not successful:
            return {
                "recipe_id": recipe_id,
                "params": {"recipe_id": recipe_id},
                "stage_index": batch.stage_index,
                "fidelity": batch.fidelity.name,
                "observation_count": len(records),
                "required_observation_count": batch.fidelity.repeat_count,
                "complete": len(records) >= batch.fidelity.repeat_count,
                "metrics": {},
                "metric_bounds": {},
                "feasible": False,
                "constraint_violations": (),
                "failures": tuple(failures),
            }
        metric_values = {
            name: [item.outcome.metrics[name] for item in successful]
            for name in self._target_contract.metric_names
        }
        bounds = {name: _objective_bounds(values) for name, values in metric_values.items()}
        metrics = {name: value[1] for name, value in bounds.items()}
        violations = []
        for constraint in self._target_contract.constraints:
            lower, _, upper = bounds[constraint.metric]
            actual = upper if constraint.relation == "<=" else lower
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
                        "actual_worst_bound": actual,
                    }
                )
        return {
            "recipe_id": recipe_id,
            "params": {"recipe_id": recipe_id},
            "stage_index": batch.stage_index,
            "fidelity": batch.fidelity.name,
            "observation_count": len(successful),
            "required_observation_count": batch.fidelity.repeat_count,
            "complete": len(records) >= batch.fidelity.repeat_count,
            "metrics": metrics,
            "metric_bounds": {
                name: {"lower": value[0], "median": value[1], "upper": value[2]}
                for name, value in bounds.items()
            },
            "feasible": not violations and not failures,
            "constraint_violations": tuple(violations),
            "failures": tuple(failures),
            "planned_physical_id": successful[0].outcome.planned_physical_id,
            "materialized_physical_id": successful[0].outcome.materialized_physical_id,
            "materialized_memory_bytes": max(
                item.outcome.materialized_memory_bytes for item in successful
            ),
        }

    def _uncertainty_dominates(
        self,
        left: Mapping[str, object],
        right: Mapping[str, object],
    ) -> bool:
        no_worse = True
        strictly_better = False
        for objective in self._target_contract.objectives:
            left_bounds = left["metric_bounds"][objective.name]
            right_bounds = right["metric_bounds"][objective.name]
            if objective.direction == "min":
                comparison = left_bounds["upper"] <= right_bounds["lower"]
                strict = left_bounds["upper"] < right_bounds["lower"]
            else:
                comparison = left_bounds["lower"] >= right_bounds["upper"]
                strict = left_bounds["lower"] > right_bounds["upper"]
            no_worse = no_worse and comparison
            strictly_better = strictly_better or strict
        return no_worse and strictly_better

    def _pareto_layers(self, candidates: list[dict[str, object]]) -> list[list[str]]:
        remaining = {item["recipe_id"]: item for item in candidates if item["feasible"]}
        layers = []
        while remaining:
            frontier = [
                recipe_id
                for recipe_id, candidate in remaining.items()
                if not any(
                    self._uncertainty_dominates(other, candidate)
                    for other_id, other in remaining.items()
                    if other_id != recipe_id
                )
            ]
            frontier.sort(
                key=lambda recipe_id: (
                    hashlib.sha256(f"{self._seed}\0survivor\0{recipe_id}".encode("utf-8")).digest(),
                    recipe_id,
                )
            )
            layers.append(frontier)
            for recipe_id in frontier:
                remaining.pop(recipe_id)
        return layers

    def _survivors(
        self,
        batch: OpaqueRecipeBatchV2,
        aggregates: list[dict[str, object]],
    ) -> tuple[str, ...]:
        feasible_count = sum(item["feasible"] for item in aggregates)
        target = max(
            self._minimum_survivors,
            math.ceil(feasible_count / self._halving_factor),
        )
        selected = []
        for layer in self._pareto_layers(aggregates):
            for recipe_id in layer:
                if len(selected) >= target:
                    break
                selected.append(recipe_id)
            if len(selected) >= target:
                break
        if (
            self._baseline_recipe_id in batch.recipe_ids
            and self._baseline_recipe_id not in selected
        ):
            selected.append(self._baseline_recipe_id)
        order = {recipe_id: index for index, recipe_id in enumerate(batch.recipe_ids)}
        return tuple(sorted(set(selected), key=lambda recipe_id: order[recipe_id]))

    def submit_batch(self, batch: OpaqueRecipeBatchV2) -> ForgeOpaqueSearchResultV2:
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("opaque V2 search must run on the Python main thread")
        self._validate_batch(batch)
        existing = next(
            (item for item in self._batches if item.batch_fingerprint == batch.batch_fingerprint),
            None,
        )
        if existing is None:
            self._batches.append(batch)
        elif existing != batch:
            raise ValueError("batch fingerprint collision with different batch contents")

        self._termination_reason = "batch_complete"
        self._active_started_at = self._clock()
        try:
            for recipe_id in self._candidate_order(batch):
                estimate = batch.recipe(recipe_id).estimated_materialized_bytes
                if estimate > self._budget.materialized_memory_limit_bytes:
                    self._record_preflight_memory_failure(batch, recipe_id)
                    continue
                for observation_index in range(batch.fidelity.repeat_count):
                    request = self._request(batch, recipe_id, observation_index)
                    if request.measurement_key in self._records:
                        continue
                    if self._evaluation_count >= self._budget.evaluation_limit:
                        self._termination_reason = "evaluation_budget_exhausted"
                        break
                    if self._elapsed() >= self._budget.time_limit_seconds:
                        self._termination_reason = "time_budget_exhausted"
                        break
                    self._run_request(batch, request)
                    if self._termination_reason == "poisoned":
                        break
                if self._termination_reason != "batch_complete":
                    break
        finally:
            self._elapsed_seconds = self._elapsed()
            self._active_started_at = None

        measured_aggregates = [
            aggregate
            for recipe_id in batch.recipe_ids
            if (aggregate := self._aggregate_recipe(batch, recipe_id)) is not None
        ]
        planned_duplicates = self._physical_duplicates(batch, kind="planned")
        materialized_duplicates = self._physical_duplicates(batch, kind="materialized")
        materialized_aliases = {item.alias_recipe_id for item in materialized_duplicates}
        aggregates = [
            item for item in measured_aggregates if item["recipe_id"] not in materialized_aliases
        ]
        evaluated_ids = tuple(item["recipe_id"] for item in measured_aggregates)
        required_ids = set(self._planned_representatives(batch))
        complete = (
            self._termination_reason != "poisoned"
            and all(item["complete"] for item in measured_aggregates)
            and {item["recipe_id"] for item in measured_aggregates} == required_ids
        )
        stage = ForgeOpaqueStageResultV2(
            batch_fingerprint=batch.batch_fingerprint,
            stage_index=batch.stage_index,
            fidelity_fingerprint=batch.fidelity.fidelity_fingerprint,
            evaluated_recipe_ids=evaluated_ids,
            survivor_recipe_ids=self._survivors(batch, aggregates),
            physical_duplicates=planned_duplicates + materialized_duplicates,
            complete=complete,
        )
        self._stages = [
            item for item in self._stages if item.batch_fingerprint != batch.batch_fingerprint
        ]
        self._stages.append(stage)
        self._stages.sort(key=lambda item: item.stage_index)
        self._status = self._derive_status(self._termination_reason)
        return ForgeOpaqueSearchResultV2(self)

    def _current_aggregates(self) -> list[dict[str, object]]:
        if not self._batches:
            return []
        batch = self._batches[-1]
        aggregates = [
            aggregate
            for recipe_id in batch.recipe_ids
            if (aggregate := self._aggregate_recipe(batch, recipe_id)) is not None
        ]
        aliases = {
            item.alias_recipe_id for item in self._physical_duplicates(batch, kind="materialized")
        }
        return [item for item in aggregates if item["recipe_id"] not in aliases]

    def result(self) -> ForgeOpaqueSearchResultV2:
        return ForgeOpaqueSearchResultV2(self)

    def finalize(
        self,
        finalization: ForgeOpaqueSearchFinalizationV1,
    ) -> ForgeOpaqueSearchResultV2:
        """Freeze provider-owned generation status against measured stage facts."""

        if not isinstance(finalization, ForgeOpaqueSearchFinalizationV1):
            raise TypeError("finalization must be a ForgeOpaqueSearchFinalizationV1")
        if self._finalization is not None:
            if self._finalization != finalization:
                raise ValueError("opaque search was already finalized differently")
            return ForgeOpaqueSearchResultV2(self)
        terminal_stage = self._terminal_stage()
        observed_status = (
            "not_reached"
            if terminal_stage is None
            else ("complete" if terminal_stage.complete else "partial")
        )
        if finalization.terminal_fidelity_status != observed_status:
            raise ValueError(
                "terminal fidelity finalization contradicts measured batch facts: "
                f"declared={finalization.terminal_fidelity_status!r}, "
                f"observed={observed_status!r}"
            )
        self._finalization = finalization.model_copy(deep=True)
        self._termination_reason = finalization.reason
        self._status = self._derive_status(self._termination_reason)
        return ForgeOpaqueSearchResultV2(self)

    def checkpoint(self) -> ForgeOpaqueSearchCheckpointV2:
        if self._active_started_at is not None:
            raise RuntimeError("cannot checkpoint during an active objective call")
        return ForgeOpaqueSearchCheckpointV2(
            session_fingerprint=self._session_fingerprint,
            dynamic_domain=self._dynamic_domain,
            evaluation_context=self._evaluation_context,
            baseline_recipe_id=self._baseline_recipe_id,
            deterministic_seed=self._seed,
            elapsed_seconds=self._elapsed_seconds,
            evaluation_count=self._evaluation_count,
            batches=tuple(self._batches),
            records=tuple(
                sorted(
                    self._records.values(),
                    key=lambda item: item.request.measurement_key,
                )
            ),
            stages=tuple(sorted(self._stages, key=lambda item: item.stage_index)),
            finalization=self._finalization,
            status=self._status,
        )


__all__ = [
    "FORGE_OPAQUE_EVALUATION_CONTEXT_SCHEMA",
    "FORGE_OPAQUE_FINALIZATION_SCHEMA",
    "FORGE_OPAQUE_PHYSICAL_DUPLICATE_SCHEMA",
    "FORGE_OPAQUE_SEARCH_BUDGET_SCHEMA",
    "FORGE_OPAQUE_SEARCH_CHECKPOINT_SCHEMA",
    "FORGE_OPAQUE_STAGE_RESULT_SCHEMA",
    "FORGE_OPAQUE_SEARCH_STATUS_SCHEMA",
    "FORGE_OPAQUE_TRIAL_OUTCOME_SCHEMA",
    "FORGE_OPAQUE_TRIAL_REQUEST_SCHEMA",
    "ForgeOpaqueEvaluationContextV1",
    "ForgeOpaquePhysicalDuplicateV2",
    "ForgeOpaqueSearchBudgetV2",
    "ForgeOpaqueSearchCheckpointV2",
    "ForgeOpaqueSearchFinalizationV1",
    "ForgeOpaqueSearchResultV2",
    "ForgeOpaqueSearchSessionV2",
    "ForgeOpaqueSearchStatusV2",
    "ForgeOpaqueStageResultV2",
    "TrialCleanupV2",
    "TrialFailureV2",
    "TrialOutcomeV2",
    "TrialRecordV2",
    "TrialRequestV2",
]
