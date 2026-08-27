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

from ..domain.lifecycle import StructureStatus, StrokeDirection
from ..domain.segment import Segment
from ..domain.stroke import Stroke
from .segment_incremental_source_continuity import (
    SegmentIncrementalSourceContinuityAction,
    SegmentIncrementalSourceContinuityDecision,
    SegmentIncrementalSourceContinuityError,
    evaluate_incremental_segment_source_continuity,
)
from ..engine.segment import SegmentEngineResult


__all__ = (
    "SegmentIncrementalReconciliationAction",
    "SegmentIncrementalReconciliationDecision",
    "SegmentIncrementalReconciliationError",
    "SegmentIncrementalTransientPolicyAction",
    "SegmentIncrementalTransientPolicyDecision",
    "SegmentIncrementalTransientPolicyError",
    "evaluate_incremental_segment_transient_policy",
    "reconcile_incremental_segment",
)


class SegmentIncrementalReconciliationError(ValueError):
    """Raised when a reconciliation decision cannot be made safely."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


class SegmentIncrementalTransientPolicyError(ValueError):
    """Raised when a transient retention decision cannot be authenticated."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


class SegmentIncrementalTransientPolicyAction(str, Enum):
    """Closed transient policy actions; neither mutates lifecycle state."""

    RETAIN_PREVIOUS = "RETAIN_PREVIOUS"
    FAIL_CLOSED = "FAIL_CLOSED"


