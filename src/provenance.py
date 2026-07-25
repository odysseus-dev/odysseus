"""Structured origin and sensitivity labels for model-visible context."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping


class ProvenanceOrigin(str, Enum):
    SYSTEM = "system"
    ODYSSEUS = "odysseus"
    WORKSPACE = "workspace"
    EXTERNAL = "external"


class ContextSensitivity(str, Enum):
    PUBLIC = "public"
    WORKSPACE = "workspace"
    PRIVATE = "private"


@dataclass
class ConversationProvenance:
    external_untrusted_context_seen: bool = False
    workspace_untrusted_context_seen: bool = False
    odysseus_untrusted_context_seen: bool = False
    private_data_context_seen: bool = False

    @property
    def any_untrusted_context_seen(self) -> bool:
        return bool(
            self.external_untrusted_context_seen
            or self.workspace_untrusted_context_seen
            or self.odysseus_untrusted_context_seen
        )

    def merge(self, other: "ConversationProvenance") -> bool:
        before = self.to_dict()
        self.external_untrusted_context_seen |= (
            other.external_untrusted_context_seen
        )
        self.workspace_untrusted_context_seen |= (
            other.workspace_untrusted_context_seen
        )
        self.odysseus_untrusted_context_seen |= (
            other.odysseus_untrusted_context_seen
        )
        self.private_data_context_seen |= other.private_data_context_seen
        return self.to_dict() != before

    def labels(self) -> tuple[str, ...]:
        labels: list[str] = []
        if self.external_untrusted_context_seen:
            labels.append("external_untrusted")
        if self.workspace_untrusted_context_seen:
            labels.append("workspace_untrusted")
        if self.odysseus_untrusted_context_seen:
            labels.append("odysseus_untrusted")
        if self.private_data_context_seen:
            labels.append("private_data")
        return tuple(labels)

    def to_dict(self) -> dict[str, bool]:
        return {
            "external_untrusted_context_seen": bool(
                self.external_untrusted_context_seen
            ),
            "workspace_untrusted_context_seen": bool(
                self.workspace_untrusted_context_seen
            ),
            "odysseus_untrusted_context_seen": bool(
                self.odysseus_untrusted_context_seen
            ),
            "private_data_context_seen": bool(self.private_data_context_seen),
        }

    @classmethod
    def from_labels(cls, labels: Iterable[str] | None) -> "ConversationProvenance":
        values = {str(label) for label in labels or ()}
        return cls(
            external_untrusted_context_seen="external_untrusted" in values,
            workspace_untrusted_context_seen="workspace_untrusted" in values,
            odysseus_untrusted_context_seen="odysseus_untrusted" in values,
            private_data_context_seen="private_data" in values,
        )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any] | None,
    ) -> "ConversationProvenance":
        value = value if isinstance(value, Mapping) else {}
        return cls(
            external_untrusted_context_seen=bool(
                value.get("external_untrusted_context_seen")
            ),
            workspace_untrusted_context_seen=bool(
                value.get("workspace_untrusted_context_seen")
            ),
            odysseus_untrusted_context_seen=bool(
                value.get("odysseus_untrusted_context_seen")
            ),
            private_data_context_seen=bool(
                value.get("private_data_context_seen")
            ),
        )


def provenance_from_messages(
    messages: Iterable[dict] | None,
) -> ConversationProvenance:
    """Derive monotonic state only from explicit server-owned metadata."""
    state = ConversationProvenance()
    for message in messages or ():
        if not isinstance(message, dict):
            continue
        metadata = message.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("trusted") is not False:
            continue
        try:
            origin = ProvenanceOrigin(metadata.get("provenance_origin"))
        except (TypeError, ValueError):
            # Legacy untrusted wrappers were predominantly external. Preserve
            # the old fail-high behavior until every saved message is labelled.
            origin = ProvenanceOrigin.EXTERNAL
        try:
            sensitivity = ContextSensitivity(metadata.get("sensitivity"))
        except (TypeError, ValueError):
            sensitivity = ContextSensitivity.PUBLIC

        if origin is ProvenanceOrigin.EXTERNAL:
            state.external_untrusted_context_seen = True
        elif origin is ProvenanceOrigin.WORKSPACE:
            state.workspace_untrusted_context_seen = True
        elif origin is ProvenanceOrigin.ODYSSEUS:
            state.odysseus_untrusted_context_seen = True
        if sensitivity is ContextSensitivity.PRIVATE:
            state.private_data_context_seen = True
    return state
