"""Pure historical Stroke-source continuity contract for Incremental Segment.

This module authenticates one previously confirmed ``Segment``, the historical
``Stroke`` source that supported it, and the current bounded-tail-stabilized
``Stroke`` source.  It reports source continuity only.  It does not decide
retention, construct records, run engines, emit events, or integrate runtime.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from ..domain.lifecycle import StructureStatus, StrokeDirection
from ..domain.segment import Segment
from ..domain.stroke import Stroke


__all__ = (
    "SegmentIncrementalSourceContinuityAction",
    "SegmentIncrementalSourceContinuityDecision",
    "SegmentIncrementalSourceContinuityError",
    "evaluate_incremental_segment_source_continuity",
)


class SegmentIncrementalSourceContinuityError(ValueError):
    """Raised when source continuity inputs cannot be authenticated safely."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


class SegmentIncrementalSourceContinuityAction(str, Enum):
    """Closed continuity result; neither action is a runtime policy."""

    PRESERVED = "PRESERVED"
    BROKEN = "BROKEN"


@dataclass(frozen=True)
class SegmentIncrementalSourceContinuityDecision:
    """Immutable continuity result for one authenticated historical prefix."""

    action: SegmentIncrementalSourceContinuityAction
    reason_code: str
    bound_prefix_length: int

    def __post_init__(self) -> None:
        if type(self.action) is not SegmentIncrementalSourceContinuityAction:
            raise SegmentIncrementalSourceContinuityError(
                "SEGMENT_SOURCE_CONTINUITY_DECISION_ACTION_INVALID"
            )
        if (
            type(self.bound_prefix_length) is not int
            or self.bound_prefix_length < 1
        ):
            raise SegmentIncrementalSourceContinuityError(
                "SEGMENT_SOURCE_CONTINUITY_DECISION_PREFIX_INVALID"
            )
        expected_reason = {
            SegmentIncrementalSourceContinuityAction.PRESERVED: (
                "SOURCE_CONTINUITY_PRESERVED"
            ),
            SegmentIncrementalSourceContinuityAction.BROKEN: (
                "SOURCE_CONTINUITY_BROKEN"
            ),
        }[self.action]
        if self.reason_code != expected_reason:
            raise SegmentIncrementalSourceContinuityError(
                "SEGMENT_SOURCE_CONTINUITY_DECISION_INVARIANT_INVALID"
            )


def evaluate_incremental_segment_source_continuity(
    *,
    previous: Segment,
    previous_source_strokes: Sequence[Stroke],
    current_source_strokes: Sequence[Stroke],
) -> SegmentIncrementalSourceContinuityDecision:
    """Authenticate and compare only the Stroke prefix bound by ``previous``."""

    _validate_previous(previous)
    historical, historical_hashes = _validate_source_strokes(
        previous_source_strokes,
        prefix="SEGMENT_SOURCE_CONTINUITY_PREVIOUS_SOURCE",
    )
    current, current_hashes = _validate_source_strokes(
        current_source_strokes,
        prefix="SEGMENT_SOURCE_CONTINUITY_CURRENT_SOURCE",
    )
    bound_length = _validate_previous_source_binding(previous, historical)

    if len(current) < bound_length:
        return _decision(
            SegmentIncrementalSourceContinuityAction.BROKEN,
            bound_length,
        )

    identity_fields = ("logical_id", "stroke_id", "object_id", "revision")
    for index, (historical_stroke, current_stroke) in enumerate(zip(
        historical[:bound_length], current[:bound_length]
    )):
        if any(
            getattr(historical_stroke, field) != getattr(current_stroke, field)
            for field in identity_fields
        ) or historical_hashes[index] != current_hashes[index]:
            return _decision(
                SegmentIncrementalSourceContinuityAction.BROKEN,
                bound_length,
            )

    return _decision(
        SegmentIncrementalSourceContinuityAction.PRESERVED,
        bound_length,
    )


def _validate_previous(previous: Segment) -> None:
    if type(previous) is not Segment:
        raise SegmentIncrementalSourceContinuityError(
            "SEGMENT_SOURCE_CONTINUITY_PREVIOUS_TYPE_INVALID"
        )
    if previous.status is not StructureStatus.CONFIRMED:
        raise SegmentIncrementalSourceContinuityError(
            "SEGMENT_SOURCE_CONTINUITY_PREVIOUS_STATUS_INVALID"
        )
    if previous.invalidated_at_bar is not None or previous.replaced_by is not None:
        raise SegmentIncrementalSourceContinuityError(
            "SEGMENT_SOURCE_CONTINUITY_PREVIOUS_LIFECYCLE_INVALID"
        )
    for field in ("logical_id", "object_id", "segment_id"):
        value = getattr(previous, field)
        if type(value) is not str or not value:
            raise SegmentIncrementalSourceContinuityError(
                f"SEGMENT_SOURCE_CONTINUITY_PREVIOUS_{field.upper()}_INVALID"
            )
    if type(previous.revision) is not int or previous.revision < 1:
        raise SegmentIncrementalSourceContinuityError(
            "SEGMENT_SOURCE_CONTINUITY_PREVIOUS_REVISION_INVALID"
        )
    if type(previous.direction) is not StrokeDirection:
        raise SegmentIncrementalSourceContinuityError(
            "SEGMENT_SOURCE_CONTINUITY_PREVIOUS_DIRECTION_INVALID"
        )
    _stable_content_hash(
        previous,
        "SEGMENT_SOURCE_CONTINUITY_PREVIOUS_CONTENT_HASH_INVALID",
    )