@dataclass(frozen=True)
class SegmentIncrementalTransientPolicyDecision:
    """Immutable policy result with the complete authenticated decision evidence."""

    action: SegmentIncrementalTransientPolicyAction
    reason_code: str
    current_outcome_code: str
    previous_logical_id: str
    previous_object_id: str
    previous_revision: int
    previous_content_hash: str
    source_continuity_action: SegmentIncrementalSourceContinuityAction
    bound_prefix_length: int
    source_continuity_decision: SegmentIncrementalSourceContinuityDecision

    def __post_init__(self) -> None:
        if type(self.action) is not SegmentIncrementalTransientPolicyAction:
            raise SegmentIncrementalTransientPolicyError(
                "SEGMENT_TRANSIENT_POLICY_DECISION_ACTION_INVALID"
            )
        if (
            type(self.current_outcome_code) is not str
            or self.current_outcome_code not in _NONMATERIALIZED_OUTCOMES
        ):
            raise SegmentIncrementalTransientPolicyError(
                "SEGMENT_TRANSIENT_POLICY_OUTCOME_INVALID"
            )
        if (
            type(self.source_continuity_action)
            is not SegmentIncrementalSourceContinuityAction
        ):
            raise SegmentIncrementalTransientPolicyError(
                "SEGMENT_TRANSIENT_POLICY_CONTINUITY_ACTION_INVALID"
            )
        if (
            type(self.source_continuity_decision)
            is not SegmentIncrementalSourceContinuityDecision
            or self.source_continuity_decision.action
            is not self.source_continuity_action
            or self.source_continuity_decision.bound_prefix_length
            != self.bound_prefix_length
        ):
            raise SegmentIncrementalTransientPolicyError(
                "SEGMENT_TRANSIENT_POLICY_CONTINUITY_EVIDENCE_INVALID"
            )
        if (
            type(self.previous_logical_id) is not str
            or not self.previous_logical_id
            or type(self.previous_object_id) is not str
            or not self.previous_object_id
            or type(self.previous_revision) is not int
            or self.previous_revision < 1
            or type(self.previous_content_hash) is not str
            or not self.previous_content_hash
            or type(self.bound_prefix_length) is not int
            or self.bound_prefix_length < 1
        ):
            raise SegmentIncrementalTransientPolicyError(
                "SEGMENT_TRANSIENT_POLICY_PREVIOUS_EVIDENCE_INVALID"
            )
        previous = self.source_continuity_decision.previous_binding
        if (
            self.previous_logical_id != previous.logical_id
            or self.previous_object_id != previous.object_id
            or self.previous_revision != previous.revision
            or self.previous_content_hash != previous.content_hash
        ):
            raise SegmentIncrementalTransientPolicyError(
                "SEGMENT_TRANSIENT_POLICY_PREVIOUS_EVIDENCE_MISMATCH"
            )
        expected_action = (
            SegmentIncrementalTransientPolicyAction.RETAIN_PREVIOUS
            if self.current_outcome_code
            in {
                "SEGMENT_FEATURE_WINDOW_INCOMPLETE",
                "SEGMENT_PRIMARY_FRACTAL_NOT_FOUND",
            }
            and self.source_continuity_action
            is SegmentIncrementalSourceContinuityAction.PRESERVED
            else SegmentIncrementalTransientPolicyAction.FAIL_CLOSED
        )
        if self.action is not expected_action:
            raise SegmentIncrementalTransientPolicyError(
                "SEGMENT_TRANSIENT_POLICY_DECISION_INVARIANT_INVALID"
            )
        expected_reason = (
            "SEGMENT_TRANSIENT_PREVIOUS_RETAINED"
            if self.action is SegmentIncrementalTransientPolicyAction.RETAIN_PREVIOUS
            else (
                "SEGMENT_TRANSIENT_SECOND_CASE_PENDING"
                if self.current_outcome_code == "SEGMENT_SECOND_CASE_PENDING"
                else "SEGMENT_TRANSIENT_SOURCE_CONTINUITY_BROKEN"
            )
        )
        if self.reason_code != expected_reason:
            raise SegmentIncrementalTransientPolicyError(
                "SEGMENT_TRANSIENT_POLICY_REASON_INVALID"
            )


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

    def __post_init__(self) -> None:
        if type(self.action) is not SegmentIncrementalReconciliationAction:
            raise SegmentIncrementalReconciliationError(
                "SEGMENT_RECONCILIATION_DECISION_ACTION_INVALID"
            )
        previous = (
            self.previous_logical_id,
            self.previous_object_id,
            self.previous_revision,
            self.previous_content_hash,
        )
        candidate = (self.candidate_logical_id, self.candidate_content_hash)
        if self.action is SegmentIncrementalReconciliationAction.NO_PREVIOUS:
            if previous != (None, None, None, None) or self.next_revision is not None:
                raise SegmentIncrementalReconciliationError(
                    "SEGMENT_RECONCILIATION_NO_PREVIOUS_DECISION_INVALID"
                )
            expected_candidate = {
                "SEGMENT_RECONCILIATION_NO_PREVIOUS_NONMATERIALIZED": (
                    None,
                    None,
                ),
                "SEGMENT_RECONCILIATION_NO_PREVIOUS_FIRST_CASE": candidate,
            }
            expected = expected_candidate.get(self.reason_code)
            if (
                expected is None
                or candidate != expected
                or (
                    self.reason_code == "SEGMENT_RECONCILIATION_NO_PREVIOUS_FIRST_CASE"
                    and not _valid_text_pair(candidate)
                )
            ):
                raise SegmentIncrementalReconciliationError(
                    "SEGMENT_RECONCILIATION_NO_PREVIOUS_DECISION_INVALID"
                )
            return

        if not _valid_previous_decision_fields(previous) or not _valid_text_pair(
            candidate
        ):
            raise SegmentIncrementalReconciliationError(
                "SEGMENT_RECONCILIATION_DECISION_IDENTITY_INVALID"
            )
        if self.action in (
            SegmentIncrementalReconciliationAction.REUSE,
            SegmentIncrementalReconciliationAction.REVISE,
        ) and type(self.next_revision) is not int:
            raise SegmentIncrementalReconciliationError(
                "SEGMENT_RECONCILIATION_NEXT_REVISION_TYPE_INVALID"
            )
        if self.action is SegmentIncrementalReconciliationAction.REUSE:
            valid = (
                self.reason_code == "SEGMENT_RECONCILIATION_IDENTITY_REUSED"
                and self.previous_logical_id == self.candidate_logical_id
                and self.previous_content_hash == self.candidate_content_hash
                and self.next_revision == self.previous_revision
            )
        elif self.action is SegmentIncrementalReconciliationAction.REVISE:
            valid = (
                self.reason_code == "SEGMENT_RECONCILIATION_CONTENT_CHANGED"
                and self.previous_logical_id == self.candidate_logical_id
                and self.previous_content_hash != self.candidate_content_hash
                and self.next_revision == self.previous_revision + 1
            )
        else:
            valid = (
                self.reason_code == "SEGMENT_RECONCILIATION_LOGICAL_ID_CHANGED"
                and self.previous_logical_id != self.candidate_logical_id
                and self.next_revision is None
            )
        if not valid:
            raise SegmentIncrementalReconciliationError(
                "SEGMENT_RECONCILIATION_DECISION_INVARIANT_INVALID"
            )


