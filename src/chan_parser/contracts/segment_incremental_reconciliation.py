"""Pure Incremental Segment identity reconciliation decision contract.

This module authenticates an already-produced ``SegmentEngineResult`` against
authoritative source ``Stroke`` records and, when present, one previously
confirmed ``Segment``.  It does not construct or mutate domain records, run
Segment rules, emit lifecycle events, or integrate with runtime/checkpoints.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from ..domain.lifecycle import StructureStatus
from ..domain.segment import Segment
from ..domain.stroke import Stroke
from ..engine.segment import SegmentEngineResult


__all__ = (
    "SegmentIncrementalReconciliationAction",
    "SegmentIncrementalReconciliationDecision",
    "SegmentIncrementalReconciliationError",
    "reconcile_incremental_segment",
)


class SegmentIncrementalReconciliationError(ValueError):
    """Raised when a reconciliation decision cannot be made safely."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


class SegmentIncrementalReconciliationAction(str, Enum):
    """Closed set of identity relationships; none performs a runtime action."""

    NO_PREVIOUS = "NO_PREVIOUS"
    REUSE = "REUSE"
    REVISE = "REVISE"
    REPLACE_REQUIRED = "REPLACE_REQUIRED"


@dataclass(frozen=True)
class SegmentIncrementalReconciliationDecision:
    """Immutable identity decision envelope, never a replacement Segment."""

    action: SegmentIncrementalReconciliationAction
    reason_code: str
    previous_logical_id: str | None
    previous_object_id: str | None
    previous_revision: int | None
    previous_content_hash: str | None
    candidate_logical_id: str | None
    candidate_content_hash: str | None
    next_revision: int | None


_FIRST_CASE = "SEGMENT_FIRST_CASE_CONFIRMED"
_NONMATERIALIZED_OUTCOMES = frozenset(
    {
        "SEGMENT_FEATURE_WINDOW_INCOMPLETE",
        "SEGMENT_PRIMARY_FRACTAL_NOT_FOUND",
        "SEGMENT_SECOND_CASE_PENDING",
    }
)


def reconcile_incremental_segment(
    *,
    previous: Segment | None,
    current: SegmentEngineResult,
    source_strokes: Sequence[Stroke],
) -> SegmentIncrementalReconciliationDecision:
    """Classify previous/current identity without publishing either record."""

    previous_hash = _validate_previous(previous)
    source = _validate_source_strokes(source_strokes)
    candidate = _validate_current(current, source)

    if candidate is None:
        if previous is not None:
            raise SegmentIncrementalReconciliationError(
                "SEGMENT_RECONCILIATION_PREVIOUS_WITH_"
                "NONMATERIALIZED_CURRENT_UNSUPPORTED"
            )
        return SegmentIncrementalReconciliationDecision(
            action=SegmentIncrementalReconciliationAction.NO_PREVIOUS,
            reason_code="SEGMENT_RECONCILIATION_NO_PREVIOUS_NONMATERIALIZED",
            previous_logical_id=None,
            previous_object_id=None,
            previous_revision=None,
            previous_content_hash=None,
            candidate_logical_id=None,
            candidate_content_hash=None,
            next_revision=None,
        )

    candidate_hash = _stable_content_hash(
        candidate, "SEGMENT_RECONCILIATION_CANDIDATE_CONTENT_HASH_INVALID"
    )
    if previous is None:
        return SegmentIncrementalReconciliationDecision(
            action=SegmentIncrementalReconciliationAction.NO_PREVIOUS,
            reason_code="SEGMENT_RECONCILIATION_NO_PREVIOUS_FIRST_CASE",
            previous_logical_id=None,
            previous_object_id=None,
            previous_revision=None,
            previous_content_hash=None,
            candidate_logical_id=candidate.logical_id,
            candidate_content_hash=candidate_hash,
            next_revision=None,
        )

    previous_fields = {
        "previous_logical_id": previous.logical_id,
        "previous_object_id": previous.object_id,
        "previous_revision": previous.revision,
        "previous_content_hash": previous_hash,
    }
    candidate_fields = {
        "candidate_logical_id": candidate.logical_id,
        "candidate_content_hash": candidate_hash,
    }
    if previous.logical_id != candidate.logical_id:
        return SegmentIncrementalReconciliationDecision(
            action=SegmentIncrementalReconciliationAction.REPLACE_REQUIRED,
            reason_code="SEGMENT_RECONCILIATION_LOGICAL_ID_CHANGED",
            **previous_fields,
            **candidate_fields,
            next_revision=None,
        )
    if previous_hash == candidate_hash:
        return SegmentIncrementalReconciliationDecision(
            action=SegmentIncrementalReconciliationAction.REUSE,
            reason_code="SEGMENT_RECONCILIATION_IDENTITY_REUSED",
            **previous_fields,
            **candidate_fields,
            next_revision=previous.revision,
        )
    return SegmentIncrementalReconciliationDecision(
        action=SegmentIncrementalReconciliationAction.REVISE,
        reason_code="SEGMENT_RECONCILIATION_CONTENT_CHANGED",
        **previous_fields,
        **candidate_fields,
        next_revision=previous.revision + 1,
    )


def _validate_previous(previous: Segment | None) -> str | None:
    if previous is None:
        return None
    if type(previous) is not Segment:
        raise SegmentIncrementalReconciliationError(
            "SEGMENT_RECONCILIATION_PREVIOUS_TYPE_INVALID"
        )
    if previous.status is not StructureStatus.CONFIRMED:
        raise SegmentIncrementalReconciliationError(
            "SEGMENT_RECONCILIATION_PREVIOUS_STATUS_INVALID"
        )
    _validate_identity(
        previous,
        prefix="SEGMENT_RECONCILIATION_PREVIOUS",
    )
    return _stable_content_hash(
        previous, "SEGMENT_RECONCILIATION_PREVIOUS_CONTENT_HASH_INVALID"
    )