def _validate_source_strokes(
    source_strokes: Sequence[Stroke],
    *,
    prefix: str,
) -> tuple[tuple[Stroke, ...], tuple[str, ...]]:
    if isinstance(source_strokes, (str, bytes, bytearray)) or not isinstance(
        source_strokes, Sequence
    ):
        raise SegmentIncrementalSourceContinuityError(f"{prefix}_REQUIRED")
    source = tuple(source_strokes)
    if not source or any(type(stroke) is not Stroke for stroke in source):
        raise SegmentIncrementalSourceContinuityError(f"{prefix}_REQUIRED")

    logical_ids: list[str] = []
    object_ids: list[str] = []
    stroke_ids: list[str] = []
    content_hashes: list[str] = []
    for stroke in source:
        if stroke.status is not StructureStatus.CONFIRMED:
            raise SegmentIncrementalSourceContinuityError(f"{prefix}_STATUS_INVALID")
        for field in ("logical_id", "object_id", "stroke_id"):
            value = getattr(stroke, field)
            if type(value) is not str or not value:
                raise SegmentIncrementalSourceContinuityError(
                    f"{prefix}_{field.upper()}_INVALID"
                )
        if type(stroke.revision) is not int or stroke.revision < 1:
            raise SegmentIncrementalSourceContinuityError(
                f"{prefix}_REVISION_INVALID"
            )
        content_hashes.append(
            _stable_content_hash(stroke, f"{prefix}_CONTENT_HASH_INVALID")
        )
        logical_ids.append(stroke.logical_id)
        object_ids.append(stroke.object_id)
        stroke_ids.append(stroke.stroke_id)

    for values, label in (
        (logical_ids, "LOGICAL_ID"),
        (object_ids, "OBJECT_ID"),
        (stroke_ids, "STROKE_ID"),
    ):
        if len(values) != len(set(values)):
            raise SegmentIncrementalSourceContinuityError(
                f"{prefix}_{label}_DUPLICATE"
            )
    return source, tuple(content_hashes)


def _validate_previous_source_binding(
    previous: Segment,
    historical: tuple[Stroke, ...],
) -> int:
    stroke_ids = previous.stroke_ids
    if (
        type(stroke_ids) is not list
        or not stroke_ids
        or any(type(stroke_id) is not str or not stroke_id for stroke_id in stroke_ids)
        or len(stroke_ids) > len(historical)
        or tuple(stroke_ids)
        != tuple(stroke.stroke_id for stroke in historical[: len(stroke_ids)])
        or previous.start_stroke_id != stroke_ids[0]
        or previous.end_stroke_id != stroke_ids[-1]
    ):
        raise SegmentIncrementalSourceContinuityError(
            "SEGMENT_SOURCE_CONTINUITY_PREVIOUS_BINDING_INVALID"
        )

    start = historical[0]
    end = historical[len(stroke_ids) - 1]
    if (
        previous.start_bar_index != start.start_bar_index
        or previous.start_price != start.start_price
        or previous.end_bar_index != end.end_bar_index
        or previous.end_price != end.end_price
        or previous.direction is not start.direction
        or previous.direction is not end.direction
    ):
        raise SegmentIncrementalSourceContinuityError(
            "SEGMENT_SOURCE_CONTINUITY_PREVIOUS_BOUNDARY_INVALID"
        )
    return len(stroke_ids)


def _stable_content_hash(record: Segment | Stroke, reason_code: str) -> str:
    try:
        first = record.content_hash()
        second = record.content_hash()
    except Exception as error:
        raise SegmentIncrementalSourceContinuityError(reason_code) from error
    if type(first) is not str or not first or first != second:
        raise SegmentIncrementalSourceContinuityError(reason_code)
    return first


def _decision(
    action: SegmentIncrementalSourceContinuityAction,
    bound_prefix_length: int,
) -> SegmentIncrementalSourceContinuityDecision:
    return SegmentIncrementalSourceContinuityDecision(
        action=action,
        reason_code=(
            "SOURCE_CONTINUITY_PRESERVED"
            if action is SegmentIncrementalSourceContinuityAction.PRESERVED
            else "SOURCE_CONTINUITY_BROKEN"
        ),
        bound_prefix_length=bound_prefix_length,
    )