_FIRST_CASE = "SEGMENT_FIRST_CASE_CONFIRMED"
_NONMATERIALIZED_OUTCOMES = frozenset(
    {
        "SEGMENT_FEATURE_WINDOW_INCOMPLETE",
        "SEGMENT_PRIMARY_FRACTAL_NOT_FOUND",
        "SEGMENT_SECOND_CASE_PENDING",
    }
)


def evaluate_incremental_segment_transient_policy(
    *,
    previous: Segment,
    current: SegmentEngineResult,
    previous_source_strokes: Sequence[Stroke],
    current_source_strokes: Sequence[Stroke],
) -> SegmentIncrementalTransientPolicyDecision:
    """Decide transient retention from authenticated previous/source evidence."""

    continuity = _evaluate_transient_continuity(
        previous=previous,
        previous_source_strokes=previous_source_strokes,
        current_source_strokes=current_source_strokes,
    )
    outcome = _validate_transient_current(current)
    retained = (
        outcome
        in {
            "SEGMENT_FEATURE_WINDOW_INCOMPLETE",
            "SEGMENT_PRIMARY_FRACTAL_NOT_FOUND",
        }
        and continuity.action is SegmentIncrementalSourceContinuityAction.PRESERVED
    )
    action = (
        SegmentIncrementalTransientPolicyAction.RETAIN_PREVIOUS
        if retained
        else SegmentIncrementalTransientPolicyAction.FAIL_CLOSED
    )
    reason_code = (
        "SEGMENT_TRANSIENT_PREVIOUS_RETAINED"
        if retained
        else (
            "SEGMENT_TRANSIENT_SECOND_CASE_PENDING"
            if outcome == "SEGMENT_SECOND_CASE_PENDING"
            else "SEGMENT_TRANSIENT_SOURCE_CONTINUITY_BROKEN"
        )
    )
    previous_binding = continuity.previous_binding
    return SegmentIncrementalTransientPolicyDecision(
        action=action,
        reason_code=reason_code,
        current_outcome_code=outcome,
        previous_logical_id=previous_binding.logical_id,
        previous_object_id=previous_binding.object_id,
        previous_revision=previous_binding.revision,
        previous_content_hash=previous_binding.content_hash,
        source_continuity_action=continuity.action,
        bound_prefix_length=continuity.bound_prefix_length,
        source_continuity_decision=continuity,
    )


def _evaluate_transient_continuity(
    *,
    previous: Segment,
    previous_source_strokes: Sequence[Stroke],
    current_source_strokes: Sequence[Stroke],
) -> SegmentIncrementalSourceContinuityDecision:
    try:
        return evaluate_incremental_segment_source_continuity(
            previous=previous,
            previous_source_strokes=previous_source_strokes,
            current_source_strokes=current_source_strokes,
        )
    except SegmentIncrementalSourceContinuityError as error:
        raise SegmentIncrementalTransientPolicyError(error.reason_code) from error


