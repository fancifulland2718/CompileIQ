"""Deterministic reports for opaque staged optimization results.

The report owns measurement facts only.  Recipe meaning and selection policy
remain the responsibility of the caller that supplied the opaque recipes.
"""

from __future__ import annotations

import hashlib
import json
from statistics import median
from typing import Any, ClassVar, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from compileiq.forge_search_v2 import (
    ForgeOpaqueSearchCheckpointV2,
    TrialRecordV2,
)


OPAQUE_OPTIMIZATION_REPORT_SCHEMA = "compileiq.opaque-optimization-report.v1"
OPAQUE_OPTIMIZATION_REPORT_JSON_SCHEMA_ID = (
    "https://raw.githubusercontent.com/fancifulland2718/CompileIQ/main/"
    "schemas/opaque-optimization-report-v1.schema.json"
)
OPAQUE_OPTIMIZATION_REPORT_RENDERER = "json_fact_source_markdown_projection_v1"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _identity(prefix: str, value: object) -> str:
    return prefix + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sequence(value: Any) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("report collection must be a list or tuple")
    return tuple(value)


def _sorted_mapping(value: Any) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("report mapping must be a mapping")
    encoded = json.dumps(
        dict(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=lambda item: item.model_dump(by_alias=True),
    )
    return json.loads(encoded)


class _ReportModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )


class OpaqueReportStatusV1(_ReportModel):
    overall: Literal["complete", "partial", "failed"]
    terminal_state: str
    generation_status: str
    evaluation_status: str
    terminal_fidelity_status: str
    baseline_status: str
    decision_status: str
    termination_reason: str


class OpaqueReportSessionV1(_ReportModel):
    protocol: str
    session_fingerprint: str
    baseline_recipe_id: str
    deterministic_seed: int
    dynamic_domain: dict[str, object]
    evaluation_context: dict[str, object]
    capability: dict[str, object]

    @field_validator("dynamic_domain", "evaluation_context", "capability", mode="before")
    @classmethod
    def _normalize_mappings(cls, value: Any) -> dict[str, object]:
        return _sorted_mapping(value)


class OpaqueReportObjectiveV1(_ReportModel):
    name: str
    direction: Literal["min", "max"]


class OpaqueReportConstraintV1(_ReportModel):
    metric: str
    relation: Literal["<=", ">="]
    bound: float


class OpaqueReportTargetV1(_ReportModel):
    schema_id: Literal["compileiq.taichi-forge-opaque-target-contract.v1"] = Field(
        alias="schema"
    )
    objectives: tuple[OpaqueReportObjectiveV1, ...]
    constraints: tuple[OpaqueReportConstraintV1, ...]

    @field_validator("objectives", "constraints", mode="before")
    @classmethod
    def _normalize_collections(cls, value: Any) -> tuple[object, ...]:
        return _sequence(value)


class OpaqueReportBudgetV1(_ReportModel):
    evaluation_limit: int
    time_limit_seconds: float
    materialized_memory_limit_bytes: int
    evaluation_count: int
    elapsed_seconds: float
    materialized_memory_peak_bytes: int


class OpaqueReportMetricSummaryV1(_ReportModel):
    observed_min: float
    median: float
    observed_max: float
    successful_observation_count: int


class OpaqueReportConstraintViolationV1(_ReportModel):
    metric: str
    relation: Literal["<=", ">="]
    bound: float
    actual_worst_bound: float


class OpaqueReportFailureV1(_ReportModel):
    measurement_key: str
    observation_index: int
    source: str
    memory_budget_exceeded: bool
    elapsed_seconds: float
    cleanup_status: str
    cleanup_detail_code: str
    failure: dict[str, object] | None

    @field_validator("failure", mode="before")
    @classmethod
    def _normalize_failure(cls, value: Any) -> dict[str, object] | None:
        if value is None:
            return None
        return _sorted_mapping(value)


