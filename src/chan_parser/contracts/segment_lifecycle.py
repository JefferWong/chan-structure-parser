"""Pure Phase 2 contract for deterministic Segment lifecycle event intents.

The contract derives content-addressed intent records.  It does not create
``LifecycleEvent`` objects, emit events, retain state, or integrate with a
parser or checkpoint implementation.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any

from ..domain.lifecycle import EventType, StructureStatus, StrokeDirection
from ..domain.segment import Segment
from ..contracts.segment_rules import (
    DestructionCase,
    PrimaryDestructionEvidence,
    SegmentDirection,
)


class SegmentLifecycleContractError(ValueError):
    """Raised when lifecycle profile or derivation input fails closed."""


@dataclass(frozen=True)
class SegmentLifecycleEventIntent:
    """An immutable event intention; ``intent_key`` is not an EventLog ID."""

    intent_key: str
    event_type: str
    object_type: str
    object_id: str
    logical_id: str
    occurred_at_bar_id: str
    reason_code: str
    rule_profile: str
    rule_version: str
    detail: Mapping[str, Any]


_EXPECTED_PROFILE: dict[str, Any] = {
    "profile_id": "minimal_segment_lifecycle_contract_v1",
    "profile_version": "0.1.0",
    "status": "CONTRACT_ONLY",
    "source_segment_profile_id": "minimal_segment_engine_core_v1",
    "source_segment_profile_version": "0.1.0",
    "source_segment_baseline_commit": (
        "c1d08aa86661838bb21561871e7f3af7ad4de235"
    ),
    "transition": {
        "source_status": "ABSENT",
        "target_status": "CONFIRMED",
        "provisional_allowed": False,
        "implicit_confirmation_allowed": False,
        "caller_confirmation_timestamp_allowed": False,
        "future_visible_confirmation_allowed": False,
    },
    "direct_confirmed_intents": {
        "event_types": [EventType.CREATED, EventType.CONFIRMED],
        "occurred_at_bar_source": "segment.confirmed_at_bar",
        "same_bar_required": True,
        "object_type": "segment",
        "created_reason_code": "SEGMENT_FIRST_CASE_CREATED",
        "confirmed_reason_code": "SEGMENT_FIRST_CASE_CONFIRMED",
    },
    "zero_intent_outcomes": [
        "SEGMENT_FEATURE_WINDOW_INCOMPLETE",
        "SEGMENT_PRIMARY_FRACTAL_NOT_FOUND",
        "SEGMENT_SECOND_CASE_PENDING",
    ],
    "integration": {
        "event_emission_enabled": False,
        "parser_integration_enabled": False,
        "checkpoint_integration_enabled": False,
        "bounded_tail_integration_enabled": False,
        "full_incremental_integration_enabled": False,
        "second_case_confirmation_enabled": False,
        "center_or_zhongshu_enabled": False,
    },
}

_FIRST_CASE_OUTCOME = "SEGMENT_FIRST_CASE_CONFIRMED"
_ZERO_OUTCOMES = frozenset(_EXPECTED_PROFILE["zero_intent_outcomes"])


def validate_segment_lifecycle_profile(profile: Mapping[str, Any]) -> None:
    """Require the complete lifecycle profile with exact values and types."""

    _validate_exact_mapping(profile, _EXPECTED_PROFILE, "profile")


def derive_segment_lifecycle_intents(
    *,
    outcome_code: str,
    segment: Segment | None,
    primary_evidence: PrimaryDestructionEvidence | None,
) -> tuple[SegmentLifecycleEventIntent, ...]:
    """Derive canonical ordered intents without emitting or retaining them."""

    if type(outcome_code) is not str or not outcome_code:
        raise SegmentLifecycleContractError("outcome_code must be a non-empty string")
    if outcome_code in _ZERO_OUTCOMES:
        return _derive_zero_intents(outcome_code, segment, primary_evidence)
    if outcome_code != _FIRST_CASE_OUTCOME:
        raise SegmentLifecycleContractError(
            f"unsupported Segment lifecycle outcome: {outcome_code!r}"
        )

    _validate_confirmed_first_case(segment, primary_evidence)
    assert segment is not None
    assert primary_evidence is not None
    endpoint = primary_evidence.endpoint
    assert endpoint is not None

    detail_payload = {
        "segment_id": segment.segment_id,
        "direction": segment.direction.value,
        "start_stroke_id": segment.start_stroke_id,
        "end_stroke_id": segment.end_stroke_id,
        "stroke_ids": tuple(segment.stroke_ids),
        "feature_sequence_stroke_ids": tuple(
            segment.feature_sequence_stroke_ids
        ),
        "destruction_evidence_stroke_ids": tuple(
            segment.destruction_evidence_stroke_ids
        ),
        "primary_evidence_key": primary_evidence.evidence_key,
        "primary_sequence_id": primary_evidence.primary_sequence_id,
        "primary_element_logical_ids": tuple(
            primary_evidence.primary_element_logical_ids
        ),
        "feature_fractal_type": primary_evidence.feature_fractal_type.value,
        "endpoint": MappingProxyType({
            "endpoint_id": endpoint.endpoint_id,
            "bar_index": endpoint.bar_index,
            "price": endpoint.price,
            "defining_stroke_logical_ids": tuple(
                endpoint.defining_stroke_logical_ids
            ),
        }),
    }
    detail = MappingProxyType(detail_payload)
    occurred_at = f"bar_{segment.confirmed_at_bar + 1:06d}"
    specifications = (
        (EventType.CREATED, "SEGMENT_FIRST_CASE_CREATED"),
        (EventType.CONFIRMED, "SEGMENT_FIRST_CASE_CONFIRMED"),
    )
    return tuple(
        _make_intent(
            event_type=event_type,
            reason_code=reason_code,
            segment=segment,
            occurred_at_bar_id=occurred_at,
            detail=detail,
        )
        for event_type, reason_code in specifications
    )


def filter_new_segment_lifecycle_intents(
    intents: Sequence[SegmentLifecycleEventIntent],
    existing_intent_keys: Set[str],
) -> tuple[SegmentLifecycleEventIntent, ...]:
    """Return unseen canonical intents in order without mutating caller state."""

    if not isinstance(intents, Sequence) or isinstance(intents, (str, bytes)):
        raise SegmentLifecycleContractError("intents must be an ordered sequence")
    if not isinstance(existing_intent_keys, Set) or any(
        type(key) is not str or not key for key in existing_intent_keys
    ):
        raise SegmentLifecycleContractError(
            "existing_intent_keys must be a set of non-empty strings"
        )
    canonical = tuple(intents)
    if canonical:
        if len(canonical) != 2 or tuple(
            item.event_type
            if isinstance(item, SegmentLifecycleEventIntent)
            else None
            for item in canonical
        ) != (EventType.CREATED, EventType.CONFIRMED):
            raise SegmentLifecycleContractError(
                "intents must be the canonical CREATED, CONFIRMED sequence"
            )
    if any(not isinstance(item, SegmentLifecycleEventIntent) for item in canonical):
        raise SegmentLifecycleContractError(
            "SegmentLifecycleEventIntent values required"
        )
    keys = tuple(item.intent_key for item in canonical)
    if any(type(key) is not str or not key for key in keys) or len(keys) != len(
        set(keys)
    ):
        raise SegmentLifecycleContractError("intent keys must be unique and non-empty")
    if any(item.intent_key != _intent_key_for(item) for item in canonical):
        raise SegmentLifecycleContractError(
            "intent_key does not match canonical intent content"
        )
    first, second = canonical if canonical else (None, None)
    if canonical and (
        first.reason_code != "SEGMENT_FIRST_CASE_CREATED"
        or second.reason_code != "SEGMENT_FIRST_CASE_CONFIRMED"
        or first.object_type != "segment"
        or second.object_type != "segment"
        or first.object_id != second.object_id
        or first.logical_id != second.logical_id
        or first.occurred_at_bar_id != second.occurred_at_bar_id
        or first.rule_profile != second.rule_profile
        or first.rule_version != second.rule_version
        or first.detail != second.detail
    ):
        raise SegmentLifecycleContractError(
            "intent pair does not match canonical direct-confirmed semantics"
        )
    if canonical:
        created_key, confirmed_key = keys
        if (
            confirmed_key in existing_intent_keys
            and created_key not in existing_intent_keys
        ):
            raise SegmentLifecycleContractError(
                "SEGMENT_LIFECYCLE_HISTORY_NOT_CANONICAL_PREFIX"
            )
    return tuple(
        item for item in canonical if item.intent_key not in existing_intent_keys
    )


def _derive_zero_intents(
    outcome_code: str,
    segment: Segment | None,
    evidence: PrimaryDestructionEvidence | None,
) -> tuple[SegmentLifecycleEventIntent, ...]:
    if segment is not None:
        raise SegmentLifecycleContractError(
            f"{outcome_code} forbids a materialized Segment"
        )
    if outcome_code == "SEGMENT_SECOND_CASE_PENDING":
        if (
            type(evidence) is not PrimaryDestructionEvidence
            or evidence.destruction_case != DestructionCase.SECOND_CASE_PENDING
        ):
            raise SegmentLifecycleContractError(
                "SECOND_CASE_PENDING evidence required"
            )
        if evidence.reason_code != "SECOND_CASE_GAP_PENDING":
            raise SegmentLifecycleContractError(
                "canonical SECOND_CASE_PENDING reason required"
            )
    elif evidence is not None:
        raise SegmentLifecycleContractError(
            f"{outcome_code} forbids primary evidence"
        )
    return ()


def _validate_confirmed_first_case(
    segment: Segment | None,
    evidence: PrimaryDestructionEvidence | None,
) -> None:
    if not isinstance(segment, Segment):
        raise SegmentLifecycleContractError("confirmed first-case Segment required")
    if type(evidence) is not PrimaryDestructionEvidence:
        raise SegmentLifecycleContractError("primary destruction evidence required")
    if evidence.destruction_case != DestructionCase.FIRST_CASE:
        raise SegmentLifecycleContractError("FIRST_CASE primary evidence required")
    endpoint = evidence.endpoint
    if endpoint is None:
        raise SegmentLifecycleContractError("first-case endpoint evidence required")
    if type(segment.status) is not StructureStatus or (
        segment.status != StructureStatus.CONFIRMED
    ):
        raise SegmentLifecycleContractError("Segment status must be CONFIRMED")
    if type(segment.direction) is not StrokeDirection:
        raise SegmentLifecycleContractError("Segment direction must be canonical")
    for name in ("object_id", "logical_id", "segment_id"):
        value = getattr(segment, name)
        if type(value) is not str or not value:
            raise SegmentLifecycleContractError(f"Segment {name} must be non-empty")
    if segment.rule_profile != "minimal_segment_engine_core_v1":
        raise SegmentLifecycleContractError("unsupported Segment rule_profile")
    if segment.rule_version != "0.1.0":
        raise SegmentLifecycleContractError("unsupported Segment rule_version")
    for name in ("start_stroke_id", "end_stroke_id"):
        value = getattr(segment, name)
        if type(value) is not str or not value:
            raise SegmentLifecycleContractError(f"Segment {name} must be non-empty")
    for name in (
        "stroke_ids",
        "feature_sequence_stroke_ids",
        "destruction_evidence_stroke_ids",
    ):
        values = getattr(segment, name)
        if (
            type(values) is not list
            or not values
            or any(type(value) is not str or not value for value in values)
            or len(values) != len(set(values))
        ):
            raise SegmentLifecycleContractError(
                f"Segment {name} must contain unique non-empty strings"
            )
    if evidence.reason_code != "FIRST_CASE_NO_GAP_CONFIRMED":
        raise SegmentLifecycleContractError("canonical FIRST_CASE reason required")
    if type(segment.created_at_bar) is not int or segment.created_at_bar < 0:
        raise SegmentLifecycleContractError("created_at_bar must be nonnegative int")
    if type(segment.confirmed_at_bar) is not int or segment.confirmed_at_bar < 0:
        raise SegmentLifecycleContractError("confirmed_at_bar must be nonnegative int")
    if segment.created_at_bar != segment.confirmed_at_bar:
        raise SegmentLifecycleContractError(
            "direct-confirmed Segment must be created and confirmed on the same bar"
        )
    if type(segment.end_bar_index) is not int or (
        segment.confirmed_at_bar < segment.end_bar_index
    ):
        raise SegmentLifecycleContractError(
            "Segment confirmation cannot precede its endpoint"
        )
    expected_direction = (
        SegmentDirection.UP
        if segment.direction == StrokeDirection.UP
        else SegmentDirection.DOWN
        if segment.direction == StrokeDirection.DOWN
        else None
    )
    if evidence.candidate_direction != expected_direction:
        raise SegmentLifecycleContractError("Segment and evidence direction mismatch")
    if endpoint.bar_index != segment.end_bar_index:
        raise SegmentLifecycleContractError("Segment and evidence endpoint bar mismatch")
    if (
        not _is_finite_number(segment.end_price)
        or endpoint.price != segment.end_price
    ):
        raise SegmentLifecycleContractError(
            "Segment and evidence endpoint price mismatch"
        )


def _make_intent(
    *,
    event_type: str,
    reason_code: str,
    segment: Segment,
    occurred_at_bar_id: str,
    detail: Mapping[str, Any],
) -> SegmentLifecycleEventIntent:
    content = {
        "event_type": event_type,
        "object_type": "segment",
        "object_id": segment.object_id,
        "logical_id": segment.logical_id,
        "occurred_at_bar_id": occurred_at_bar_id,
        "reason_code": reason_code,
        "rule_profile": segment.rule_profile,
        "rule_version": segment.rule_version,
        "detail": _json_ready(detail),
    }
    partial = SegmentLifecycleEventIntent(intent_key="", detail=detail, **{
        key: value for key, value in content.items() if key != "detail"
    })
    return SegmentLifecycleEventIntent(
        **{
            **partial.__dict__,
            "intent_key": _intent_key_for(partial),
        }
    )


def _intent_key_for(intent: SegmentLifecycleEventIntent) -> str:
    content = {
        "event_type": intent.event_type,
        "object_type": intent.object_type,
        "object_id": intent.object_id,
        "logical_id": intent.logical_id,
        "occurred_at_bar_id": intent.occurred_at_bar_id,
        "reason_code": intent.reason_code,
        "rule_profile": intent.rule_profile,
        "rule_version": intent.rule_version,
        "detail": _json_ready(intent.detail),
    }
    encoded = json.dumps(
        content,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"segment_lifecycle:{hashlib.sha256(encoded).hexdigest()[:24]}"


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_ready(value[key]) for key in sorted(value)}
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    return value


def _validate_exact_mapping(
    actual: Mapping[str, Any],
    expected: Mapping[str, Any],
    path: str,
) -> None:
    if not isinstance(actual, Mapping):
        raise SegmentLifecycleContractError(f"{path} must be a mapping")
    missing = set(expected) - set(actual)
    unknown = set(actual) - set(expected)
    if missing or unknown:
        raise SegmentLifecycleContractError(
            f"{path} keys invalid: missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    for key, expected_value in expected.items():
        value = actual[key]
        child = f"{path}.{key}"
        if isinstance(expected_value, Mapping):
            _validate_exact_mapping(value, expected_value, child)
        elif type(value) is not type(expected_value) or value != expected_value:
            raise SegmentLifecycleContractError(
                f"{child}={value!r} is unsupported; expected {expected_value!r}"
            )


def _is_finite_number(value: Any) -> bool:
    return type(value) in {int, float} and math.isfinite(value)