def _validate_transient_current(current: SegmentEngineResult) -> str:
    if type(current) is not SegmentEngineResult:
        raise SegmentIncrementalTransientPolicyError(
            "SEGMENT_TRANSIENT_POLICY_CURRENT_TYPE_INVALID"
        )
    if (
        type(current.reason_code) is not str
        or current.reason_code not in _NONMATERIALIZED_OUTCOMES
    ):
        raise SegmentIncrementalTransientPolicyError(
            "SEGMENT_TRANSIENT_POLICY_OUTCOME_UNSUPPORTED"
        )
    if type(current.candidate_direction) is not StrokeDirection:
        raise SegmentIncrementalTransientPolicyError(
            "SEGMENT_TRANSIENT_POLICY_CANDIDATE_DIRECTION_INVALID"
        )
    if current.segment is not None or current.completed is not False:
        raise SegmentIncrementalTransientPolicyError(
            "SEGMENT_TRANSIENT_POLICY_NONMATERIALIZED_SHAPE_INVALID"
        )
    if current.reason_code == "SEGMENT_SECOND_CASE_PENDING":
        if current.pending_second_case is None:
            raise SegmentIncrementalTransientPolicyError(
                "SEGMENT_TRANSIENT_POLICY_PENDING_SECOND_CASE_SHAPE_INVALID"
            )
    elif current.pending_second_case is not None:
        raise SegmentIncrementalTransientPolicyError(
            "SEGMENT_TRANSIENT_POLICY_NONMATERIALIZED_SHAPE_INVALID"
        )
    return current.reason_code


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
    if previous.invalidated_at_bar is not None or previous.replaced_by is not None:
        raise SegmentIncrementalReconciliationError(
            "SEGMENT_RECONCILIATION_PREVIOUS_LIFECYCLE_STATE_INVALID"
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
    if type(current.candidate_direction) is not StrokeDirection:
        raise SegmentIncrementalReconciliationError(
            "SEGMENT_RECONCILIATION_CANDIDATE_DIRECTION_INVALID"
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
        if candidate.invalidated_at_bar is not None or candidate.replaced_by is not None:
            raise SegmentIncrementalReconciliationError(
                "SEGMENT_RECONCILIATION_CANDIDATE_LIFECYCLE_STATE_INVALID"
            )
        if candidate.direction is not current.candidate_direction:
            raise SegmentIncrementalReconciliationError(
                "SEGMENT_RECONCILIATION_CANDIDATE_DIRECTION_MISMATCH"
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
        or candidate.start_stroke_id != stroke_ids[0]
        or candidate.end_stroke_id != stroke_ids[-1]
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
        or type(segment.segment_id) is not str
        or not segment.segment_id
        or type(segment.revision) is not int
        or segment.revision < 1
    ):
        raise SegmentIncrementalReconciliationError(f"{prefix}_IDENTITY_INVALID")


def _valid_text_pair(values: tuple[str | None, str | None]) -> bool:
    return all(type(value) is str and bool(value) for value in values)


def _valid_previous_decision_fields(
    values: tuple[str | None, str | None, int | None, str | None],
) -> bool:
    logical_id, object_id, revision, content_hash = values
    return (
        type(logical_id) is str
        and bool(logical_id)
        and type(object_id) is str
        and bool(object_id)
        and type(revision) is int
        and revision >= 1
        and type(content_hash) is str
        and bool(content_hash)
    )


def _stable_content_hash(record: Segment | Stroke, reason_code: str) -> str:
    try:
        first = record.content_hash()
        second = record.content_hash()
    except Exception as error:
        raise SegmentIncrementalReconciliationError(reason_code) from error
    if type(first) is not str or not first or first != second:
        raise SegmentIncrementalReconciliationError(reason_code)
    return first
