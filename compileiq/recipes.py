"""Provider-neutral opaque recipe domains for CompileIQ searches."""

from __future__ import annotations

import hashlib
from functools import cached_property
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from compileiq.search_spaces.base import choice, literal
from compileiq.search_spaces.models import ParamConfig


OPAQUE_RECIPE_DOMAIN_FINGERPRINT_KEY = "domain_fingerprint"
OPAQUE_RECIPE_ID_KEY = "recipe_id"
OPAQUE_RECIPE_SELECTION_AUDIT_SCHEMA = "compileiq.opaque-recipe-selection.v1"


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
        if not self.compileiq_capability_id.startswith("ciq-forge-cap-v1:"):
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
        return (
            f"{cls.CORE_RECIPE_TOKEN_PREFIX}" f"{ordinal:0{cls.CORE_RECIPE_TOKEN_ORDINAL_WIDTH}d}"
        )

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