class OpaqueReportCandidateV1(_ReportModel):
    candidate_key: str
    recipe_id: str
    batch_fingerprint: str
    stage_index: int
    fidelity_name: str
    fidelity_fingerprint: str
    terminal_fidelity: bool
    total_observation_count: int
    successful_observation_count: int
    required_observation_count: int
    complete: bool
    feasible: bool
    metrics: dict[str, OpaqueReportMetricSummaryV1]
    constraint_violations: tuple[OpaqueReportConstraintViolationV1, ...]
    failures: tuple[OpaqueReportFailureV1, ...]
    planned_physical_ids: tuple[str, ...]
    materialized_physical_ids: tuple[str, ...]
    materialized_memory_peak_bytes: int
    observed_provenance: tuple[dict[str, str], ...]

    @field_validator("metrics", mode="before")
    @classmethod
    def _normalize_metrics(cls, value: Any) -> dict[str, object]:
        return _sorted_mapping(value)

    @field_validator(
        "constraint_violations",
        "failures",
        "planned_physical_ids",
        "materialized_physical_ids",
        "observed_provenance",
        mode="before",
    )
    @classmethod
    def _normalize_collections(cls, value: Any) -> tuple[object, ...]:
        return _sequence(value)


class OpaqueReportStageV1(_ReportModel):
    batch_fingerprint: str
    stage_index: int
    parent_batch_fingerprint: str | None
    fidelity_name: str
    fidelity_fingerprint: str
    fidelity_ordinal: int
    repeat_count: int
    work_scale: float
    terminal_fidelity: bool
    recipe_ids: tuple[str, ...]
    evaluated_recipe_ids: tuple[str, ...]
    survivor_recipe_ids: tuple[str, ...]
    physical_duplicates: tuple[dict[str, object], ...]
    complete: bool

    @field_validator(
        "recipe_ids",
        "evaluated_recipe_ids",
        "survivor_recipe_ids",
        "physical_duplicates",
        mode="before",
    )
    @classmethod
    def _normalize_collections(cls, value: Any) -> tuple[object, ...]:
        return _sequence(value)


class OpaqueReportParetoEntryV1(_ReportModel):
    candidate_key: str
    recipe_id: str
    metrics: dict[str, float]

    @field_validator("metrics", mode="before")
    @classmethod
    def _normalize_metrics(cls, value: Any) -> dict[str, object]:
        return _sorted_mapping(value)


class OpaqueReportBaselineComparisonV1(_ReportModel):
    candidate_key: str
    recipe_id: str
    metric: str
    direction: Literal["min", "max"]
    baseline_value: float
    candidate_value: float
    delta: float
    candidate_over_baseline: float | None
    directional_improvement: float
    relative_directional_improvement: float | None


class OpaqueReportWarningV1(_ReportModel):
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    stage_index: int | None = None
    candidate_key: str | None = None


class OpaqueReportCheckpointV1(_ReportModel):
    schema_id: str = Field(alias="schema")
    digest: str
    embedded: bool
    payload: dict[str, object] | None = None

    @field_validator("payload", mode="before")
    @classmethod
    def _normalize_payload(cls, value: Any) -> dict[str, object] | None:
        if value is None:
            return None
        return _sorted_mapping(value)


