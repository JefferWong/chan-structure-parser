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
    "SegmentIncrementalSourcePreviousBinding",
    "SegmentIncrementalSourceStrokeBinding",
    "bind_incremental_segment_source_strokes",
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


_PUBLIC_SOURCE_BINDING_PREFIX = "SEGMENT_SOURCE_BINDING"


@dataclass(frozen=True)
class SegmentIncrementalSourcePreviousBinding:
    """Explicit evidence binding for one previously confirmed Segment."""

    logical_id: str
    object_id: str
    segment_id: str
    revision: int
    content_hash: str
    stroke_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_text(self.logical_id, "SEGMENT_SOURCE_CONTINUITY_BINDING_LOGICAL_ID_INVALID")
        _validate_text(self.object_id, "SEGMENT_SOURCE_CONTINUITY_BINDING_OBJECT_ID_INVALID")
        _validate_text(self.segment_id, "SEGMENT_SOURCE_CONTINUITY_BINDING_SEGMENT_ID_INVALID")
        _validate_revision(self.revision, "SEGMENT_SOURCE_CONTINUITY_BINDING_REVISION_INVALID")
        _validate_text(self.content_hash, "SEGMENT_SOURCE_CONTINUITY_BINDING_CONTENT_HASH_INVALID")
        _validate_stroke_ids(self.stroke_ids, "SEGMENT_SOURCE_CONTINUITY_BINDING_STROKE_IDS_INVALID")


@dataclass(frozen=True)
class SegmentIncrementalSourceStrokeBinding:
    """Explicit evidence binding for one authenticated Stroke record."""

    logical_id: str
    object_id: str
    stroke_id: str
    revision: int
    content_hash: str

    def __post_init__(self) -> None:
        _validate_text(self.logical_id, "SEGMENT_SOURCE_CONTINUITY_STROKE_BINDING_LOGICAL_ID_INVALID")
        _validate_text(self.object_id, "SEGMENT_SOURCE_CONTINUITY_STROKE_BINDING_OBJECT_ID_INVALID")
        _validate_text(self.stroke_id, "SEGMENT_SOURCE_CONTINUITY_STROKE_BINDING_STROKE_ID_INVALID")
        _validate_revision(self.revision, "SEGMENT_SOURCE_CONTINUITY_STROKE_BINDING_REVISION_INVALID")
        _validate_text(self.content_hash, "SEGMENT_SOURCE_CONTINUITY_STROKE_BINDING_CONTENT_HASH_INVALID")


