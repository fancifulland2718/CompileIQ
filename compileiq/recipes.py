"""Provider-neutral opaque recipe domains for CompileIQ searches."""

from __future__ import annotations

import hashlib
import json
import math
from functools import cached_property
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from compileiq.search_spaces.base import choice, literal
from compileiq.search_spaces.models import ParamConfig


OPAQUE_RECIPE_DOMAIN_FINGERPRINT_KEY = "domain_fingerprint"
OPAQUE_RECIPE_ID_KEY = "recipe_id"
OPAQUE_RECIPE_SELECTION_AUDIT_SCHEMA = "compileiq.opaque-recipe-selection.v1"
OPAQUE_RECIPE_BATCH_SCHEMA = "compileiq.opaque-recipe-batch.v2"
OPAQUE_RECIPE_FIDELITY_SCHEMA = "compileiq.opaque-recipe-fidelity.v2"
OPAQUE_RECIPE_LINEAGE_SCHEMA = "compileiq.opaque-recipe-lineage.v2"


def _canonical_identity(prefix: str, payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return prefix + hashlib.sha256(encoded).hexdigest()


def _bounded_nonempty_text(value: object, *, field_name: str, limit: int = 4096) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be nonempty text")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field_name} must be valid UTF-8 text") from exc
    if len(encoded) > limit:
        raise ValueError(f"{field_name} exceeds the {limit} byte limit")
    return value