class OpaqueOptimizationReportV1(_ReportModel):
    """Versioned, deterministic fact report for one opaque search result."""

    SCHEMA: ClassVar[str] = OPAQUE_OPTIMIZATION_REPORT_SCHEMA
    REPORT_ID_PREFIX: ClassVar[str] = "ciq-opaque-report-v1:"

    schema_id: Literal["compileiq.opaque-optimization-report.v1"] = Field(
        default=SCHEMA,
        alias="schema",
    )
    report_id: str
    detail: Literal["summary", "full"]
    status: OpaqueReportStatusV1
    session: OpaqueReportSessionV1
    target: OpaqueReportTargetV1
    budget: OpaqueReportBudgetV1
    stages: tuple[OpaqueReportStageV1, ...]
    candidates: tuple[OpaqueReportCandidateV1, ...]
    pareto_front: tuple[OpaqueReportParetoEntryV1, ...]
    baseline_comparison: tuple[OpaqueReportBaselineComparisonV1, ...]
    warnings: tuple[OpaqueReportWarningV1, ...]
    checkpoint: OpaqueReportCheckpointV1

    @field_validator(
        "stages",
        "candidates",
        "pareto_front",
        "baseline_comparison",
        "warnings",
        mode="before",
    )
    @classmethod
    def _normalize_collections(cls, value: Any) -> tuple[object, ...]:
        return _sequence(value)

    def _identity_facts(self) -> dict[str, object]:
        payload = self.model_dump(by_alias=True)
        payload.pop("report_id")
        payload.pop("detail")
        payload["checkpoint"] = {
            "schema": self.checkpoint.schema_id,
            "digest": self.checkpoint.digest,
        }
        return payload

    @model_validator(mode="after")
    def _validate_report(self) -> "OpaqueOptimizationReportV1":
        expected = _identity(self.REPORT_ID_PREFIX, self._identity_facts())
        if self.report_id != expected:
            raise ValueError("opaque optimization report identity mismatch")
        if self.detail == "summary":
            if self.checkpoint.embedded or self.checkpoint.payload is not None:
                raise ValueError("summary report must not embed checkpoint or trial records")
        else:
            if not self.checkpoint.embedded or self.checkpoint.payload is None:
                raise ValueError("full report must embed its checkpoint payload")
        return self

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "OpaqueOptimizationReportV1":
        return cls.model_validate(dict(value))

    @classmethod
    def from_json(cls, value: str) -> "OpaqueOptimizationReportV1":
        return cls.model_validate_json(value)

    def summary(self) -> "OpaqueOptimizationReportV1":
        if self.detail == "summary":
            return self
        payload = self.model_dump(by_alias=True)
        payload["detail"] = "summary"
        payload["checkpoint"] = {
            "schema": self.checkpoint.schema_id,
            "digest": self.checkpoint.digest,
            "embedded": False,
            "payload": None,
        }
        return type(self).model_validate(payload)

    @property
    def trials(self) -> tuple[TrialRecordV2, ...]:
        """Return trial records derived from the single embedded checkpoint source."""

        if self.checkpoint.payload is None:
            return ()
        checkpoint = ForgeOpaqueSearchCheckpointV2.model_validate(
            self.checkpoint.payload
        )
        return checkpoint.records

    def to_dict(self, *, detail: Literal["summary", "full"] | None = None) -> dict[str, object]:
        report = self
        if detail == "summary":
            report = self.summary()
        elif detail == "full" and self.detail != "full":
            raise ValueError("a summary report cannot reconstruct omitted full trial facts")
        elif detail not in (None, "summary", "full"):
            raise ValueError("detail must be 'summary' or 'full'")
        return report.model_dump(by_alias=True)

    def to_json(self, *, detail: Literal["summary", "full"] | None = None) -> str:
        return _canonical_json(self.to_dict(detail=detail))

    def to_markdown(self, *, detail: Literal["summary", "full"] | None = None) -> str:
        report = self
        if detail == "summary":
            report = self.summary()
        elif detail == "full" and self.detail != "full":
            raise ValueError("a summary report cannot reconstruct omitted full trial facts")
        elif detail not in (None, "summary", "full"):
            raise ValueError("detail must be 'summary' or 'full'")
        return _render_markdown(report)


def _candidate_key(batch_fingerprint: str, fidelity_fingerprint: str, recipe_id: str) -> str:
    return _identity(
        "ciq-report-candidate-v1:",
        {
            "batch_fingerprint": batch_fingerprint,
            "fidelity_fingerprint": fidelity_fingerprint,
            "recipe_id": recipe_id,
        },
    )


