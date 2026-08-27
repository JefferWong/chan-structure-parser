"""Pure materialization of authenticated incremental Segment identities.

This contract consumes an already-evaluated first-case ``SegmentEngineResult``
and authoritative source ``Stroke`` records.  It delegates classification to
``reconcile_incremental_segment`` and only materializes the two supported
actions: REUSE and REVISE.  It does not publish state, emit events, or wire
into an engine, parser, checkpoint, or lifecycle runtime.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Sequence

from ..domain.lifecycle import StructureStatus
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
    "SegmentIncrementalMaterializationError",
    "SegmentIncrementalMaterializationResult",
    "materialize_incremental_segment",
)


class SegmentIncrementalMaterializationError(ValueError):
    """Raised when a REUSE/REVISE materialization cannot be authenticated."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class SegmentIncrementalMaterializationResult:
    """Frozen audit envelope for one independent Segment materialization."""

    action: SegmentIncrementalReconciliationAction
    previous_logical_id: str
    previous_object_id: str
    previous_revision: int
    materialized_logical_id: str
    materialized_object_id: str
    materialized_revision: int
    previous_content_hash: str
    materialized_content_hash: str
    canonical_reconciliation: SegmentIncrementalReconciliationDecision
    materialized_segment: Segment

    def __post_init__(self) -> None:
        if type(self.action) is not SegmentIncrementalReconciliationAction:
            raise SegmentIncrementalMaterializationError(
                "SEGMENT_MATERIALIZATION_ACTION_INVALID"
            )
        if self.action not in {
            SegmentIncrementalReconciliationAction.REUSE,
            SegmentIncrementalReconciliationAction.REVISE,
        }:
            raise SegmentIncrementalMaterializationError(
                "SEGMENT_MATERIALIZATION_ACTION_UNSUPPORTED"
            )
        if type(self.canonical_reconciliation) is not SegmentIncrementalReconciliationDecision:
            raise SegmentIncrementalMaterializationError(
                "SEGMENT_MATERIALIZATION_DECISION_INVALID"
            )
        if self.canonical_reconciliation.action is not self.action:
            raise SegmentIncrementalMaterializationError(
                "SEGMENT_MATERIALIZATION_DECISION_ACTION_MISMATCH"
            )
        if type(self.materialized_segment) is not Segment:
            raise SegmentIncrementalMaterializationError(
                "SEGMENT_MATERIALIZATION_SEGMENT_INVALID"
            )
        fields = (
            self.previous_logical_id,
            self.previous_object_id,
            self.materialized_logical_id,
            self.materialized_object_id,
            self.previous_content_hash,
            self.materialized_content_hash,
        )
        if any(type(value) is not str or not value for value in fields):
            raise SegmentIncrementalMaterializationError(
                "SEGMENT_MATERIALIZATION_IDENTITY_FIELDS_INVALID"
            )
        if (
            type(self.previous_revision) is not int
            or self.previous_revision < 1
            or type(self.materialized_revision) is not int
            or self.materialized_revision < 1
        ):
            raise SegmentIncrementalMaterializationError(
                "SEGMENT_MATERIALIZATION_REVISION_INVALID"
            )
        decision = self.canonical_reconciliation
        materialized = self.materialized_segment
        if (
            self.previous_logical_id != decision.previous_logical_id
            or self.previous_object_id != decision.previous_object_id
            or self.previous_revision != decision.previous_revision
            or self.previous_content_hash != decision.previous_content_hash
            or self.materialized_logical_id != decision.candidate_logical_id
            or self.materialized_logical_id != materialized.logical_id
            or self.materialized_object_id != materialized.object_id
            or self.materialized_revision != materialized.revision
            or self.materialized_revision != decision.next_revision
            or self.materialized_content_hash != materialized.content_hash()
            or self.materialized_content_hash != decision.candidate_content_hash
        ):
            raise SegmentIncrementalMaterializationError(
                "SEGMENT_MATERIALIZATION_EVIDENCE_MISMATCH"
            )
        if self.action is SegmentIncrementalReconciliationAction.REUSE:
            valid_action_evidence = (
                self.materialized_logical_id == self.previous_logical_id
                and self.materialized_object_id == self.previous_object_id
                and self.materialized_revision == self.previous_revision
                and self.materialized_content_hash == self.previous_content_hash
            )
        else:
            valid_action_evidence = (
                self.materialized_logical_id == self.previous_logical_id
                and self.materialized_revision == self.previous_revision + 1
                and self.materialized_revision == decision.next_revision
                and self.materialized_object_id
                == f"{materialized.segment_id}_r{self.materialized_revision}"
            )
        if not valid_action_evidence:
            raise SegmentIncrementalMaterializationError(
                "SEGMENT_MATERIALIZATION_ACTION_EVIDENCE_INVALID"
            )