def _validate_current(
    current: SegmentEngineResult,
    source: tuple[Stroke, ...],
) -> Segment | None:
    if type(current) is not SegmentEngineResult:
        raise SegmentIncrementalReconciliationError(
            "SEGMENT_RECONCILIATION_CURRENT_TYPE_INVALID"
        )
    if current.reason_code == _FIRST_CASE:
        if (
            type(current.segment) is not Segment
            or current.completed is not True
            or current.pending_second_case is not None
        ):
            raise SegmentIncrementalReconciliationError(
                "SEGMENT_RECONCILIATION_FIRST_CASE_SHAPE_INVALID"
            )
        candidate = current.segment
        if candidate.status is not StructureStatus.CONFIRMED:
            raise SegmentIncrementalReconciliationError(
                "SEGMENT_RECONCILIATION_CANDIDATE_STATUS_INVALID"
            )
        _validate_identity(
            candidate,
            prefix="SEGMENT_RECONCILIATION_CANDIDATE",
        )
        _validate_candidate_source_binding(candidate, source)
        return candidate

    if current.reason_code not in _NONMATERIALIZED_OUTCOMES:
        raise SegmentIncrementalReconciliationError(
            "SEGMENT_RECONCILIATION_OUTCOME_UNSUPPORTED"
        )
    if current.segment is not None or current.completed is not False:
        raise SegmentIncrementalReconciliationError(
            "SEGMENT_RECONCILIATION_NONMATERIALIZED_SHAPE_INVALID"
        )
    if current.reason_code == "SEGMENT_SECOND_CASE_PENDING":
        if current.pending_second_case is None:
            raise SegmentIncrementalReconciliationError(
                "SEGMENT_RECONCILIATION_SECOND_CASE_SHAPE_INVALID"
            )
    elif current.pending_second_case is not None:
        raise SegmentIncrementalReconciliationError(
            "SEGMENT_RECONCILIATION_NONMATERIALIZED_SHAPE_INVALID"
        )
    return None


def _validate_source_strokes(source_strokes: Sequence[Stroke]) -> tuple[Stroke, ...]:
    if isinstance(source_strokes, (str, bytes)) or not isinstance(
        source_strokes, Sequence
    ):
        raise SegmentIncrementalReconciliationError(
            "SEGMENT_RECONCILIATION_SOURCE_STROKES_REQUIRED"
        )
    source = tuple(source_strokes)
    if not source or any(type(stroke) is not Stroke for stroke in source):
        raise SegmentIncrementalReconciliationError(
            "SEGMENT_RECONCILIATION_SOURCE_STROKES_REQUIRED"
        )

    logical_ids: list[str] = []
    object_ids: list[str] = []
    stroke_ids: list[str] = []
    for stroke in source:
        if stroke.status is not StructureStatus.CONFIRMED:
            raise SegmentIncrementalReconciliationError(
                "SEGMENT_RECONCILIATION_SOURCE_STROKE_STATUS_INVALID"
            )
        if (
            type(stroke.logical_id) is not str
            or not stroke.logical_id
            or type(stroke.object_id) is not str
            or not stroke.object_id
            or type(stroke.stroke_id) is not str
            or not stroke.stroke_id
            or type(stroke.revision) is not int
            or stroke.revision < 1
        ):
            raise SegmentIncrementalReconciliationError(
                "SEGMENT_RECONCILIATION_SOURCE_STROKE_IDENTITY_INVALID"
            )
        _stable_content_hash(
            stroke, "SEGMENT_RECONCILIATION_SOURCE_STROKE_CONTENT_HASH_INVALID"
        )
        logical_ids.append(stroke.logical_id)
        object_ids.append(stroke.object_id)
        stroke_ids.append(stroke.stroke_id)
    if (
        len(logical_ids) != len(set(logical_ids))
        or len(object_ids) != len(set(object_ids))
        or len(stroke_ids) != len(set(stroke_ids))
    ):
        raise SegmentIncrementalReconciliationError(
            "SEGMENT_RECONCILIATION_SOURCE_STROKE_IDENTITY_DUPLICATE"
        )
    return source


def _validate_candidate_source_binding(
    candidate: Segment,
    source: tuple[Stroke, ...],
) -> None:
    stroke_ids = candidate.stroke_ids
    if (
        type(stroke_ids) is not list
        or not stroke_ids
        or any(type(stroke_id) is not str or not stroke_id for stroke_id in stroke_ids)
        or len(stroke_ids) > len(source)
        or tuple(stroke_ids) != tuple(stroke.stroke_id for stroke in source[: len(stroke_ids)])
    ):
        raise SegmentIncrementalReconciliationError(
            "SEGMENT_RECONCILIATION_CANDIDATE_SOURCE_BINDING_INVALID"
        )


def _validate_identity(segment: Segment, *, prefix: str) -> None:
    if (
        type(segment.logical_id) is not str
        or not segment.logical_id
        or type(segment.object_id) is not str
        or not segment.object_id
        or type(segment.revision) is not int
        or segment.revision < 1
    ):
        raise SegmentIncrementalReconciliationError(f"{prefix}_IDENTITY_INVALID")


def _stable_content_hash(record: Segment | Stroke, reason_code: str) -> str:
    try:
        first = record.content_hash()
        second = record.content_hash()
    except Exception as error:
        raise SegmentIncrementalReconciliationError(reason_code) from error
    if type(first) is not str or not first or first != second:
        raise SegmentIncrementalReconciliationError(reason_code)
    return first