def _records_for(
    checkpoint: ForgeOpaqueSearchCheckpointV2,
    *,
    batch_fingerprint: str,
    fidelity_fingerprint: str,
    recipe_id: str,
) -> tuple[TrialRecordV2, ...]:
    return tuple(
        sorted(
            (
                record
                for record in checkpoint.records
                if record.request.batch_fingerprint == batch_fingerprint
                and record.request.fidelity_fingerprint == fidelity_fingerprint
                and record.request.recipe_id == recipe_id
            ),
            key=lambda record: (record.request.observation_index, record.request.measurement_key),
        )
    )


def _candidate(
    checkpoint: ForgeOpaqueSearchCheckpointV2,
    batch,
    lineage,
    target: OpaqueReportTargetV1,
) -> OpaqueReportCandidateV1:
    records = _records_for(
        checkpoint,
        batch_fingerprint=batch.batch_fingerprint,
        fidelity_fingerprint=batch.fidelity.fidelity_fingerprint,
        recipe_id=lineage.recipe_id,
    )
    successful = tuple(
        record
        for record in records
        if record.outcome.failure is None and not record.memory_budget_exceeded
    )
    metrics: dict[str, OpaqueReportMetricSummaryV1] = {}
    metric_names = tuple(
        dict.fromkeys(
            [objective.name for objective in target.objectives]
            + [constraint.metric for constraint in target.constraints]
        )
    )
    for name in metric_names:
        values = [record.outcome.metrics[name] for record in successful]
        if values:
            metrics[name] = OpaqueReportMetricSummaryV1(
                observed_min=min(values),
                median=float(median(values)),
                observed_max=max(values),
                successful_observation_count=len(values),
            )

    violations = []
    for constraint in target.constraints:
        summary = metrics.get(constraint.metric)
        if summary is None:
            continue
        actual = (
            summary.observed_max
            if constraint.relation == "<="
            else summary.observed_min
        )
        satisfied = (
            actual <= constraint.bound
            if constraint.relation == "<="
            else actual >= constraint.bound
        )
        if not satisfied:
            violations.append(
                OpaqueReportConstraintViolationV1(
                    metric=constraint.metric,
                    relation=constraint.relation,
                    bound=constraint.bound,
                    actual_worst_bound=actual,
                )
            )

    failures = tuple(
        OpaqueReportFailureV1(
            measurement_key=record.request.measurement_key,
            observation_index=record.request.observation_index,
            source=record.source,
            memory_budget_exceeded=record.memory_budget_exceeded,
            elapsed_seconds=record.elapsed_seconds,
            cleanup_status=record.outcome.cleanup.status,
            cleanup_detail_code=record.outcome.cleanup.detail_code,
            failure=(
                None
                if record.outcome.failure is None
                else record.outcome.failure.model_dump()
            ),
        )
        for record in records
        if record.outcome.failure is not None or record.memory_budget_exceeded
    )
    provenance = {
        _canonical_json(record.outcome.provenance): dict(record.outcome.provenance)
        for record in successful
    }
    planned_ids = {lineage.planned_physical_id}
    planned_ids.update(record.outcome.planned_physical_id for record in records)
    materialized_ids = {
        record.outcome.materialized_physical_id
        for record in successful
        if record.outcome.materialized_physical_id is not None
    }
    return OpaqueReportCandidateV1(
        candidate_key=_candidate_key(
            batch.batch_fingerprint,
            batch.fidelity.fidelity_fingerprint,
            lineage.recipe_id,
        ),
        recipe_id=lineage.recipe_id,
        batch_fingerprint=batch.batch_fingerprint,
        stage_index=batch.stage_index,
        fidelity_name=batch.fidelity.name,
        fidelity_fingerprint=batch.fidelity.fidelity_fingerprint,
        terminal_fidelity=batch.fidelity.terminal,
        total_observation_count=len(records),
        successful_observation_count=len(successful),
        required_observation_count=batch.fidelity.repeat_count,
        complete=len(records) >= batch.fidelity.repeat_count,
        feasible=bool(successful) and not failures and not violations,
        metrics=metrics,
        constraint_violations=tuple(violations),
        failures=failures,
        planned_physical_ids=tuple(sorted(planned_ids)),
        materialized_physical_ids=tuple(sorted(materialized_ids)),
        materialized_memory_peak_bytes=max(
            (record.outcome.materialized_memory_bytes for record in successful),
            default=0,
        ),
        observed_provenance=tuple(provenance[key] for key in sorted(provenance)),
    )