def materialize_incremental_segment(
    *,
    previous: Segment,
    current: SegmentEngineResult,
    source_strokes: Sequence[Stroke],
) -> SegmentIncrementalMaterializationResult:
    """Materialize only an authenticated REUSE or REVISE result.

    ``previous``, ``current.segment``, and ``source_strokes`` remain untouched.
    The returned Segment owns all mutable collections independently of both
    caller inputs.
    """

    try:
        decision = reconcile_incremental_segment(
            previous=previous,
            current=current,
            source_strokes=source_strokes,
        )
    except SegmentIncrementalReconciliationError as error:
        raise SegmentIncrementalMaterializationError(error.reason_code) from error

    if decision.action not in {
        SegmentIncrementalReconciliationAction.REUSE,
        SegmentIncrementalReconciliationAction.REVISE,
    }:
        raise SegmentIncrementalMaterializationError(
            "SEGMENT_MATERIALIZATION_ACTION_UNSUPPORTED"
        )
    if type(previous) is not Segment or type(current) is not SegmentEngineResult:
        raise SegmentIncrementalMaterializationError(
            "SEGMENT_MATERIALIZATION_INPUT_TYPE_INVALID"
        )
    candidate = current.segment
    if type(candidate) is not Segment:
        raise SegmentIncrementalMaterializationError(
            "SEGMENT_MATERIALIZATION_CANDIDATE_REQUIRED"
        )
    if candidate.object_id != f"{candidate.segment_id}_r1" or candidate.revision != 1:
        raise SegmentIncrementalMaterializationError(
            "SEGMENT_MATERIALIZATION_FIRST_REVISION_IDENTITY_INVALID"
        )
    if previous.object_id != f"{previous.segment_id}_r{previous.revision}":
        raise SegmentIncrementalMaterializationError(
            "SEGMENT_MATERIALIZATION_PREVIOUS_REVISION_IDENTITY_INVALID"
        )

    if decision.action is SegmentIncrementalReconciliationAction.REUSE:
        materialized = deepcopy(previous)
        expected_logical_id = previous.logical_id
        expected_object_id = previous.object_id
        expected_revision = previous.revision
        expected_hash = previous.content_hash()
    else:
        materialized = deepcopy(candidate)
        expected_logical_id = previous.logical_id
        expected_revision = previous.revision + 1
        expected_object_id = f"{candidate.segment_id}_r{expected_revision}"
        materialized.logical_id = expected_logical_id
        materialized.revision = expected_revision
        materialized.object_id = expected_object_id
        materialized.status = StructureStatus.CONFIRMED
        materialized.invalidated_at_bar = None
        materialized.replaced_by = None
        expected_hash = candidate.content_hash()

    if (
        materialized.logical_id != expected_logical_id
        or materialized.object_id != expected_object_id
        or materialized.revision != expected_revision
        or materialized.status is not StructureStatus.CONFIRMED
        or materialized.invalidated_at_bar is not None
        or materialized.replaced_by is not None
        or materialized.content_hash() != expected_hash
    ):
        raise SegmentIncrementalMaterializationError(
            "SEGMENT_MATERIALIZATION_OUTPUT_INVARIANT_INVALID"
        )

    return SegmentIncrementalMaterializationResult(
        action=decision.action,
        previous_logical_id=previous.logical_id,
        previous_object_id=previous.object_id,
        previous_revision=previous.revision,
        materialized_logical_id=materialized.logical_id,
        materialized_object_id=materialized.object_id,
        materialized_revision=materialized.revision,
        previous_content_hash=previous.content_hash(),
        materialized_content_hash=materialized.content_hash(),
        canonical_reconciliation=decision,
        materialized_segment=materialized,
    )