@dataclass(frozen=True)
class SegmentIncrementalSourceContinuityDecision:
    """Immutable continuity result for one authenticated historical prefix."""

    action: SegmentIncrementalSourceContinuityAction
    reason_code: str
    bound_prefix_length: int
    previous_binding: SegmentIncrementalSourcePreviousBinding
    historical_bound_prefix_binding: tuple[SegmentIncrementalSourceStrokeBinding, ...]
    current_source_binding: tuple[SegmentIncrementalSourceStrokeBinding, ...]

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
        if type(self.reason_code) is not str or self.reason_code != expected_reason:
            raise SegmentIncrementalSourceContinuityError(
                "SEGMENT_SOURCE_CONTINUITY_DECISION_INVARIANT_INVALID"
            )
        if type(self.previous_binding) is not SegmentIncrementalSourcePreviousBinding:
            raise SegmentIncrementalSourceContinuityError(
                "SEGMENT_SOURCE_CONTINUITY_DECISION_PREVIOUS_BINDING_INVALID"
            )
        if type(self.historical_bound_prefix_binding) is not tuple:
            raise SegmentIncrementalSourceContinuityError(
                "SEGMENT_SOURCE_CONTINUITY_DECISION_HISTORICAL_BINDING_INVALID"
            )
        if type(self.current_source_binding) is not tuple:
            raise SegmentIncrementalSourceContinuityError(
                "SEGMENT_SOURCE_CONTINUITY_DECISION_CURRENT_BINDING_INVALID"
            )
        if not self.historical_bound_prefix_binding:
            raise SegmentIncrementalSourceContinuityError(
                "SEGMENT_SOURCE_CONTINUITY_DECISION_HISTORICAL_BINDING_EMPTY"
            )
        if not self.current_source_binding:
            raise SegmentIncrementalSourceContinuityError(
                "SEGMENT_SOURCE_CONTINUITY_DECISION_CURRENT_BINDING_EMPTY"
            )
        if any(
            type(binding) is not SegmentIncrementalSourceStrokeBinding
            for binding in (
                *self.historical_bound_prefix_binding,
                *self.current_source_binding,
            )
        ):
            raise SegmentIncrementalSourceContinuityError(
                "SEGMENT_SOURCE_CONTINUITY_DECISION_STROKE_BINDING_INVALID"
            )
        _validate_binding_uniqueness(
            self.historical_bound_prefix_binding,
            "SEGMENT_SOURCE_CONTINUITY_DECISION_HISTORICAL_BINDING",
        )
        _validate_binding_uniqueness(
            self.current_source_binding,
            "SEGMENT_SOURCE_CONTINUITY_DECISION_CURRENT_BINDING",
        )
        if len(self.previous_binding.stroke_ids) != self.bound_prefix_length:
            raise SegmentIncrementalSourceContinuityError(
                "SEGMENT_SOURCE_CONTINUITY_DECISION_PREVIOUS_PREFIX_LENGTH_INVALID"
            )
        if len(self.historical_bound_prefix_binding) != self.bound_prefix_length:
            raise SegmentIncrementalSourceContinuityError(
                "SEGMENT_SOURCE_CONTINUITY_DECISION_HISTORICAL_PREFIX_LENGTH_INVALID"
            )
        if tuple(
            binding.stroke_id for binding in self.historical_bound_prefix_binding
        ) != self.previous_binding.stroke_ids:
            raise SegmentIncrementalSourceContinuityError(
                "SEGMENT_SOURCE_CONTINUITY_DECISION_HISTORICAL_PREFIX_MISMATCH"
            )
        current_prefix = self.current_source_binding[: self.bound_prefix_length]
        if self.action is SegmentIncrementalSourceContinuityAction.PRESERVED:
            if len(self.current_source_binding) < self.bound_prefix_length:
                raise SegmentIncrementalSourceContinuityError(
                    "SEGMENT_SOURCE_CONTINUITY_DECISION_PRESERVED_SOURCE_TOO_SHORT"
                )
            if self.historical_bound_prefix_binding != current_prefix:
                raise SegmentIncrementalSourceContinuityError(
                    "SEGMENT_SOURCE_CONTINUITY_DECISION_PRESERVED_PREFIX_MISMATCH"
                )
        elif (
            len(self.current_source_binding) >= self.bound_prefix_length
            and self.historical_bound_prefix_binding == current_prefix
        ):
            raise SegmentIncrementalSourceContinuityError(
                "SEGMENT_SOURCE_CONTINUITY_DECISION_BROKEN_EVIDENCE_IDENTICAL"
            )


def bind_incremental_segment_source_strokes(
    source_strokes: Sequence[Stroke],
) -> tuple[SegmentIncrementalSourceStrokeBinding, ...]:
    """Return ordered, self-describing evidence for one complete Stroke source."""

    source, content_hashes = _validate_source_strokes(
        source_strokes,
        prefix=_PUBLIC_SOURCE_BINDING_PREFIX,
    )
    return _build_stroke_bindings(source, content_hashes)


def evaluate_incremental_segment_source_continuity(
    *,
    previous: Segment,
    previous_source_strokes: Sequence[Stroke],
    current_source_strokes: Sequence[Stroke],
) -> SegmentIncrementalSourceContinuityDecision:
    """Authenticate and compare only the Stroke prefix bound by ``previous``."""

    previous_binding = _validate_previous(previous)
    historical, historical_hashes = _validate_source_strokes(
        previous_source_strokes,
        prefix="SEGMENT_SOURCE_CONTINUITY_PREVIOUS_SOURCE",
    )
    current, current_hashes = _validate_source_strokes(
        current_source_strokes,
        prefix="SEGMENT_SOURCE_CONTINUITY_CURRENT_SOURCE",
    )
    bound_length = _validate_previous_source_binding(previous, historical)
    historical_binding = _build_stroke_bindings(historical, historical_hashes)
    current_binding = _build_stroke_bindings(current, current_hashes)
    historical_prefix_binding = historical_binding[:bound_length]

    if len(current) < bound_length:
        return _decision(
            SegmentIncrementalSourceContinuityAction.BROKEN,
            bound_length,
            previous_binding=previous_binding,
            historical_bound_prefix_binding=historical_prefix_binding,
            current_source_binding=current_binding,
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
                previous_binding=previous_binding,
                historical_bound_prefix_binding=historical_prefix_binding,
                current_source_binding=current_binding,
            )

    return _decision(
        SegmentIncrementalSourceContinuityAction.PRESERVED,
        bound_length,
        previous_binding=previous_binding,
        historical_bound_prefix_binding=historical_prefix_binding,
        current_source_binding=current_binding,
    )