def _overall_status(terminal_state: str) -> Literal["complete", "partial", "failed"]:
    if terminal_state == "complete":
        return "complete"
    if terminal_state in {"all_failed", "poisoned", "provider_failed"}:
        return "failed"
    return "partial"


def build_opaque_optimization_report(
    *,
    checkpoint: ForgeOpaqueSearchCheckpointV2,
    target_contract: Mapping[str, object],
    budget: Mapping[str, object],
    capability: Mapping[str, object],
    current_aggregates: tuple[Mapping[str, object], ...],
    pareto_front: tuple[Mapping[str, object], ...],
    termination_reason: str,
    detail: Literal["summary", "full"] = "full",
) -> OpaqueOptimizationReportV1:
    """Build a detached report without interpreting opaque recipe semantics."""

    if detail not in ("summary", "full"):
        raise ValueError("detail must be 'summary' or 'full'")
    target = OpaqueReportTargetV1.model_validate(dict(target_contract))
    stage_results = {stage.batch_fingerprint: stage for stage in checkpoint.stages}
    stages = []
    candidates = []
    for batch in sorted(
        checkpoint.batches,
        key=lambda item: (item.stage_index, item.batch_fingerprint),
    ):
        stage = stage_results.get(batch.batch_fingerprint)
        stages.append(
            OpaqueReportStageV1(
                batch_fingerprint=batch.batch_fingerprint,
                stage_index=batch.stage_index,
                parent_batch_fingerprint=batch.parent_batch_fingerprint,
                fidelity_name=batch.fidelity.name,
                fidelity_fingerprint=batch.fidelity.fidelity_fingerprint,
                fidelity_ordinal=batch.fidelity.ordinal,
                repeat_count=batch.fidelity.repeat_count,
                work_scale=batch.fidelity.work_scale,
                terminal_fidelity=batch.fidelity.terminal,
                recipe_ids=batch.recipe_ids,
                evaluated_recipe_ids=(() if stage is None else stage.evaluated_recipe_ids),
                survivor_recipe_ids=(() if stage is None else stage.survivor_recipe_ids),
                physical_duplicates=(
                    ()
                    if stage is None
                    else tuple(
                        item.model_dump(by_alias=True) for item in stage.physical_duplicates
                    )
                ),
                complete=False if stage is None else stage.complete,
            )
        )
        candidates.extend(
            _candidate(checkpoint, batch, lineage, target) for lineage in batch.recipes
        )
    candidates.sort(key=lambda item: (item.stage_index, item.candidate_key))

    current_by_recipe = {str(item["recipe_id"]): item for item in current_aggregates}
    current_candidates = {
        candidate.recipe_id: candidate
        for candidate in candidates
        if candidate.batch_fingerprint
        == (checkpoint.batches[-1].batch_fingerprint if checkpoint.batches else "")
    }
    pareto_entries = tuple(
        OpaqueReportParetoEntryV1(
            candidate_key=current_candidates[str(item["recipe_id"])].candidate_key,
            recipe_id=str(item["recipe_id"]),
            metrics=dict(item["metrics"]),
        )
        for item in sorted(pareto_front, key=lambda entry: str(entry["recipe_id"]))
    )

    baseline = current_by_recipe.get(checkpoint.baseline_recipe_id)
    objective_directions = {
        objective.name: objective.direction for objective in target.objectives
    }
    comparisons = []
    if baseline is not None and baseline.get("metrics"):
        baseline_metrics = baseline["metrics"]
        for recipe_id, aggregate in sorted(current_by_recipe.items()):
            candidate = current_candidates.get(recipe_id)
            if candidate is None:
                continue
            for metric_name, direction in objective_directions.items():
                if metric_name not in aggregate["metrics"] or metric_name not in baseline_metrics:
                    continue
                baseline_value = float(baseline_metrics[metric_name])
                candidate_value = float(aggregate["metrics"][metric_name])
                delta = candidate_value - baseline_value
                directional = -delta if direction == "min" else delta
                comparisons.append(
                    OpaqueReportBaselineComparisonV1(
                        candidate_key=candidate.candidate_key,
                        recipe_id=recipe_id,
                        metric=metric_name,
                        direction=direction,
                        baseline_value=baseline_value,
                        candidate_value=candidate_value,
                        delta=delta,
                        candidate_over_baseline=(
                            None if baseline_value == 0 else candidate_value / baseline_value
                        ),
                        directional_improvement=directional,
                        relative_directional_improvement=(
                            None if baseline_value == 0 else directional / abs(baseline_value)
                        ),
                    )
                )

    warnings = []
    status = checkpoint.status
    overall = _overall_status(status.terminal_state)
    if overall != "complete":
        warnings.append(
            OpaqueReportWarningV1(
                code="search_not_complete",
                severity="error" if overall == "failed" else "warning",
                message=(
                    "The search did not finish with complete terminal evidence; "
                    "resume or re-evaluate before treating the frontier as final."
                ),
            )
        )
    if status.terminal_fidelity_status != "complete":
        warnings.append(
            OpaqueReportWarningV1(
                code="terminal_fidelity_incomplete",
                severity="warning",
                message="No complete terminal-fidelity comparison is available.",
            )
        )
    if status.baseline_status != "available":
        warnings.append(
            OpaqueReportWarningV1(
                code="baseline_unavailable",
                severity="warning",
                message="Baseline comparison is unavailable at the current fidelity.",
            )
        )
    failed_observations = sum(len(candidate.failures) for candidate in candidates)
    if failed_observations:
        warnings.append(
            OpaqueReportWarningV1(
                code="failed_observations",
                severity="warning",
                message=(
                    f"{failed_observations} failed or memory-rejected "
                    "observations were retained."
                ),
            )
        )
    incomplete_candidates = [
        candidate for candidate in candidates if not candidate.complete
    ]
    if incomplete_candidates:
        warnings.append(
            OpaqueReportWarningV1(
                code="incomplete_candidates",
                severity="warning",
                message=(
                    f"{len(incomplete_candidates)} candidate-fidelity observations "
                    "are incomplete."
                ),
            )
        )
    if len(target.objectives) > 1:
        warnings.append(
            OpaqueReportWarningV1(
                code="pareto_not_scalarized",
                severity="info",
                message=(
                    "Multiple objectives are reported as a Pareto frontier without "
                    "a scalar winner."
                ),
            )
        )
    warnings.sort(
        key=lambda item: (
            item.code,
            -1 if item.stage_index is None else item.stage_index,
            "" if item.candidate_key is None else item.candidate_key,
        )
    )

    checkpoint_payload = checkpoint.model_dump(by_alias=True)
    checkpoint_digest = _identity("ciq-checkpoint-facts-v1:", checkpoint_payload)
    report_fields = {
        "schema": OPAQUE_OPTIMIZATION_REPORT_SCHEMA,
        "detail": detail,
        "status": OpaqueReportStatusV1(
            overall=overall,
            terminal_state=status.terminal_state,
            generation_status=status.generation_status,
            evaluation_status=status.evaluation_status,
            terminal_fidelity_status=status.terminal_fidelity_status,
            baseline_status=status.baseline_status,
            decision_status=status.decision_status,
            termination_reason=termination_reason,
        ),
        "session": OpaqueReportSessionV1(
            protocol="dynamic_batch_pareto_racing_main_thread_v2",
            session_fingerprint=checkpoint.session_fingerprint,
            baseline_recipe_id=checkpoint.baseline_recipe_id,
            deterministic_seed=checkpoint.deterministic_seed,
            dynamic_domain=checkpoint.dynamic_domain.model_dump(by_alias=True),
            evaluation_context=checkpoint.evaluation_context.model_dump(by_alias=True),
            capability=dict(capability),
        ),
        "target": target,
        "budget": OpaqueReportBudgetV1(
            evaluation_limit=int(budget["evaluation_limit"]),
            time_limit_seconds=float(budget["time_limit_seconds"]),
            materialized_memory_limit_bytes=int(
                budget["materialized_memory_limit_bytes"]
            ),
            evaluation_count=checkpoint.evaluation_count,
            elapsed_seconds=checkpoint.elapsed_seconds,
            materialized_memory_peak_bytes=max(
                (
                    record.outcome.materialized_memory_bytes
                    for record in checkpoint.records
                    if record.outcome.failure is None
                ),
                default=0,
            ),
        ),
        "stages": tuple(stages),
        "candidates": tuple(candidates),
        "pareto_front": pareto_entries,
        "baseline_comparison": tuple(comparisons),
        "warnings": tuple(warnings),
        "checkpoint": OpaqueReportCheckpointV1(
            schema=checkpoint.schema_id,
            digest=checkpoint_digest,
            embedded=detail == "full",
            payload=checkpoint_payload if detail == "full" else None,
        ),
    }
    identity_model = OpaqueOptimizationReportV1.model_construct(
        report_id="pending",
        **{
            key if key != "schema" else "schema_id": value
            for key, value in report_fields.items()
        },
    )
    report_id = _identity(
        OpaqueOptimizationReportV1.REPORT_ID_PREFIX,
        identity_model._identity_facts(),
    )
    return OpaqueOptimizationReportV1(report_id=report_id, **report_fields)


