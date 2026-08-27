"""Pure replacement materialization and lifecycle intent contract.

This contract handles only an authenticated ``REPLACE_REQUIRED`` result.  It
returns independent previous/replacement records and lifecycle intents; it
does not publish state, record events, or integrate with runtime/checkpoints.
"""
from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass

from ..contracts.segment_lifecycle import (
    SegmentLifecycleContractError,
    SegmentLifecycleEventIntent,
    derive_segment_lifecycle_intents,
    filter_new_segment_lifecycle_intents,
)
from ..domain.lifecycle import EventType, StructureStatus
from ..domain.segment import Segment
from ..domain.stroke import Stroke
from ..engine.segment import SegmentEngineResult
from .segment_incremental_reconciliation import (
    SegmentIncrementalReconciliationAction,
    SegmentIncrementalReconciliationDecision,
    SegmentIncrementalReconciliationError,
    reconcile_incremental_segment,
)

__all__ = (
    "SegmentIncrementalReplacementError",
    "SegmentIncrementalReplacementLifecycleIntent",
    "SegmentIncrementalReplacementResult",
    "materialize_incremental_segment_replacement",
)


class SegmentIncrementalReplacementError(ValueError):
    """Raised when replacement materialization cannot be authenticated."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class SegmentIncrementalReplacementLifecycleIntent:
    """Pure, deterministic intent for replacing the previous Segment."""

    intent_key: str
    event_type: str
    object_type: str
    object_id: str
    logical_id: str
    occurred_at_bar_id: str
    reason_code: str
    replaced_by: str
    rule_profile: str
    rule_version: str

    def __post_init__(self) -> None:
        expected_key = (
            f"segment_replacement:{self.object_id}:"
            f"{self.replaced_by}:{self.occurred_at_bar_id}"
        )
        if (
            type(self.intent_key) is not str
            or not self.intent_key
            or self.intent_key != expected_key
            or self.event_type != EventType.STRUCTURE_REPLACED
            or self.object_type != "segment"
            or type(self.object_id) is not str
            or not self.object_id
            or type(self.logical_id) is not str
            or not self.logical_id
            or type(self.occurred_at_bar_id) is not str
            or not self.occurred_at_bar_id
            or self.reason_code != "SEGMENT_RECONCILIATION_LOGICAL_ID_CHANGED"
            or type(self.replaced_by) is not str
            or not self.replaced_by
            or type(self.rule_profile) is not str
            or not self.rule_profile
            or type(self.rule_version) is not str
            or not self.rule_version
        ):
            raise SegmentIncrementalReplacementError(
                "SEGMENT_REPLACEMENT_INTENT_INVALID"
            )


@dataclass(frozen=True)
class SegmentIncrementalReplacementResult:
    """Frozen audit envelope for one pure replacement materialization."""

    action: SegmentIncrementalReconciliationAction
    canonical_reconciliation: SegmentIncrementalReconciliationDecision
    previous_logical_id: str
    previous_object_id: str
    previous_revision: int
    previous_content_hash: str
    replacement_logical_id: str
    replacement_object_id: str
    replacement_revision: int
    replacement_content_hash: str
    replaced_previous: Segment
    replacement_segment: Segment
    replacement_segment_lifecycle_intents: tuple[SegmentLifecycleEventIntent, ...]
    previous_replacement_intent: SegmentIncrementalReplacementLifecycleIntent
    lifecycle_order: tuple[str, str, str]

    def __post_init__(self) -> None:
        if (
            type(self.action) is not SegmentIncrementalReconciliationAction
            or self.action is not SegmentIncrementalReconciliationAction.REPLACE_REQUIRED
            or type(self.canonical_reconciliation)
            is not SegmentIncrementalReconciliationDecision
            or self.canonical_reconciliation.action is not self.action
            or self.canonical_reconciliation.reason_code
            != "SEGMENT_RECONCILIATION_LOGICAL_ID_CHANGED"
        ):
            raise SegmentIncrementalReplacementError(
                "SEGMENT_REPLACEMENT_RECONCILIATION_INVALID"
            )
        if type(self.replaced_previous) is not Segment or type(self.replacement_segment) is not Segment:
            raise SegmentIncrementalReplacementError(
                "SEGMENT_REPLACEMENT_SEGMENT_INVALID"
            )
        text_fields = (
            self.previous_logical_id,
            self.previous_object_id,
            self.previous_content_hash,
            self.replacement_logical_id,
            self.replacement_object_id,
            self.replacement_content_hash,
        )
        if any(type(value) is not str or not value for value in text_fields):
            raise SegmentIncrementalReplacementError(
                "SEGMENT_REPLACEMENT_EVIDENCE_INVALID"
            )
        if (
            type(self.previous_revision) is not int
            or self.previous_revision < 1
            or type(self.replacement_revision) is not int
            or self.replacement_revision != 1
        ):
            raise SegmentIncrementalReplacementError(
                "SEGMENT_REPLACEMENT_REVISION_INVALID"
            )
        decision = self.canonical_reconciliation
        previous = self.replaced_previous
        replacement = self.replacement_segment
        if (
            self.previous_object_id == self.replacement_object_id
            or previous.object_id == replacement.object_id
        ):
            raise SegmentIncrementalReplacementError(
                "SEGMENT_REPLACEMENT_OBJECT_ID_COLLISION"
            )
        if (
            self.previous_logical_id != decision.previous_logical_id
            or self.previous_object_id != decision.previous_object_id
            or self.previous_revision != decision.previous_revision
            or self.previous_content_hash != decision.previous_content_hash
            or self.replacement_logical_id != decision.candidate_logical_id
            or self.replacement_object_id != replacement.object_id
            or self.replacement_logical_id != replacement.logical_id
            or self.replacement_revision != replacement.revision
            or self.replacement_content_hash != replacement.content_hash()
            or self.replacement_content_hash != decision.candidate_content_hash
            or previous.logical_id != self.previous_logical_id
            or previous.object_id != self.previous_object_id
            or previous.revision != self.previous_revision
            or previous.content_hash() != self.previous_content_hash
            or replacement.status is not StructureStatus.CONFIRMED
            or replacement.invalidated_at_bar is not None
            or replacement.replaced_by is not None
            or previous.status is not StructureStatus.REPLACED
            or previous.invalidated_at_bar is not None
            or previous.replaced_by != self.replacement_object_id
        ):
            raise SegmentIncrementalReplacementError(
                "SEGMENT_REPLACEMENT_EVIDENCE_MISMATCH"
            )
        if (
            replacement.revision != 1
            or replacement.object_id != f"{replacement.segment_id}_r1"
            or previous.object_id != f"{previous.segment_id}_r{previous.revision}"
        ):
            raise SegmentIncrementalReplacementError(
                "SEGMENT_REPLACEMENT_IDENTITY_INVALID"
            )
        if type(self.replacement_segment_lifecycle_intents) is not tuple or len(
            self.replacement_segment_lifecycle_intents
        ) != 2 or any(
            type(intent) is not SegmentLifecycleEventIntent
            for intent in self.replacement_segment_lifecycle_intents
        ):
            raise SegmentIncrementalReplacementError(
                "SEGMENT_REPLACEMENT_LIFECYCLE_INTENTS_INVALID"
            )
        created, confirmed = self.replacement_segment_lifecycle_intents
        try:
            filter_new_segment_lifecycle_intents(
                self.replacement_segment_lifecycle_intents,
                set(),
            )
        except SegmentLifecycleContractError as error:
            raise SegmentIncrementalReplacementError(
                "SEGMENT_REPLACEMENT_CANDIDATE_LIFECYCLE_INVALID"
            ) from error
        occurred_at = f"bar_{replacement.confirmed_at_bar + 1:06d}"
        if (
            created.event_type != EventType.CREATED
            or confirmed.event_type != EventType.CONFIRMED
            or created.object_type != "segment"
            or confirmed.object_type != "segment"
            or created.object_id != replacement.object_id
            or confirmed.object_id != replacement.object_id
            or created.logical_id != replacement.logical_id
            or confirmed.logical_id != replacement.logical_id
            or created.occurred_at_bar_id != occurred_at
            or confirmed.occurred_at_bar_id != occurred_at
            or created.reason_code != "SEGMENT_FIRST_CASE_CREATED"
            or confirmed.reason_code != "SEGMENT_FIRST_CASE_CONFIRMED"
            or created.rule_profile != replacement.rule_profile
            or confirmed.rule_profile != replacement.rule_profile
            or created.rule_version != replacement.rule_version
            or confirmed.rule_version != replacement.rule_version
        ):
            raise SegmentIncrementalReplacementError(
                "SEGMENT_REPLACEMENT_CANDIDATE_LIFECYCLE_INVALID"
            )
        intent = self.previous_replacement_intent
        if type(intent) is not SegmentIncrementalReplacementLifecycleIntent:
            raise SegmentIncrementalReplacementError(
                "SEGMENT_REPLACEMENT_INTENT_INVALID"
            )
        if (
            intent.object_id != self.previous_object_id
            or intent.logical_id != self.previous_logical_id
            or intent.replaced_by != self.replacement_object_id
            or intent.occurred_at_bar_id != occurred_at
            or intent.rule_profile != previous.rule_profile
            or intent.rule_version != previous.rule_version
        ):
            raise SegmentIncrementalReplacementError(
                "SEGMENT_REPLACEMENT_INTENT_EVIDENCE_MISMATCH"
            )
        if self.lifecycle_order != (
            EventType.CREATED,
            EventType.CONFIRMED,
            EventType.STRUCTURE_REPLACED,
        ):
            raise SegmentIncrementalReplacementError(
                "SEGMENT_REPLACEMENT_LIFECYCLE_ORDER_INVALID"
            )


def materialize_incremental_segment_replacement(
    *,
    previous: Segment,
    current: SegmentEngineResult,
    source_strokes: Sequence[Stroke],
) -> SegmentIncrementalReplacementResult:
    """Materialize only an authenticated REPLACE_REQUIRED result."""

    if type(previous) is not Segment or type(current) is not SegmentEngineResult:
        raise SegmentIncrementalReplacementError(
            "SEGMENT_REPLACEMENT_INPUT_TYPE_INVALID"
        )
    try:
        decision = reconcile_incremental_segment(
            previous=previous,
            current=current,
            source_strokes=source_strokes,
        )
    except SegmentIncrementalReconciliationError as error:
        raise SegmentIncrementalReplacementError(error.reason_code) from error
    if decision.action is not SegmentIncrementalReconciliationAction.REPLACE_REQUIRED:
        raise SegmentIncrementalReplacementError(
            "SEGMENT_REPLACEMENT_ACTION_UNSUPPORTED"
        )
    candidate = current.segment
    if type(candidate) is not Segment:
        raise SegmentIncrementalReplacementError(
            "SEGMENT_REPLACEMENT_CANDIDATE_REQUIRED"
        )
    if candidate.revision != 1 or candidate.object_id != f"{candidate.segment_id}_r1":
        raise SegmentIncrementalReplacementError(
            "SEGMENT_REPLACEMENT_CANDIDATE_R1_IDENTITY_INVALID"
        )
    if previous.object_id != f"{previous.segment_id}_r{previous.revision}":
        raise SegmentIncrementalReplacementError(
            "SEGMENT_REPLACEMENT_PREVIOUS_RN_IDENTITY_INVALID"
        )
    if previous.object_id == candidate.object_id:
        raise SegmentIncrementalReplacementError(
            "SEGMENT_REPLACEMENT_OBJECT_ID_COLLISION"
        )

    replacement = deepcopy(candidate)
    replaced_previous = deepcopy(previous)
    replaced_previous.mark_replaced(replacement.object_id)
    try:
        candidate_intents = derive_segment_lifecycle_intents(
            outcome_code=current.reason_code,
            segment=replacement,
            primary_evidence=current.primary_evidence,
        )
    except SegmentLifecycleContractError as error:
        raise SegmentIncrementalReplacementError(
            "SEGMENT_REPLACEMENT_CANDIDATE_LIFECYCLE_INVALID"
        ) from error
    if len(candidate_intents) != 2:
        raise SegmentIncrementalReplacementError(
            "SEGMENT_REPLACEMENT_CANDIDATE_LIFECYCLE_INVALID"
        )
    occurred_at = f"bar_{replacement.confirmed_at_bar + 1:06d}"
    previous_intent = SegmentIncrementalReplacementLifecycleIntent(
        intent_key=(
            f"segment_replacement:{replaced_previous.object_id}:"
            f"{replacement.object_id}:{occurred_at}"
        ),
        event_type=EventType.STRUCTURE_REPLACED,
        object_type="segment",
        object_id=replaced_previous.object_id,
        logical_id=replaced_previous.logical_id,
        occurred_at_bar_id=occurred_at,
        reason_code="SEGMENT_RECONCILIATION_LOGICAL_ID_CHANGED",
        replaced_by=replacement.object_id,
        rule_profile=replaced_previous.rule_profile,
        rule_version=replaced_previous.rule_version,
    )
    return SegmentIncrementalReplacementResult(
        action=decision.action,
        canonical_reconciliation=decision,
        previous_logical_id=previous.logical_id,
        previous_object_id=previous.object_id,
        previous_revision=previous.revision,
        previous_content_hash=previous.content_hash(),
        replacement_logical_id=replacement.logical_id,
        replacement_object_id=replacement.object_id,
        replacement_revision=replacement.revision,
        replacement_content_hash=replacement.content_hash(),
        replaced_previous=replaced_previous,
        replacement_segment=replacement,
        replacement_segment_lifecycle_intents=candidate_intents,
        previous_replacement_intent=previous_intent,
        lifecycle_order=(
            EventType.CREATED,
            EventType.CONFIRMED,
            EventType.STRUCTURE_REPLACED,
        ),
    )