def _validate_previous(previous: Segment) -> SegmentIncrementalSourcePreviousBinding:
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
    previous_hash = _stable_content_hash(
        previous,
        "SEGMENT_SOURCE_CONTINUITY_PREVIOUS_CONTENT_HASH_INVALID",
    )
    return SegmentIncrementalSourcePreviousBinding(
        logical_id=previous.logical_id,
        object_id=previous.object_id,
        segment_id=previous.segment_id,
        revision=previous.revision,
        content_hash=previous_hash,
        stroke_ids=tuple(previous.stroke_ids),
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
        if stroke.invalidated_at_bar is not None or stroke.replaced_by is not None:
            raise SegmentIncrementalSourceContinuityError(
                f"{prefix}_LIFECYCLE_INVALID"
            )
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


def _build_stroke_bindings(
    source: tuple[Stroke, ...],
    content_hashes: tuple[str, ...],
) -> tuple[SegmentIncrementalSourceStrokeBinding, ...]:
    return tuple(
        SegmentIncrementalSourceStrokeBinding(
            logical_id=stroke.logical_id,
            object_id=stroke.object_id,
            stroke_id=stroke.stroke_id,
            revision=stroke.revision,
            content_hash=content_hash,
        )
        for stroke, content_hash in zip(source, content_hashes)
    )


def _validate_text(value: object, reason_code: str) -> None:
    if type(value) is not str or not value:
        raise SegmentIncrementalSourceContinuityError(reason_code)


def _validate_revision(value: object, reason_code: str) -> None:
    if type(value) is not int or value < 1:
        raise SegmentIncrementalSourceContinuityError(reason_code)


def _validate_stroke_ids(value: object, reason_code: str) -> None:
    if type(value) is not tuple or not value:
        raise SegmentIncrementalSourceContinuityError(reason_code)
    if any(type(stroke_id) is not str or not stroke_id for stroke_id in value):
        raise SegmentIncrementalSourceContinuityError(reason_code)
    if len(value) != len(set(value)):
        raise SegmentIncrementalSourceContinuityError(reason_code)


def _validate_binding_uniqueness(
    bindings: tuple[SegmentIncrementalSourceStrokeBinding, ...],
    prefix: str,
) -> None:
    for field in ("logical_id", "object_id", "stroke_id"):
        values = tuple(getattr(binding, field) for binding in bindings)
        if len(values) != len(set(values)):
            raise SegmentIncrementalSourceContinuityError(
                f"{prefix}_{field.upper()}_DUPLICATE"
            )


def _decision(
    action: SegmentIncrementalSourceContinuityAction,
    bound_prefix_length: int,
    *,
    previous_binding: SegmentIncrementalSourcePreviousBinding,
    historical_bound_prefix_binding: tuple[SegmentIncrementalSourceStrokeBinding, ...],
    current_source_binding: tuple[SegmentIncrementalSourceStrokeBinding, ...],
) -> SegmentIncrementalSourceContinuityDecision:
    return SegmentIncrementalSourceContinuityDecision(
        action=action,
        reason_code=(
            "SOURCE_CONTINUITY_PRESERVED"
            if action is SegmentIncrementalSourceContinuityAction.PRESERVED
            else "SOURCE_CONTINUITY_BROKEN"
        ),
        bound_prefix_length=bound_prefix_length,
        previous_binding=previous_binding,
        historical_bound_prefix_binding=historical_bound_prefix_binding,
        current_source_binding=current_source_binding,
    )