def opaque_optimization_report_json_schema() -> dict[str, object]:
    schema = OpaqueOptimizationReportV1.model_json_schema(by_alias=True)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": OPAQUE_OPTIMIZATION_REPORT_JSON_SCHEMA_ID,
        **schema,
    }


def _markdown_cell(value: object) -> str:
    text = str(value).replace("\n", " ").replace("|", "\\|")
    return text if text else "-"


def _metric_text(metrics: Mapping[str, object]) -> str:
    values = []
    for name, raw in sorted(metrics.items()):
        value = raw.median if isinstance(raw, OpaqueReportMetricSummaryV1) else raw
        values.append(f"{name}={value:.6g}")
    return ", ".join(values) or "unavailable"


def _render_markdown(report: OpaqueOptimizationReportV1) -> str:
    lines = [
        "# CompileIQ Opaque Optimization Report",
        "",
        f"- Report ID: `{report.report_id}`",
        f"- Status: `{report.status.overall}`",
        f"- Termination: `{report.status.termination_reason}`",
        f"- Decision state: `{report.status.decision_status}`",
        "",
        "## Search contract",
        "",
        f"- Session: `{report.session.session_fingerprint}`",
        f"- Provider: `{report.session.dynamic_domain['provider_namespace']}`",
        f"- Workload: `{report.session.evaluation_context['workload_context_id']}`",
        f"- Baseline: `{report.session.baseline_recipe_id}`",
        "- Objectives: "
        + ", ".join(
            f"`{item.name}` ({item.direction})" for item in report.target.objectives
        ),
        "",
        "## Budget and observations",
        "",
        "| Evaluations | Limit | Elapsed seconds | Time limit | "
        "Peak materialized bytes | Memory limit |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| {report.budget.evaluation_count} | {report.budget.evaluation_limit} | "
            f"{report.budget.elapsed_seconds:.6g} | {report.budget.time_limit_seconds:.6g} | "
            f"{report.budget.materialized_memory_peak_bytes} | "
            f"{report.budget.materialized_memory_limit_bytes} |"
        ),
        "",
        "## Pareto frontier",
        "",
    ]
    if report.pareto_front:
        lines.extend(
            [
                "| Recipe | Observed medians |",
                "| --- | --- |",
                *[
                    f"| `{_markdown_cell(item.recipe_id)}` | "
                    f"{_markdown_cell(_metric_text(item.metrics))} |"
                    for item in report.pareto_front
                ],
            ]
        )
    else:
        lines.append("No feasible Pareto candidate is available.")

    lines.extend(["", "## Stages", "", "| Stage | Fidelity | Evaluated | Survivors | Complete |"])
    lines.append("| ---: | --- | ---: | ---: | --- |")
    for stage in report.stages:
        lines.append(
            f"| {stage.stage_index} | `{_markdown_cell(stage.fidelity_name)}` | "
            f"{len(stage.evaluated_recipe_ids)} | {len(stage.survivor_recipe_ids)} | "
            f"{str(stage.complete).lower()} |"
        )

    lines.extend(
        [
            "",
            "## Candidate evidence",
            "",
            "| Stage | Recipe | Fidelity | Observations | Feasible | "
            "Observed medians | Peak bytes |",
            "| ---: | --- | --- | ---: | --- | --- | ---: |",
        ]
    )
    for candidate in report.candidates:
        lines.append(
            f"| {candidate.stage_index} | `{_markdown_cell(candidate.recipe_id)}` | "
            f"`{_markdown_cell(candidate.fidelity_name)}` | "
            f"{candidate.successful_observation_count}/{candidate.required_observation_count} | "
            f"{str(candidate.feasible).lower()} | "
            f"{_markdown_cell(_metric_text(candidate.metrics))} | "
            f"{candidate.materialized_memory_peak_bytes} |"
        )

    lines.extend(["", "## Baseline comparison", ""])
    if report.baseline_comparison:
        lines.extend(
            [
                "| Recipe | Metric | Direction | Baseline | Candidate | Delta | "
                "Relative improvement |",
                "| --- | --- | --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for item in report.baseline_comparison:
            relative = (
                "unavailable"
                if item.relative_directional_improvement is None
                else f"{item.relative_directional_improvement:.6g}"
            )
            lines.append(
                f"| `{_markdown_cell(item.recipe_id)}` | `{_markdown_cell(item.metric)}` | "
                f"{item.direction} | {item.baseline_value:.6g} | {item.candidate_value:.6g} | "
                f"{item.delta:.6g} | {relative} |"
            )
    else:
        lines.append("Baseline comparison is unavailable at the current fidelity.")

    lines.extend(["", "## Warnings", ""])
    if report.warnings:
        lines.extend(
            f"- `{warning.code}` ({warning.severity}): {warning.message}"
            for warning in report.warnings
        )
    else:
        lines.append("No report warnings.")

    if report.detail == "full":
        lines.extend(["", "## Trial record summary", ""])
        lines.append(
            f"The embedded checkpoint contains {len(report.trials)} immutable trial records."
        )
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "OPAQUE_OPTIMIZATION_REPORT_JSON_SCHEMA_ID",
    "OPAQUE_OPTIMIZATION_REPORT_RENDERER",
    "OPAQUE_OPTIMIZATION_REPORT_SCHEMA",
    "OpaqueOptimizationReportV1",
    "build_opaque_optimization_report",
    "opaque_optimization_report_json_schema",
]