class OpaqueRecipeDomainV1(BaseModel):
    """A bounded, immutable set of provider-owned opaque recipe identifiers.

    CompileIQ treats every recipe identifier and provider fingerprint as opaque
    text. Only exact identity, boundedness, and membership are validated here.
    """

    SCHEMA: ClassVar[str] = "compileiq.opaque-recipe-domain.v1"
    FINGERPRINT_DOMAIN: ClassVar[bytes] = b"ciq-domain-v1"
    FINGERPRINT_PREFIX: ClassVar[str] = "ciq-domain-v1:"
    CORE_RECIPE_TOKEN_PREFIX: ClassVar[str] = "ciq-recipe-v1-"
    CORE_RECIPE_TOKEN_ORDINAL_WIDTH: ClassVar[int] = 4
    MAX_RECIPE_IDS: ClassVar[int] = 4096
    MAX_FIELD_UTF8_BYTES: ClassVar[int] = 4096
    MAX_CANONICAL_BYTES: ClassVar[int] = 4 * 1024 * 1024

    schema_id: Literal["compileiq.opaque-recipe-domain.v1"] = Field(
        default=SCHEMA,
        alias="schema",
    )
    provider_namespace: str
    domain_version: str
    provider_semantic_fingerprint: str
    compileiq_capability_id: str
    compileiq_core_commit: str
    compileiq_core_lock: str
    recipe_ids: tuple[str, ...]

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )

    @field_validator("recipe_ids", mode="before")
    @classmethod
    def _normalize_recipe_ids_container(cls, value: Any) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("recipe_ids must be a list or tuple of opaque strings")
        return tuple(value)

    @model_validator(mode="after")
    def _validate_and_canonicalize(self) -> "OpaqueRecipeDomainV1":
        encoded_fields = [
            self._encode_nonempty("schema", self.schema_id),
            self._encode_nonempty("provider_namespace", self.provider_namespace),
            self._encode_nonempty("domain_version", self.domain_version),
            self._encode_nonempty(
                "provider_semantic_fingerprint", self.provider_semantic_fingerprint
            ),
            self._encode_nonempty("compileiq_capability_id", self.compileiq_capability_id),
            self._encode_nonempty("compileiq_core_commit", self.compileiq_core_commit),
            self._encode_nonempty("compileiq_core_lock", self.compileiq_core_lock),
        ]
        if not self.compileiq_capability_id.startswith(("ciq-forge-cap-v1:", "ciq-forge-cap-v2:")):
            raise ValueError("compileiq_capability_id is not a Forge capability identity")
        if not self.compileiq_core_lock.startswith("sha256:"):
            raise ValueError("compileiq_core_lock is not a sha256 identity")

        if not self.recipe_ids:
            raise ValueError("recipe_ids must contain at least one opaque recipe identifier")
        if len(self.recipe_ids) > self.MAX_RECIPE_IDS:
            raise ValueError(f"recipe_ids exceeds the {self.MAX_RECIPE_IDS} item limit")

        encoded_ids: list[tuple[bytes, str]] = []
        seen: set[str] = set()
        for recipe_id in self.recipe_ids:
            if not isinstance(recipe_id, str):
                raise ValueError("every opaque recipe identifier must be an exact string")
            encoded = self._encode_nonempty("recipe_id", recipe_id)
            if recipe_id in seen:
                raise ValueError(f"duplicate opaque recipe identifier: {recipe_id!r}")
            seen.add(recipe_id)
            encoded_ids.append((encoded, recipe_id))

        encoded_ids.sort(key=lambda item: item[0])
        canonical_ids = tuple(recipe_id for _, recipe_id in encoded_ids)
        canonical_size = sum(8 + len(field) for field in encoded_fields)
        canonical_size += 8 + sum(8 + len(encoded) for encoded, _ in encoded_ids)
        canonical_size += 8 + len(self.FINGERPRINT_DOMAIN)
        if canonical_size > self.MAX_CANONICAL_BYTES:
            raise ValueError(
                f"opaque recipe domain exceeds the {self.MAX_CANONICAL_BYTES} byte limit"
            )

        object.__setattr__(self, "recipe_ids", canonical_ids)
        return self

    @classmethod
    def _encode_nonempty(cls, field_name: str, value: str) -> bytes:
        if not isinstance(value, str):
            raise ValueError(f"{field_name} must be an exact string")
        if not value:
            raise ValueError(f"{field_name} must not be empty")
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError(f"{field_name} must be valid UTF-8 text") from exc
        if len(encoded) > cls.MAX_FIELD_UTF8_BYTES:
            raise ValueError(f"{field_name} exceeds the {cls.MAX_FIELD_UTF8_BYTES} byte limit")
        return encoded

    @staticmethod
    def _length_prefix(value: bytes) -> bytes:
        return len(value).to_bytes(8, byteorder="big", signed=False) + value

    @cached_property
    def domain_fingerprint(self) -> str:
        """Return the canonical v1 fingerprint for this exact provider domain.

        The SHA-256 preimage is ``LP("ciq-domain-v1") || LP(schema) ||
        LP(provider_namespace) || LP(domain_version) ||
        LP(provider_semantic_fingerprint) || LP(compileiq_capability_id) ||
        LP(compileiq_core_commit) || LP(compileiq_core_lock) || U64(recipe_count) ||
        LP(recipe_id_0) ...``. ``LP`` is an unsigned big-endian 64-bit UTF-8
        byte length followed by those bytes, and recipe IDs are UTF-8 sorted.
        """

        preimage = bytearray(self._length_prefix(self.FINGERPRINT_DOMAIN))
        preimage.extend(self._length_prefix(self.schema_id.encode("utf-8")))
        preimage.extend(self._length_prefix(self.provider_namespace.encode("utf-8")))
        preimage.extend(self._length_prefix(self.domain_version.encode("utf-8")))
        preimage.extend(self._length_prefix(self.provider_semantic_fingerprint.encode("utf-8")))
        preimage.extend(self._length_prefix(self.compileiq_capability_id.encode("utf-8")))
        preimage.extend(self._length_prefix(self.compileiq_core_commit.encode("utf-8")))
        preimage.extend(self._length_prefix(self.compileiq_core_lock.encode("utf-8")))
        preimage.extend(len(self.recipe_ids).to_bytes(8, byteorder="big", signed=False))
        for recipe_id in self.recipe_ids:
            preimage.extend(self._length_prefix(recipe_id.encode("utf-8")))
        return self.FINGERPRINT_PREFIX + hashlib.sha256(preimage).hexdigest()

    @classmethod
    def _core_recipe_token(cls, ordinal: int) -> str:
        return f"{cls.CORE_RECIPE_TOKEN_PREFIX}{ordinal:0{cls.CORE_RECIPE_TOKEN_ORDINAL_WIDTH}d}"

    @cached_property
    def _core_recipe_tokens(self) -> tuple[str, ...]:
        return tuple(self._core_recipe_token(i) for i in range(len(self.recipe_ids)))

    def to_search_space(self) -> dict[str, ParamConfig]:
        """Compile this domain to safe existing literal and choice primitives.

        Provider-owned identifiers never cross the core boundary. The fixed
        ASCII tokens encode only the ordinal in the canonical recipe-ID set.
        """

        return {
            OPAQUE_RECIPE_DOMAIN_FINGERPRINT_KEY: literal(self.domain_fingerprint),
            OPAQUE_RECIPE_ID_KEY: choice(self._core_recipe_tokens),
        }

    def decode_candidate(self, candidate: object) -> dict[str, str]:
        """Validate a core candidate and restore its provider-owned recipe ID."""

        decoded, _ = self.decode_candidate_with_audit(candidate)
        return decoded

    def decode_candidate_with_audit(
        self, candidate: object
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Decode one core token and retain an auditable raw-to-provider mapping."""

        if not isinstance(candidate, dict):
            raise RuntimeError("Opaque recipe candidate must be a JSON object")
        expected_keys = {
            OPAQUE_RECIPE_DOMAIN_FINGERPRINT_KEY,
            OPAQUE_RECIPE_ID_KEY,
        }
        if set(candidate) != expected_keys:
            raise RuntimeError("Opaque recipe candidate has missing or unexpected fields")

        fingerprint = candidate[OPAQUE_RECIPE_DOMAIN_FINGERPRINT_KEY]
        if not isinstance(fingerprint, str) or fingerprint != self.domain_fingerprint:
            raise RuntimeError("Opaque recipe candidate domain fingerprint mismatch")

        token = candidate[OPAQUE_RECIPE_ID_KEY]
        prefix_length = len(self.CORE_RECIPE_TOKEN_PREFIX)
        if not isinstance(token, str) or len(token) != (
            prefix_length + self.CORE_RECIPE_TOKEN_ORDINAL_WIDTH
        ):
            raise RuntimeError("Opaque recipe candidate recipe token is outside the domain")
        if not token.startswith(self.CORE_RECIPE_TOKEN_PREFIX):
            raise RuntimeError("Opaque recipe candidate recipe token is outside the domain")

        encoded_ordinal = token[prefix_length:]
        if not all("0" <= char <= "9" for char in encoded_ordinal):
            raise RuntimeError("Opaque recipe candidate recipe token is outside the domain")
        ordinal = int(encoded_ordinal)
        if ordinal >= len(self.recipe_ids) or token != self._core_recipe_token(ordinal):
            raise RuntimeError("Opaque recipe candidate recipe token is outside the domain")

        decoded = {
            OPAQUE_RECIPE_DOMAIN_FINGERPRINT_KEY: self.domain_fingerprint,
            OPAQUE_RECIPE_ID_KEY: self.recipe_ids[ordinal],
        }
        audit = {
            "schema": OPAQUE_RECIPE_SELECTION_AUDIT_SCHEMA,
            "provider_namespace": self.provider_namespace,
            "domain_version": self.domain_version,
            "provider_semantic_fingerprint": self.provider_semantic_fingerprint,
            "compileiq_capability_id": self.compileiq_capability_id,
            "compileiq_core_commit": self.compileiq_core_commit,
            "compileiq_core_lock": self.compileiq_core_lock,
            OPAQUE_RECIPE_DOMAIN_FINGERPRINT_KEY: self.domain_fingerprint,
            "core_recipe_token": token,
            OPAQUE_RECIPE_ID_KEY: self.recipe_ids[ordinal],
        }
        return decoded, audit


class OpaqueRecipeLineageV2(BaseModel):
    """One complete provider recipe and its immediate parent recipes."""

    SCHEMA: ClassVar[str] = OPAQUE_RECIPE_LINEAGE_SCHEMA

    schema_id: Literal["compileiq.opaque-recipe-lineage.v2"] = Field(
        default=SCHEMA,
        alias="schema",
    )
    recipe_id: str
    parent_recipe_ids: tuple[str, ...] = ()
    estimated_materialized_bytes: int = 0

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )

    @field_validator("parent_recipe_ids", mode="before")
    @classmethod
    def _normalize_parents(cls, value: Any) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("parent_recipe_ids must be a list or tuple")
        return tuple(value)

    @model_validator(mode="after")
    def _validate_lineage(self) -> "OpaqueRecipeLineageV2":
        _bounded_nonempty_text(self.recipe_id, field_name="recipe_id")
        parents = []
        seen = set()
        for parent in self.parent_recipe_ids:
            _bounded_nonempty_text(parent, field_name="parent_recipe_id")
            if parent in seen:
                raise ValueError(f"duplicate parent recipe identifier: {parent!r}")
            seen.add(parent)
            parents.append(parent)
        parents.sort(key=lambda item: item.encode("utf-8"))
        object.__setattr__(self, "parent_recipe_ids", tuple(parents))
        if (
            isinstance(self.estimated_materialized_bytes, bool)
            or not isinstance(self.estimated_materialized_bytes, int)
            or self.estimated_materialized_bytes < 0
        ):
            raise ValueError("estimated_materialized_bytes must be a nonnegative integer")
        return self


class OpaqueRecipeFidelityV2(BaseModel):
    """Provider-defined measurement fidelity with an explicit repeat count."""

    SCHEMA: ClassVar[str] = OPAQUE_RECIPE_FIDELITY_SCHEMA

    schema_id: Literal["compileiq.opaque-recipe-fidelity.v2"] = Field(
        default=SCHEMA,
        alias="schema",
    )
    name: str
    ordinal: int
    repeat_count: int
    work_scale: float = 1.0

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )

    @model_validator(mode="after")
    def _validate_fidelity(self) -> "OpaqueRecipeFidelityV2":
        _bounded_nonempty_text(self.name, field_name="fidelity name", limit=128)
        if isinstance(self.ordinal, bool) or self.ordinal < 0:
            raise ValueError("fidelity ordinal must be a nonnegative integer")
        if isinstance(self.repeat_count, bool) or self.repeat_count < 1 or self.repeat_count > 64:
            raise ValueError("fidelity repeat_count must be between 1 and 64")
        if (
            isinstance(self.work_scale, bool)
            or not isinstance(self.work_scale, (int, float))
            or not math.isfinite(float(self.work_scale))
            or float(self.work_scale) <= 0
        ):
            raise ValueError("fidelity work_scale must be finite and positive")
        object.__setattr__(self, "work_scale", float(self.work_scale))
        return self

    @cached_property
    def fidelity_fingerprint(self) -> str:
        return _canonical_identity(
            "ciq-fidelity-v2:",
            self.model_dump(by_alias=True),
        )


class OpaqueRecipeBatchV2(BaseModel):
    """One canonical batch of complete recipes in a staged opaque search.

    The provider supplies complete recipe identifiers and immediate lineage.
    CompileIQ owns only deterministic ordering, bounded evaluation, and the
    stage chain. Recipe contents remain opaque and never cross the core token
    boundary.
    """

    SCHEMA: ClassVar[str] = OPAQUE_RECIPE_BATCH_SCHEMA
    FINGERPRINT_PREFIX: ClassVar[str] = "ciq-batch-v2:"
    MAX_RECIPES: ClassVar[int] = OpaqueRecipeDomainV1.MAX_RECIPE_IDS

    schema_id: Literal["compileiq.opaque-recipe-batch.v2"] = Field(
        default=SCHEMA,
        alias="schema",
    )
    provider_namespace: str
    domain_version: str
    provider_semantic_fingerprint: str
    compileiq_capability_id: str
    compileiq_core_commit: str
    compileiq_core_lock: str
    stage_index: int
    stage_fingerprint: str
    parent_batch_fingerprint: str | None = None
    fidelity: OpaqueRecipeFidelityV2
    recipes: tuple[OpaqueRecipeLineageV2, ...]

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        strict=True,
    )

    @field_validator("recipes", mode="before")
    @classmethod
    def _normalize_recipes(cls, value: Any) -> tuple[object, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("recipes must be a list or tuple")
        return tuple(value)

    @model_validator(mode="after")
    def _validate_batch(self) -> "OpaqueRecipeBatchV2":
        for name in (
            "provider_namespace",
            "domain_version",
            "provider_semantic_fingerprint",
            "compileiq_capability_id",
            "compileiq_core_commit",
            "compileiq_core_lock",
            "stage_fingerprint",
        ):
            _bounded_nonempty_text(getattr(self, name), field_name=name)
        if not self.compileiq_capability_id.startswith(("ciq-forge-cap-v1:", "ciq-forge-cap-v2:")):
            raise ValueError("compileiq_capability_id is not a Forge capability identity")
        if not self.compileiq_core_lock.startswith("sha256:"):
            raise ValueError("compileiq_core_lock is not a sha256 identity")
        if isinstance(self.stage_index, bool) or self.stage_index < 0:
            raise ValueError("stage_index must be a nonnegative integer")
        if not self.recipes:
            raise ValueError("opaque recipe batch must contain at least one recipe")
        if len(self.recipes) > self.MAX_RECIPES:
            raise ValueError(f"opaque recipe batch exceeds the {self.MAX_RECIPES} item limit")

        recipes = sorted(self.recipes, key=lambda item: item.recipe_id.encode("utf-8"))
        recipe_ids = tuple(item.recipe_id for item in recipes)
        if len(set(recipe_ids)) != len(recipe_ids):
            raise ValueError("opaque recipe batch contains duplicate recipe identifiers")
        object.__setattr__(self, "recipes", tuple(recipes))

        if self.stage_index == 0:
            if self.parent_batch_fingerprint is not None:
                raise ValueError("stage zero must not declare a parent batch")
            if any(item.parent_recipe_ids for item in recipes):
                raise ValueError("stage-zero recipes must not declare parents")
        else:
            if not isinstance(
                self.parent_batch_fingerprint, str
            ) or not self.parent_batch_fingerprint.startswith(self.FINGERPRINT_PREFIX):
                raise ValueError("nonzero stages require a parent batch fingerprint")
            if any(not item.parent_recipe_ids for item in recipes):
                raise ValueError("nonzero-stage recipes require immediate parent lineage")
        return self

    @cached_property
    def recipe_ids(self) -> tuple[str, ...]:
        return tuple(item.recipe_id for item in self.recipes)

    @cached_property
    def batch_fingerprint(self) -> str:
        return _canonical_identity(
            self.FINGERPRINT_PREFIX,
            self.model_dump(by_alias=True),
        )

    def recipe(self, recipe_id: str) -> OpaqueRecipeLineageV2:
        for recipe in self.recipes:
            if recipe.recipe_id == recipe_id:
                return recipe
        raise KeyError(f"unknown opaque batch recipe {recipe_id!r}")

    def to_domain_v1(self) -> OpaqueRecipeDomainV1:
        """Compile this batch through the existing safe ordinal-token codec."""

        return OpaqueRecipeDomainV1(
            provider_namespace=self.provider_namespace,
            domain_version=(
                f"{self.domain_version}:stage={self.stage_index}:"
                f"fidelity={self.fidelity.fidelity_fingerprint}"
            ),
            provider_semantic_fingerprint=self.batch_fingerprint,
            compileiq_capability_id=self.compileiq_capability_id,
            compileiq_core_commit=self.compileiq_core_commit,
            compileiq_core_lock=self.compileiq_core_lock,
            recipe_ids=self.recipe_ids,
        )


__all__ = [
    "OPAQUE_RECIPE_BATCH_SCHEMA",
    "OPAQUE_RECIPE_DOMAIN_FINGERPRINT_KEY",
    "OPAQUE_RECIPE_FIDELITY_SCHEMA",
    "OPAQUE_RECIPE_ID_KEY",
    "OPAQUE_RECIPE_LINEAGE_SCHEMA",
    "OPAQUE_RECIPE_SELECTION_AUDIT_SCHEMA",
    "OpaqueRecipeBatchV2",
    "OpaqueRecipeDomainV1",
    "OpaqueRecipeFidelityV2",
    "OpaqueRecipeLineageV2",
]
