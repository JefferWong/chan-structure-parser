"""Pure Phase 2 Segment checkpoint semantic-state integrity contract.

This module authenticates already-produced source, Segment, and lifecycle
records. It does not run Segment rules, emit events, own a log, or restore an
engine.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
from itertools import pairwise
import json
import math
import re
from typing import Any

from ..domain.lifecycle import EventType, StructureStatus, StrokeDirection
from ..domain.segment import Segment
from ..domain.stroke import Stroke


__all__ = (
    "SegmentCheckpointContractError",
    "SegmentCheckpointState",
    "validate_segment_checkpoint_profile",
    "derive_segment_checkpoint_state",
    "validate_segment_checkpoint_state",
)


class SegmentCheckpointContractError(ValueError):
    """Raised when checkpoint semantic state cannot be authenticated."""


@dataclass(frozen=True)
class SegmentCheckpointState:
    """Content-addressed semantic envelope for one Segment R1 outcome."""

    outcome_code: str
    candidate_direction: StrokeDirection
    source_stroke_logical_ids: tuple[str, ...]
    source_stroke_object_ids: tuple[str, ...]
    source_stroke_content_hashes: tuple[str, ...]
    source_stroke_semantic_hashes: tuple[str, ...]
    segment_id: str | None
    segment_object_id: str | None
    segment_logical_id: str | None
    segment_revision: int | None
    segment_content_hash: str | None
    segment_semantic_hash: str | None
    segment_created_at_bar: int | None
    segment_confirmed_at_bar: int | None
    lifecycle_intent_keys: tuple[str, ...]
    lifecycle_binding_key: str | None
    lifecycle_event_semantic_hashes: tuple[str, ...]
    state_key: str


_SUPPORTED_OUTCOMES = frozenset(
    {
        "SEGMENT_FEATURE_WINDOW_INCOMPLETE",
        "SEGMENT_PRIMARY_FRACTAL_NOT_FOUND",
        "SEGMENT_SECOND_CASE_PENDING",
        "SEGMENT_FIRST_CASE_CONFIRMED",
    }
)
_FIRST_CASE = "SEGMENT_FIRST_CASE_CONFIRMED"
_ZERO_EVENT_OUTCOMES = _SUPPORTED_OUTCOMES - {_FIRST_CASE}
_EXPECTED_EVENT_SHAPE = (
    (EventType.CREATED, "SEGMENT_FIRST_CASE_CREATED"),
    (EventType.CONFIRMED, "SEGMENT_FIRST_CASE_CONFIRMED"),
)
_EVENT_KEYS = frozenset(
    {
        "event_id",
        "event_type",
        "object_type",
        "object_id",
        "logical_id",
        "occurred_at_bar_id",
        "reason_code",
        "replaced_by",
        "rule_profile",
        "rule_version",
        "detail",
    }
)
_PRODUCER_DETAIL_KEYS = frozenset(
    {
        "segment_id",
        "direction",
        "start_stroke_id",
        "end_stroke_id",
        "stroke_ids",
        "feature_sequence_stroke_ids",
        "destruction_evidence_stroke_ids",
        "primary_evidence_key",
        "primary_sequence_id",
        "primary_element_logical_ids",
        "feature_fractal_type",
        "endpoint",
    }
)
_INTENT_KEY_FIELD = "segment_" + "lifecycle_intent_key"
_BINDING_KEY_FIELD = "segment_" + "lifecycle_binding_key"
_EMITTER_DETAIL_KEYS = frozenset(
    {_INTENT_KEY_FIELD, _BINDING_KEY_FIELD, "emission_binding"}
)
_DETAIL_KEYS = _PRODUCER_DETAIL_KEYS | _EMITTER_DETAIL_KEYS
_EMISSION_BINDING_KEYS = frozenset(
    {
        "source_stroke_logical_ids",
        "source_stroke_ids",
        "primary_element_logical_ids",
        "primary_feature_visibility",
        "endpoint_bar_index",
        "confirmation_bar",
    }
)
_FEATURE_VISIBILITY_KEYS = frozenset(
    {"logical_id", "visible_at_bar_index", "source_stroke_logical_ids"}
)
_ENDPOINT_KEYS = frozenset(
    {"endpoint_id", "bar_index", "price", "defining_stroke_logical_ids"}
)
_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_LIFECYCLE_PROFILE_ID = "minimal_segment_" + "lifecycle_emission_v1"
_INTENT_KEY_PREFIX = "segment_" + "lifecycle:"

_EXPECTED_PROFILE: dict[str, Any] = {
    "profile_id": "minimal_segment_checkpoint_contract_v1",
    "profile_version": "0.1.0",
    "status": "CONTRACT_ONLY",
    "source_segment_profile_id": "minimal_segment_engine_core_v1",
    "source_segment_profile_version": "0.1.0",
    "source_lifecycle_emission_profile_id": _LIFECYCLE_PROFILE_ID,
    "source_lifecycle_emission_profile_version": "0.1.0",
    "source_lifecycle_emission_baseline_commit": (
        "937ad3a3d805fd36527a8c295e04141232e53a1e"
    ),
    "checkpoint": {
        "semantic_state_only": True,
        "event_log_snapshot_owned_elsewhere": True,
        "partial_lifecycle_prefix_allowed": False,
        "second_case_state_capture_allowed": True,
        "second_case_orchestration_enabled": False,
    },
    "binding": {
        "source_strokes_required": True,
        "source_stroke_content_hashes_required": True,
        "source_stroke_semantic_hashes_required": True,
        "first_case_segment_binding_required": True,
        "segment_semantic_hash_required": True,
        "first_case_complete_lifecycle_pair_required": True,
        "canonical_intent_key_validation_required": True,
        "lifecycle_event_semantic_hashes_required": True,
        "zero_event_outcomes_require_empty_lifecycle_slice": True,
    },
    "integration": {
        "full_rebuild_integration_enabled": False,
        "incremental_integration_enabled": False,
        "checkpoint_runtime_integration_enabled": False,
        "bounded_tail_segment_recompute_enabled": False,
        "parser_integration_enabled": False,
        "center_or_zhongshu_enabled": False,
    },
}


def validate_segment_checkpoint_profile(profile: Mapping[str, Any]) -> None:
    """Accept only the frozen Stage C-A profile."""

    _validate_exact_mapping(profile, _EXPECTED_PROFILE, "profile")


def _production_segment_checkpoint_profile() -> dict[str, Any]:
    """Return the narrow PR25 revision-aware integration profile."""
    profile = dict(_EXPECTED_PROFILE)
    profile["profile_id"] = "minimal_segment_checkpoint_production_v2"
    profile["profile_version"] = "0.2.0"
    profile["status"] = "INCREMENTAL_PRODUCTION"
    profile["integration"] = {
        **profile["integration"],
        "incremental_integration_enabled": True,
        "checkpoint_runtime_integration_enabled": True,
    }
    profile["checkpoint"] = {
        **profile["checkpoint"],
        "revision_aware_formal_segment_enabled": True,
    }
    return profile


def derive_segment_checkpoint_state(
    *,
    outcome_code: str,
    candidate_direction: StrokeDirection,
    source_strokes: Sequence[Stroke],
    segment: Segment | None,
    lifecycle_events: Sequence[Mapping[str, Any]],
    allow_revisions: bool = False,
) -> SegmentCheckpointState:
    """Derive an immutable envelope without mutating or re-running producers."""

    if type(allow_revisions) is not bool:
        raise TypeError("allow_revisions must be a bool")
    if type(outcome_code) is not str or outcome_code not in _SUPPORTED_OUTCOMES:
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_OUTCOME_UNSUPPORTED"
        )
    if type(candidate_direction) is not StrokeDirection:
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_CANDIDATE_DIRECTION_INVALID"
        )
    source = _validate_source_strokes(source_strokes)
    if candidate_direction != source[0].direction:
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_CANDIDATE_SOURCE_DIRECTION_MISMATCH"
        )
    events = _validate_event_sequence(lifecycle_events)

    if outcome_code in _ZERO_EVENT_OUTCOMES:
        if segment is not None:
            raise SegmentCheckpointContractError(
                "SEGMENT_CHECKPOINT_ZERO_EVENT_OUTCOME_HAS_SEGMENT"
            )
        if events:
            raise SegmentCheckpointContractError(
                "SEGMENT_CHECKPOINT_ZERO_EVENT_OUTCOME_HAS_EVENTS"
            )
        semantic = _semantic_fields(
            outcome_code=outcome_code,
            candidate_direction=candidate_direction,
            source=source,
            segment=None,
            lifecycle_intent_keys=(),
            lifecycle_binding_key=None,
            lifecycle_event_semantic_hashes=(),
        )
    else:
        bound_segment = _validate_first_case_segment(
            segment,
            candidate_direction=candidate_direction,
            source=source,
            allow_revisions=allow_revisions,
        )
        intent_keys, binding_key, event_hashes = _validate_complete_lifecycle(
            events,
            segment=bound_segment,
            source=source,
            candidate_direction=candidate_direction,
        )
        semantic = _semantic_fields(
            outcome_code=outcome_code,
            candidate_direction=candidate_direction,
            source=source,
            segment=bound_segment,
            lifecycle_intent_keys=intent_keys,
            lifecycle_binding_key=binding_key,
            lifecycle_event_semantic_hashes=event_hashes,
        )

    return SegmentCheckpointState(
        **semantic,
        state_key=_state_key_for_fields(semantic),
    )


def validate_segment_checkpoint_state(
    state: SegmentCheckpointState,
    *,
    outcome_code: str,
    candidate_direction: StrokeDirection,
    source_strokes: Sequence[Stroke],
    segment: Segment | None,
    lifecycle_events: Sequence[Mapping[str, Any]],
    allow_revisions: bool = False,
) -> None:
    """Require exact equality with a freshly derived canonical envelope."""

    if type(allow_revisions) is not bool:
        raise TypeError("allow_revisions must be a bool")
    if type(state) is not SegmentCheckpointState:
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_STATE_REQUIRED"
        )
    canonical = derive_segment_checkpoint_state(
        outcome_code=outcome_code,
        candidate_direction=candidate_direction,
        source_strokes=source_strokes,
        segment=segment,
        lifecycle_events=lifecycle_events,
        allow_revisions=allow_revisions,
    )
    if state != canonical:
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_STATE_MISMATCH"
        )


def _validate_source_strokes(source_strokes: Sequence[Stroke]) -> tuple[Stroke, ...]:
    if not _is_ordered_sequence(source_strokes):
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_SOURCE_STROKES_REQUIRED"
        )
    source = tuple(source_strokes)
    if not source or any(type(stroke) is not Stroke for stroke in source):
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_SOURCE_STROKES_REQUIRED"
        )

    logical_ids: list[str] = []
    object_ids: list[str] = []
    stroke_ids: list[str] = []
    for stroke in source:
        for name in (
            "logical_id",
            "object_id",
            "stroke_id",
            "start_fractal_id",
            "end_fractal_id",
            "repaint_risk",
            "rule_profile",
            "rule_version",
        ):
            value = getattr(stroke, name)
            if type(value) is not str or not value:
                raise SegmentCheckpointContractError(
                    f"SEGMENT_CHECKPOINT_SOURCE_{name.upper()}_INVALID"
                )
        if type(stroke.status) is not StructureStatus or (
            stroke.status != StructureStatus.CONFIRMED
        ):
            raise SegmentCheckpointContractError(
                "SEGMENT_CHECKPOINT_SOURCE_STATUS_INVALID"
            )
        if type(stroke.direction) is not StrokeDirection:
            raise SegmentCheckpointContractError(
                "SEGMENT_CHECKPOINT_SOURCE_DIRECTION_INVALID"
            )
        if type(stroke.revision) is not int or stroke.revision < 1:
            raise SegmentCheckpointContractError(
                "SEGMENT_CHECKPOINT_SOURCE_REVISION_INVALID"
            )
        for name in ("start_bar_index", "end_bar_index", "created_at_bar"):
            value = getattr(stroke, name)
            if type(value) is not int or value < 0:
                raise SegmentCheckpointContractError(
                    f"SEGMENT_CHECKPOINT_SOURCE_{name.upper()}_INVALID"
                )
        if (
            stroke.end_bar_index <= stroke.start_bar_index
            or type(stroke.confirmed_at_bar) is not int
            or stroke.confirmed_at_bar < stroke.end_bar_index
            or stroke.created_at_bar > stroke.confirmed_at_bar
        ):
            raise SegmentCheckpointContractError(
                "SEGMENT_CHECKPOINT_SOURCE_BAR_INVALID"
            )
        if stroke.invalidated_at_bar is not None or stroke.replaced_by is not None:
            raise SegmentCheckpointContractError(
                "SEGMENT_CHECKPOINT_SOURCE_CONFIRMED_LIFECYCLE_INVALID"
            )
        for name in (
            "start_price",
            "end_price",
            "max_price",
            "min_price",
            "price_range",
        ):
            if not _is_finite_number(getattr(stroke, name)):
                raise SegmentCheckpointContractError(
                    f"SEGMENT_CHECKPOINT_SOURCE_{name.upper()}_INVALID"
                )
        if stroke.min_price > min(stroke.start_price, stroke.end_price) or (
            stroke.max_price < max(stroke.start_price, stroke.end_price)
        ) or stroke.price_range < 0:
            raise SegmentCheckpointContractError(
                "SEGMENT_CHECKPOINT_SOURCE_PRICE_RANGE_INVALID"
            )
        if (
            stroke.direction == StrokeDirection.UP
            and stroke.start_price >= stroke.end_price
        ) or (
            stroke.direction == StrokeDirection.DOWN
            and stroke.start_price <= stroke.end_price
        ):
            raise SegmentCheckpointContractError(
                "SEGMENT_CHECKPOINT_SOURCE_DIRECTION_PRICE_MISMATCH"
            )
        if type(stroke.merged_bar_count) is not int or stroke.merged_bar_count < 0:
            raise SegmentCheckpointContractError(
                "SEGMENT_CHECKPOINT_SOURCE_MERGED_BAR_COUNT_INVALID"
            )
        _string_sequence(
            stroke.confirmation_requirements,
            "SEGMENT_CHECKPOINT_SOURCE_CONFIRMATION_REQUIREMENTS",
            allow_empty=True,
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
            raise SegmentCheckpointContractError(
                f"SEGMENT_CHECKPOINT_SOURCE_{label}_DUPLICATE"
            )
    for previous, current in pairwise(source):
        if previous.direction == current.direction:
            raise SegmentCheckpointContractError(
                "SEGMENT_CHECKPOINT_SOURCE_NOT_ALTERNATING"
            )
        if (
            previous.end_fractal_id != current.start_fractal_id
            or previous.end_bar_index != current.start_bar_index
        ):
            raise SegmentCheckpointContractError(
                "SEGMENT_CHECKPOINT_SOURCE_ENDPOINT_NOT_CONTIGUOUS"
            )
        if previous.end_price != current.start_price:
            raise SegmentCheckpointContractError(
                "SEGMENT_CHECKPOINT_SOURCE_PRICE_NOT_CONTIGUOUS"
            )
    return source


def _validate_event_sequence(
    lifecycle_events: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    if not _is_ordered_sequence(lifecycle_events):
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_LIFECYCLE_SEQUENCE_REQUIRED"
        )
    events = tuple(lifecycle_events)
    if any(not isinstance(event, Mapping) for event in events):
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_LIFECYCLE_EVENT_INVALID"
        )
    return events


def _validate_first_case_segment(
    segment: Segment | None,
    *,
    candidate_direction: StrokeDirection,
    source: tuple[Stroke, ...],
    allow_revisions: bool = False,
) -> Segment:
    if type(segment) is not Segment:
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_FIRST_CASE_SEGMENT_REQUIRED"
        )
    if type(segment.status) is not StructureStatus or (
        segment.status != StructureStatus.CONFIRMED
    ):
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_SEGMENT_STATUS_INVALID"
        )
    if type(segment.direction) is not StrokeDirection or (
        segment.direction != candidate_direction
    ):
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_SEGMENT_DIRECTION_MISMATCH"
        )
    if type(segment.end_bar_index) is not int or not _is_finite_number(
        segment.end_price
    ):
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_SEGMENT_BOUNDARY_INVALID"
        )
    if type(segment.start_bar_index) is not int or not _is_finite_number(
        segment.start_price
    ):
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_SEGMENT_BOUNDARY_INVALID"
        )
    boundary_matches = tuple(
        (index, stroke)
        for index, stroke in enumerate(source)
        if stroke.end_bar_index == segment.end_bar_index
        and stroke.end_price == segment.end_price
    )
    if len(boundary_matches) != 1:
        raise SegmentCheckpointContractError(
            f"SEGMENT_CHECKPOINT_BOUNDARY_MATCH_COUNT={len(boundary_matches)}"
        )
    boundary_index, boundary = boundary_matches[0]
    candidate_prefix = source[: boundary_index + 1]
    if (
        len(candidate_prefix) < 3
        or len(candidate_prefix) % 2 == 0
        or candidate_prefix[-1].direction != candidate_direction
    ):
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_CANDIDATE_PREFIX_INVALID"
        )
    first = candidate_prefix[0]
    direction_code = "U" if candidate_direction == StrokeDirection.UP else "D"
    expected_segment_id = (
        f"segment_{first.start_bar_index + 1:06d}_"
        f"{boundary.end_bar_index + 1:06d}_{direction_code}"
    )
    expected_values = {
        "segment_id": expected_segment_id,
        "object_id": (
            f"{expected_segment_id}_r{segment.revision}"
            if allow_revisions else f"{expected_segment_id}_r1"
        ),
        "logical_id": f"segment:{first.logical_id}->{boundary.logical_id}",
        "revision": segment.revision if allow_revisions else 1,
        "rule_profile": "minimal_segment_engine_core_v1",
        "rule_version": "0.1.0",
        "start_stroke_id": first.stroke_id,
        "end_stroke_id": boundary.stroke_id,
        "start_bar_index": first.start_bar_index,
        "end_bar_index": boundary.end_bar_index,
        "start_price": first.start_price,
        "end_price": boundary.end_price,
        "confirmation_requirements": [],
        "repaint_risk": "NONE",
        "invalidated_at_bar": None,
        "replaced_by": None,
    }
    for name, expected in expected_values.items():
        if getattr(segment, name) != expected:
            raise SegmentCheckpointContractError(
                f"SEGMENT_CHECKPOINT_SEGMENT_REBINDING_MISMATCH:{name}"
            )
    if type(segment.revision) is not int or segment.revision < 1:
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_SEGMENT_REVISION_INVALID"
        )
    if (
        type(segment.created_at_bar) is not int
        or type(segment.confirmed_at_bar) is not int
        or segment.created_at_bar != segment.confirmed_at_bar
        or segment.confirmed_at_bar < segment.end_bar_index
    ):
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_SEGMENT_CONFIRMATION_INVALID"
        )
    stroke_ids = _string_sequence(
        segment.stroke_ids,
        "SEGMENT_CHECKPOINT_SEGMENT_STROKE_IDS",
    )
    if stroke_ids != tuple(stroke.stroke_id for stroke in candidate_prefix):
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_SEGMENT_SOURCE_PREFIX_MISMATCH"
        )
    _string_sequence(
        segment.feature_sequence_stroke_ids,
        "SEGMENT_CHECKPOINT_SEGMENT_FEATURE_STROKE_IDS",
    )
    _string_sequence(
        segment.destruction_evidence_stroke_ids,
        "SEGMENT_CHECKPOINT_SEGMENT_DESTRUCTION_STROKE_IDS",
    )
    return segment


def _validate_complete_lifecycle(
    events: tuple[Mapping[str, Any], ...],
    *,
    segment: Segment,
    source: tuple[Stroke, ...],
    candidate_direction: StrokeDirection,
) -> tuple[tuple[str, str], str, tuple[str, str]]:
    if len(events) != 2:
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_FIRST_CASE_LIFECYCLE_INCOMPLETE"
        )
    expected_occurred = f"bar_{segment.confirmed_at_bar + 1:06d}"
    event_ids: list[str] = []
    intent_keys: list[str] = []
    binding_keys: list[str] = []
    canonical_details: list[dict[str, Any]] = []
    producer_details: list[dict[str, Any]] = []
    event_hashes: list[str] = []

    for event, (event_type, reason_code) in zip(events, _EXPECTED_EVENT_SHAPE):
        _require_exact_keys(event, _EVENT_KEYS, "LIFECYCLE_EVENT")
        expected_fields = {
            "event_type": event_type,
            "object_type": "segment",
            "object_id": segment.object_id,
            "logical_id": segment.logical_id,
            "occurred_at_bar_id": expected_occurred,
            "reason_code": reason_code,
            "replaced_by": None,
            "rule_profile": segment.rule_profile,
            "rule_version": segment.rule_version,
        }
        for name, expected in expected_fields.items():
            if event[name] != expected:
                raise SegmentCheckpointContractError(
                    f"SEGMENT_CHECKPOINT_LIFECYCLE_FIELD_MISMATCH:{name}"
                )
        event_id = event["event_id"]
        if type(event_id) is not str or not event_id:
            raise SegmentCheckpointContractError(
                "SEGMENT_CHECKPOINT_LIFECYCLE_EVENT_ID_INVALID"
            )
        detail = event["detail"]
        if not isinstance(detail, Mapping):
            raise SegmentCheckpointContractError(
                "SEGMENT_CHECKPOINT_LIFECYCLE_DETAIL_INVALID"
            )
        _require_exact_keys(detail, _DETAIL_KEYS, "LIFECYCLE_DETAIL")
        canonical_detail = _plain_value(detail)
        producer_detail = {
            key: canonical_detail[key] for key in sorted(_PRODUCER_DETAIL_KEYS)
        }
        _validate_producer_detail(
            producer_detail,
            segment=segment,
            source=source,
        )
        intent_key = detail[_INTENT_KEY_FIELD]
        binding_key = detail[_BINDING_KEY_FIELD]
        if type(intent_key) is not str or (
            intent_key != _intent_key_for_event(event, producer_detail)
        ):
            raise SegmentCheckpointContractError(
                "SEGMENT_CHECKPOINT_LIFECYCLE_INTENT_KEY_MISMATCH"
            )
        if type(binding_key) is not str or _HEX_64.fullmatch(binding_key) is None:
            raise SegmentCheckpointContractError(
                "SEGMENT_CHECKPOINT_LIFECYCLE_BINDING_KEY_INVALID"
            )
        emission_binding = detail["emission_binding"]
        _validate_emission_binding(
            emission_binding,
            producer_detail=producer_detail,
            segment=segment,
            source=source,
            candidate_direction=candidate_direction,
        )
        expected_binding_key = _semantic_digest(emission_binding)
        if binding_key != expected_binding_key:
            raise SegmentCheckpointContractError(
                "SEGMENT_CHECKPOINT_LIFECYCLE_BINDING_KEY_CONTENT_MISMATCH"
            )
        event_ids.append(event_id)
        intent_keys.append(intent_key)
        binding_keys.append(binding_key)
        canonical_details.append(canonical_detail)
        producer_details.append(producer_detail)
        event_hashes.append(_semantic_digest(event))

    if len(set(event_ids)) != 2:
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_LIFECYCLE_EVENT_ID_DUPLICATE"
        )
    if len(set(intent_keys)) != 2:
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_LIFECYCLE_INTENT_KEY_DUPLICATE"
        )
    if binding_keys[0] != binding_keys[1]:
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_LIFECYCLE_BINDING_KEY_MISMATCH"
        )
    if producer_details[0] != producer_details[1]:
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_LIFECYCLE_PRODUCER_DETAIL_MISMATCH"
        )
    without_intent = tuple(
        {key: detail[key] for key in detail if key != _INTENT_KEY_FIELD}
        for detail in canonical_details
    )
    if without_intent[0] != without_intent[1]:
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_LIFECYCLE_DETAIL_PAIR_MISMATCH"
        )
    return (
        (intent_keys[0], intent_keys[1]),
        binding_keys[0],
        (event_hashes[0], event_hashes[1]),
    )


def _validate_producer_detail(
    detail: Mapping[str, Any],
    *,
    segment: Segment,
    source: tuple[Stroke, ...],
) -> None:
    exact_segment_values = {
        "segment_id": segment.segment_id,
        "direction": segment.direction.value,
        "start_stroke_id": segment.start_stroke_id,
        "end_stroke_id": segment.end_stroke_id,
        "stroke_ids": list(segment.stroke_ids),
        "feature_sequence_stroke_ids": list(segment.feature_sequence_stroke_ids),
        "destruction_evidence_stroke_ids": list(
            segment.destruction_evidence_stroke_ids
        ),
    }
    for name, expected in exact_segment_values.items():
        if detail[name] != expected:
            raise SegmentCheckpointContractError(
                f"SEGMENT_CHECKPOINT_LIFECYCLE_DETAIL_MISMATCH:{name}"
            )
    for name in (
        "primary_evidence_key",
        "primary_sequence_id",
        "feature_fractal_type",
    ):
        if type(detail[name]) is not str or not detail[name]:
            raise SegmentCheckpointContractError(
                f"SEGMENT_CHECKPOINT_LIFECYCLE_DETAIL_INVALID:{name}"
            )
    primary_ids = _string_sequence(
        detail["primary_element_logical_ids"],
        "SEGMENT_CHECKPOINT_PRIMARY_ELEMENT_IDS",
        exact_length=3,
    )
    if len(set(primary_ids)) != 3:
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_PRIMARY_ELEMENT_IDS_DUPLICATE"
        )
    endpoint = detail["endpoint"]
    if not isinstance(endpoint, Mapping):
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_ENDPOINT_INVALID"
        )
    _require_exact_keys(endpoint, _ENDPOINT_KEYS, "ENDPOINT")
    if (
        type(endpoint["endpoint_id"]) is not str
        or type(endpoint["bar_index"]) is not int
        or not _is_finite_number(endpoint["price"])
        or endpoint["endpoint_id"]
        != source[len(segment.stroke_ids) - 1].end_fractal_id
        or endpoint["bar_index"] != segment.end_bar_index
        or endpoint["price"] != segment.end_price
    ):
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_ENDPOINT_REBINDING_MISMATCH"
        )
    defining_ids = _string_sequence(
        endpoint["defining_stroke_logical_ids"],
        "SEGMENT_CHECKPOINT_ENDPOINT_DEFINING_IDS",
    )
    source_ids = {stroke.logical_id for stroke in source}
    if len(defining_ids) != len(set(defining_ids)) or not set(defining_ids) <= source_ids:
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_ENDPOINT_DEFINING_IDS_INVALID"
        )


def _validate_emission_binding(
    emission_binding: Any,
    *,
    producer_detail: Mapping[str, Any],
    segment: Segment,
    source: tuple[Stroke, ...],
    candidate_direction: StrokeDirection,
) -> None:
    if not isinstance(emission_binding, Mapping):
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_EMISSION_BINDING_INVALID"
        )
    _require_exact_keys(
        emission_binding,
        _EMISSION_BINDING_KEYS,
        "EMISSION_BINDING",
    )
    source_logical_ids = _string_sequence(
        emission_binding["source_stroke_logical_ids"],
        "SEGMENT_CHECKPOINT_EMISSION_SOURCE_LOGICAL_IDS",
    )
    source_stroke_ids = _string_sequence(
        emission_binding["source_stroke_ids"],
        "SEGMENT_CHECKPOINT_EMISSION_SOURCE_STROKE_IDS",
    )
    if source_logical_ids != tuple(stroke.logical_id for stroke in source):
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_EMISSION_SOURCE_LOGICAL_IDS_MISMATCH"
        )
    if source_stroke_ids != tuple(stroke.stroke_id for stroke in source):
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_EMISSION_SOURCE_STROKE_IDS_MISMATCH"
        )
    if (
        type(emission_binding["endpoint_bar_index"]) is not int
        or emission_binding["endpoint_bar_index"] != segment.end_bar_index
    ):
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_EMISSION_ENDPOINT_BAR_MISMATCH"
        )
    if (
        type(emission_binding["confirmation_bar"]) is not int
        or emission_binding["confirmation_bar"] != segment.confirmed_at_bar
    ):
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_EMISSION_CONFIRMATION_BAR_MISMATCH"
        )
    primary_ids = _string_sequence(
        emission_binding["primary_element_logical_ids"],
        "SEGMENT_CHECKPOINT_EMISSION_PRIMARY_ELEMENT_IDS",
        exact_length=3,
    )
    detail_primary_ids = _string_sequence(
        producer_detail["primary_element_logical_ids"],
        "SEGMENT_CHECKPOINT_DETAIL_PRIMARY_ELEMENT_IDS",
        exact_length=3,
    )
    if len(set(primary_ids)) != 3 or primary_ids != detail_primary_ids:
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_EMISSION_PRIMARY_ELEMENT_IDS_MISMATCH"
        )
    visibility_items = _ordered_sequence(
        emission_binding["primary_feature_visibility"],
        "SEGMENT_CHECKPOINT_PRIMARY_FEATURE_VISIBILITY",
        exact_length=3,
    )
    source_by_logical_id = {stroke.logical_id: stroke for stroke in source}
    source_position = {
        stroke.logical_id: index for index, stroke in enumerate(source)
    }
    expected_provenance_direction = (
        StrokeDirection.DOWN
        if candidate_direction == StrokeDirection.UP
        else StrokeDirection.UP
    )
    flattened_strokes: list[Stroke] = []
    previous_position = -1
    visibility_values: list[int] = []
    for item, expected_logical_id in zip(visibility_items, primary_ids):
        if not isinstance(item, Mapping):
            raise SegmentCheckpointContractError(
                "SEGMENT_CHECKPOINT_PRIMARY_FEATURE_VISIBILITY_ITEM_INVALID"
            )
        _require_exact_keys(
            item,
            _FEATURE_VISIBILITY_KEYS,
            "PRIMARY_FEATURE_VISIBILITY_ITEM",
        )
        if item["logical_id"] != expected_logical_id:
            raise SegmentCheckpointContractError(
                "SEGMENT_CHECKPOINT_PRIMARY_FEATURE_LOGICAL_ID_MISMATCH"
            )
        if (
            type(item["visible_at_bar_index"]) is not int
            or item["visible_at_bar_index"] < 0
        ):
            raise SegmentCheckpointContractError(
                "SEGMENT_CHECKPOINT_PRIMARY_FEATURE_VISIBILITY_INVALID"
            )
        provenance = _string_sequence(
            item["source_stroke_logical_ids"],
            "SEGMENT_CHECKPOINT_PRIMARY_FEATURE_PROVENANCE",
        )
        provenance_strokes: list[Stroke] = []
        for logical_id in provenance:
            stroke = source_by_logical_id.get(logical_id)
            if stroke is None:
                raise SegmentCheckpointContractError(
                    "SEGMENT_CHECKPOINT_PRIMARY_FEATURE_SOURCE_MISSING"
                )
            if stroke.direction != expected_provenance_direction:
                raise SegmentCheckpointContractError(
                    "SEGMENT_CHECKPOINT_PRIMARY_FEATURE_DIRECTION_MISMATCH"
                )
            position = source_position[logical_id]
            if position <= previous_position:
                raise SegmentCheckpointContractError(
                    "SEGMENT_CHECKPOINT_PRIMARY_FEATURE_ORDER_MISMATCH"
                )
            previous_position = position
            provenance_strokes.append(stroke)
            flattened_strokes.append(stroke)
        expected_visibility = max(
            stroke.confirmed_at_bar for stroke in provenance_strokes
        )
        if item["visible_at_bar_index"] != expected_visibility:
            raise SegmentCheckpointContractError(
                "SEGMENT_CHECKPOINT_PRIMARY_FEATURE_VISIBILITY_MISMATCH"
            )
        visibility_values.append(expected_visibility)
    if tuple(stroke.stroke_id for stroke in flattened_strokes) != tuple(
        segment.feature_sequence_stroke_ids
    ):
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_FEATURE_PROVENANCE_MISMATCH"
        )
    expected_destruction = tuple(
        stroke.stroke_id
        for stroke in flattened_strokes
        if stroke.end_bar_index > segment.end_bar_index
    )
    if expected_destruction != tuple(segment.destruction_evidence_stroke_ids):
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_DESTRUCTION_PROVENANCE_MISMATCH"
        )
    expected_confirmation = max(segment.end_bar_index, *visibility_values)
    if (
        segment.created_at_bar != expected_confirmation
        or segment.confirmed_at_bar != expected_confirmation
    ):
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_CONFIRMATION_VISIBILITY_MISMATCH"
        )


def _intent_key_for_event(
    event: Mapping[str, Any],
    producer_detail: Mapping[str, Any],
) -> str:
    content = {
        "event_type": event["event_type"],
        "object_type": event["object_type"],
        "object_id": event["object_id"],
        "logical_id": event["logical_id"],
        "occurred_at_bar_id": event["occurred_at_bar_id"],
        "reason_code": event["reason_code"],
        "rule_profile": event["rule_profile"],
        "rule_version": event["rule_version"],
        "detail": producer_detail,
    }
    digest = hashlib.sha256(_canonical_json(content).encode("utf-8")).hexdigest()
    return f"{_INTENT_KEY_PREFIX}{digest[:24]}"


def _semantic_fields(
    *,
    outcome_code: str,
    candidate_direction: StrokeDirection,
    source: tuple[Stroke, ...],
    segment: Segment | None,
    lifecycle_intent_keys: tuple[str, ...],
    lifecycle_binding_key: str | None,
    lifecycle_event_semantic_hashes: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "outcome_code": outcome_code,
        "candidate_direction": candidate_direction,
        "source_stroke_logical_ids": tuple(stroke.logical_id for stroke in source),
        "source_stroke_object_ids": tuple(stroke.object_id for stroke in source),
        "source_stroke_content_hashes": tuple(
            stroke.content_hash() for stroke in source
        ),
        "source_stroke_semantic_hashes": tuple(
            _semantic_digest(_stroke_semantic_value(stroke)) for stroke in source
        ),
        "segment_id": segment.segment_id if segment is not None else None,
        "segment_object_id": segment.object_id if segment is not None else None,
        "segment_logical_id": segment.logical_id if segment is not None else None,
        "segment_revision": segment.revision if segment is not None else None,
        "segment_content_hash": segment.content_hash() if segment is not None else None,
        "segment_semantic_hash": (
            _semantic_digest(_segment_semantic_value(segment))
            if segment is not None
            else None
        ),
        "segment_created_at_bar": segment.created_at_bar if segment else None,
        "segment_confirmed_at_bar": segment.confirmed_at_bar if segment else None,
        "lifecycle_intent_keys": lifecycle_intent_keys,
        "lifecycle_binding_key": lifecycle_binding_key,
        "lifecycle_event_semantic_hashes": lifecycle_event_semantic_hashes,
    }


def _stroke_semantic_value(stroke: Stroke) -> dict[str, Any]:
    return {
        "object_id": stroke.object_id,
        "logical_id": stroke.logical_id,
        "revision": stroke.revision,
        "status": stroke.status.value,
        "stroke_id": stroke.stroke_id,
        "direction": stroke.direction.value,
        "start_fractal_id": stroke.start_fractal_id,
        "end_fractal_id": stroke.end_fractal_id,
        "start_price": stroke.start_price,
        "end_price": stroke.end_price,
        "start_bar_index": stroke.start_bar_index,
        "end_bar_index": stroke.end_bar_index,
        "merged_bar_count": stroke.merged_bar_count,
        "max_price": stroke.max_price,
        "min_price": stroke.min_price,
        "price_range": stroke.price_range,
        "confirmation_requirements": list(stroke.confirmation_requirements),
        "repaint_risk": stroke.repaint_risk,
        "created_at_bar": stroke.created_at_bar,
        "confirmed_at_bar": stroke.confirmed_at_bar,
        "invalidated_at_bar": stroke.invalidated_at_bar,
        "replaced_by": stroke.replaced_by,
        "rule_profile": stroke.rule_profile,
        "rule_version": stroke.rule_version,
    }


def _segment_semantic_value(segment: Segment) -> dict[str, Any]:
    return {
        "object_id": segment.object_id,
        "logical_id": segment.logical_id,
        "revision": segment.revision,
        "status": segment.status.value,
        "segment_id": segment.segment_id,
        "direction": segment.direction.value,
        "start_stroke_id": segment.start_stroke_id,
        "end_stroke_id": segment.end_stroke_id,
        "stroke_ids": list(segment.stroke_ids),
        "feature_sequence_stroke_ids": list(segment.feature_sequence_stroke_ids),
        "destruction_evidence_stroke_ids": list(
            segment.destruction_evidence_stroke_ids
        ),
        "start_price": segment.start_price,
        "end_price": segment.end_price,
        "start_bar_index": segment.start_bar_index,
        "end_bar_index": segment.end_bar_index,
        "confirmation_requirements": list(segment.confirmation_requirements),
        "repaint_risk": segment.repaint_risk,
        "created_at_bar": segment.created_at_bar,
        "confirmed_at_bar": segment.confirmed_at_bar,
        "invalidated_at_bar": segment.invalidated_at_bar,
        "replaced_by": segment.replaced_by,
        "rule_profile": segment.rule_profile,
        "rule_version": segment.rule_version,
    }


def _state_key_for_fields(fields: Mapping[str, Any]) -> str:
    expected_fields = {
        name for name in SegmentCheckpointState.__dataclass_fields__ if name != "state_key"
    }
    if set(fields) != expected_fields:
        raise SegmentCheckpointContractError(
            "SEGMENT_CHECKPOINT_STATE_FIELDS_INCOMPLETE"
        )
    return _semantic_digest(fields)


def _semantic_digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _plain_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise SegmentCheckpointContractError(
                "SEGMENT_CHECKPOINT_CANONICAL_KEY_INVALID"
            )
        return {key: _plain_value(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_plain_value(item) for item in value]
    if isinstance(value, (StrokeDirection, StructureStatus)):
        return value.value
    if value is None or type(value) in {str, int, float, bool}:
        return value
    raise SegmentCheckpointContractError(
        f"SEGMENT_CHECKPOINT_CANONICAL_VALUE_UNSUPPORTED:{type(value).__name__}"
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _plain_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _require_exact_keys(
    actual: Mapping[str, Any],
    expected: frozenset[str],
    path: str,
) -> None:
    if any(type(key) is not str for key in actual):
        raise SegmentCheckpointContractError(
            f"SEGMENT_CHECKPOINT_{path}_KEYS_INVALID:non_string_key"
        )
    if set(actual) != expected:
        raise SegmentCheckpointContractError(
            f"SEGMENT_CHECKPOINT_{path}_KEYS_INVALID:"
            f"missing={sorted(expected - set(actual))}:"
            f"unknown={sorted(set(actual) - expected)}"
        )


def _ordered_sequence(
    value: Any,
    path: str,
    *,
    exact_length: int | None = None,
) -> tuple[Any, ...]:
    if not _is_ordered_sequence(value):
        raise SegmentCheckpointContractError(f"{path}_INVALID")
    result = tuple(value)
    if exact_length is not None and len(result) != exact_length:
        raise SegmentCheckpointContractError(f"{path}_LENGTH_INVALID")
    return result


def _string_sequence(
    value: Any,
    path: str,
    *,
    allow_empty: bool = False,
    exact_length: int | None = None,
) -> tuple[str, ...]:
    result = _ordered_sequence(value, path, exact_length=exact_length)
    if (not allow_empty and not result) or any(
        type(item) is not str or not item for item in result
    ):
        raise SegmentCheckpointContractError(f"{path}_INVALID")
    return result


def _is_ordered_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    )


def _validate_exact_mapping(
    actual: Mapping[str, Any],
    expected: Mapping[str, Any],
    path: str,
) -> None:
    if not isinstance(actual, Mapping):
        raise SegmentCheckpointContractError(f"{path} must be a mapping")
    missing = set(expected) - set(actual)
    unknown = set(actual) - set(expected)
    if missing or unknown:
        raise SegmentCheckpointContractError(
            f"{path} keys invalid: missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    for key, expected_value in expected.items():
        value = actual[key]
        child = f"{path}.{key}"
        if isinstance(expected_value, Mapping):
            _validate_exact_mapping(value, expected_value, child)
        elif type(value) is not type(expected_value) or value != expected_value:
            raise SegmentCheckpointContractError(
                f"{child}={value!r} is unsupported; expected {expected_value!r}"
            )


def _is_finite_number(value: Any) -> bool:
    return type(value) in {int, float} and math.isfinite(value)
