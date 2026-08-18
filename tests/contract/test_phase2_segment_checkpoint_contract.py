"""Contract tests for pure Segment checkpoint semantic state."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest
import yaml

from chan_parser.audit.event_log import EventLog
import chan_parser.contracts.segment_checkpoint as checkpoint_contract
from chan_parser.contracts.segment_checkpoint import (
    SegmentCheckpointContractError,
    SegmentCheckpointState,
    derive_segment_checkpoint_state,
    validate_segment_checkpoint_profile,
    validate_segment_checkpoint_state,
)
from chan_parser.domain.lifecycle import StructureStatus, StrokeDirection
from chan_parser.domain.segment import Segment
from chan_parser.domain.stroke import Stroke
from chan_parser.engine.segment import SegmentEngine
from chan_parser.engine.segment_lifecycle_emitter import SegmentLifecycleEmitter


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "configs/profiles/minimal_segment_checkpoint_contract_v1.yaml"
ENGINE_PROFILE = ROOT / "configs/profiles/minimal_segment_engine_core_v1.yaml"
EMISSION_PROFILE = ROOT / "configs/profiles/minimal_segment_lifecycle_emission_v1.yaml"
ZERO_EVENT_OUTCOMES = (
    "SEGMENT_FEATURE_WINDOW_INCOMPLETE",
    "SEGMENT_PRIMARY_FRACTAL_NOT_FOUND",
    "SEGMENT_SECOND_CASE_PENDING",
)


def profile() -> dict:
    return yaml.safe_load(PROFILE.read_text(encoding="utf-8"))


def loaded(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def make_strokes(points: list[float]) -> list[Stroke]:
    strokes: list[Stroke] = []
    for index, (start, end) in enumerate(zip(points, points[1:])):
        direction = StrokeDirection.UP if start < end else StrokeDirection.DOWN
        strokes.append(
            Stroke(
                object_id=f"stroke_{index:06d}_r1",
                logical_id=f"stroke:{index}",
                revision=1,
                status=StructureStatus.CONFIRMED,
                created_at_bar=index + 1,
                confirmed_at_bar=index + 1,
                rule_profile="minimal_strict_v1",
                rule_version="1.0.0",
                stroke_id=f"stroke_{index:06d}",
                direction=direction,
                start_fractal_id=f"fx:{index}",
                end_fractal_id=f"fx:{index + 1}",
                start_price=start,
                end_price=end,
                start_bar_index=index,
                end_bar_index=index + 1,
                merged_bar_count=2,
                max_price=max(start, end),
                min_price=min(start, end),
                price_range=abs(end - start),
                confirmation_requirements=[],
                repaint_risk="NONE",
            )
        )
    return strokes


def first_case_inputs() -> tuple[list[Stroke], Segment, list[dict]]:
    strokes = make_strokes([0, 10, 4, 12, 6, 11, 5])
    result = SegmentEngine(loaded(ENGINE_PROFILE)).process_primary(
        strokes,
        sequence_id="primary:checkpoint",
    )
    assert result.reason_code == "SEGMENT_FIRST_CASE_CONFIRMED"
    assert result.segment is not None
    event_log = EventLog()
    SegmentLifecycleEmitter(loaded(EMISSION_PROFILE)).emit(
        result=result,
        source_strokes=strokes,
        event_log=event_log,
    )
    return strokes, result.segment, event_log.to_list()


def derive_first_case() -> tuple[SegmentCheckpointState, list[Stroke], Segment, list[dict]]:
    strokes, segment, events = first_case_inputs()
    state = derive_segment_checkpoint_state(
        outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
        candidate_direction=StrokeDirection.UP,
        source_strokes=strokes,
        segment=segment,
        lifecycle_events=events,
    )
    return state, strokes, segment, events


def validate_first_case(
    state: SegmentCheckpointState,
    strokes: list[Stroke],
    segment: Segment,
    events: list[dict],
) -> None:
    validate_segment_checkpoint_state(
        state,
        outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
        candidate_direction=StrokeDirection.UP,
        source_strokes=strokes,
        segment=segment,
        lifecycle_events=events,
    )


def refresh_intent_key(event: dict) -> None:
    producer_detail = {
        key: event["detail"][key]
        for key in checkpoint_contract._PRODUCER_DETAIL_KEYS
    }
    event["detail"][checkpoint_contract._INTENT_KEY_FIELD] = (
        checkpoint_contract._intent_key_for_event(event, producer_detail)
    )


def refresh_binding_key(events: list[dict]) -> None:
    for event in events:
        event["detail"][checkpoint_contract._BINDING_KEY_FIELD] = (
            checkpoint_contract._semantic_digest(
                event["detail"]["emission_binding"]
            )
        )


def synchronize_segment_detail(segment: Segment, events: list[dict]) -> None:
    for event in events:
        event["object_id"] = segment.object_id
        event["logical_id"] = segment.logical_id
        event["rule_profile"] = segment.rule_profile
        event["rule_version"] = segment.rule_version
        event["occurred_at_bar_id"] = f"bar_{segment.confirmed_at_bar + 1:06d}"
        detail = event["detail"]
        detail.update(
            {
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
            }
        )
        refresh_intent_key(event)


def self_consistent_prefix_segment(
    segment: Segment,
    strokes: list[Stroke],
    length: int,
) -> Segment:
    first = strokes[0]
    boundary = strokes[length - 1]
    direction_code = "U" if segment.direction == StrokeDirection.UP else "D"
    segment_id = (
        f"segment_{first.start_bar_index + 1:06d}_"
        f"{boundary.end_bar_index + 1:06d}_{direction_code}"
    )
    return replace(
        segment,
        segment_id=segment_id,
        object_id=f"{segment_id}_r1",
        logical_id=f"segment:{first.logical_id}->{boundary.logical_id}",
        start_stroke_id=first.stroke_id,
        end_stroke_id=boundary.stroke_id,
        stroke_ids=[stroke.stroke_id for stroke in strokes[:length]],
        start_bar_index=first.start_bar_index,
        end_bar_index=boundary.end_bar_index,
        start_price=first.start_price,
        end_price=boundary.end_price,
    )


def test_profile_exact_mapping_accepts_frozen_profile():
    assert validate_segment_checkpoint_profile(profile()) is None


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("profile_version",), "0.2.0"),
        (("source_lifecycle_emission_baseline_commit",), "wrong"),
        (("checkpoint", "semantic_state_only"), False),
        (("checkpoint", "partial_lifecycle_prefix_allowed"), True),
        (("integration", "checkpoint_runtime_integration_enabled"), True),
    ],
)
def test_profile_wrong_value_fails_closed(path, value):
    value_map = profile()
    target = value_map
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(SegmentCheckpointContractError):
        validate_segment_checkpoint_profile(value_map)


@pytest.mark.parametrize("mutation", ["missing", "unknown", "wrong_type"])
def test_profile_shape_fails_closed(mutation):
    value_map = profile()
    if mutation == "missing":
        del value_map["binding"]["source_strokes_required"]
    elif mutation == "unknown":
        value_map["checkpoint"]["runtime_restore_enabled"] = False
    else:
        value_map["checkpoint"]["semantic_state_only"] = 1
    with pytest.raises(SegmentCheckpointContractError):
        validate_segment_checkpoint_profile(value_map)


def test_valid_first_case_freezes_source_segment_and_complete_lifecycle():
    state, strokes, segment, events = derive_first_case()
    assert state.source_stroke_logical_ids == tuple(s.logical_id for s in strokes)
    assert state.source_stroke_object_ids == tuple(s.object_id for s in strokes)
    assert state.source_stroke_content_hashes == tuple(s.content_hash() for s in strokes)
    assert len(state.source_stroke_semantic_hashes) == len(strokes)
    assert all(len(value) == 64 for value in state.source_stroke_semantic_hashes)
    assert state.segment_id == segment.segment_id
    assert state.segment_object_id == segment.object_id
    assert state.segment_logical_id == segment.logical_id
    assert state.segment_revision == segment.revision
    assert state.segment_content_hash == segment.content_hash()
    assert len(state.segment_semantic_hash or "") == 64
    assert state.segment_created_at_bar == segment.created_at_bar
    assert state.segment_confirmed_at_bar == segment.confirmed_at_bar
    assert state.lifecycle_intent_keys == tuple(
        event["detail"]["segment_lifecycle_intent_key"] for event in events
    )
    assert len(state.lifecycle_binding_key or "") == 64
    assert len(state.lifecycle_event_semantic_hashes) == 2
    assert all(len(value) == 64 for value in state.lifecycle_event_semantic_hashes)
    assert len(state.state_key) == 64
    validate_first_case(state, strokes, segment, events)


def test_same_first_case_derives_exact_equal_state_and_key_twice():
    _, strokes, segment, events = derive_first_case()
    kwargs = {
        "outcome_code": "SEGMENT_FIRST_CASE_CONFIRMED",
        "candidate_direction": StrokeDirection.UP,
        "source_strokes": strokes,
        "segment": segment,
        "lifecycle_events": events,
    }
    first = derive_segment_checkpoint_state(**kwargs)
    second = derive_segment_checkpoint_state(**kwargs)
    assert first == second
    assert first.state_key == second.state_key


def test_source_logical_identity_tamper_fails_closed():
    state, strokes, segment, events = derive_first_case()
    strokes[-1] = replace(strokes[-1], logical_id="stroke:foreign")
    with pytest.raises(SegmentCheckpointContractError):
        validate_first_case(state, strokes, segment, events)


def test_source_object_identity_tamper_fails_state_validation():
    state, strokes, segment, events = derive_first_case()
    strokes[-1] = replace(strokes[-1], object_id="stroke_foreign_r1")
    with pytest.raises(SegmentCheckpointContractError, match="STATE_MISMATCH"):
        validate_first_case(state, strokes, segment, events)


def test_source_content_mutation_with_same_ids_fails_state_validation():
    state, strokes, segment, events = derive_first_case()
    strokes[-1] = replace(strokes[-1], end_fractal_id="fx:foreign")
    with pytest.raises(SegmentCheckpointContractError, match="STATE_MISMATCH"):
        validate_first_case(state, strokes, segment, events)


def test_source_order_mutation_fails_closed():
    state, strokes, segment, events = derive_first_case()
    strokes[0], strokes[1] = strokes[1], strokes[0]
    with pytest.raises(SegmentCheckpointContractError):
        validate_first_case(state, strokes, segment, events)


@pytest.mark.parametrize("field", ["logical_id", "object_id"])
def test_foreign_segment_identity_fails_closed(field):
    state, strokes, segment, events = derive_first_case()
    segment = replace(segment, **{field: f"foreign-{field}"})
    with pytest.raises(SegmentCheckpointContractError, match="REBINDING_MISMATCH"):
        validate_first_case(state, strokes, segment, events)


@pytest.mark.parametrize("mutation", ["missing", "reordered", "extra"])
def test_segment_stroke_ids_must_be_exact_source_prefix(mutation):
    state, strokes, segment, events = derive_first_case()
    ids = list(segment.stroke_ids)
    if mutation == "missing":
        ids.pop()
    elif mutation == "reordered":
        ids[0], ids[1] = ids[1], ids[0]
    else:
        ids.append("stroke:injected")
    segment = replace(segment, stroke_ids=ids)
    with pytest.raises(SegmentCheckpointContractError, match="SOURCE_PREFIX_MISMATCH"):
        validate_first_case(state, strokes, segment, events)


@pytest.mark.parametrize("history", ["empty", "created_only", "confirmed_only"])
def test_first_case_partial_lifecycle_checkpoint_is_rejected(history):
    state, strokes, segment, events = derive_first_case()
    if history == "empty":
        events = []
    elif history == "created_only":
        events = events[:1]
    else:
        events = events[1:]
    with pytest.raises(SegmentCheckpointContractError, match="LIFECYCLE_INCOMPLETE"):
        validate_first_case(state, strokes, segment, events)


def test_lifecycle_order_swapped_fails_closed():
    state, strokes, segment, events = derive_first_case()
    with pytest.raises(SegmentCheckpointContractError, match="FIELD_MISMATCH"):
        validate_first_case(state, strokes, segment, list(reversed(events)))


def test_duplicate_lifecycle_intent_keys_fail_closed():
    state, strokes, segment, events = derive_first_case()
    events[1]["detail"]["segment_lifecycle_intent_key"] = events[0]["detail"][
        "segment_lifecycle_intent_key"
    ]
    with pytest.raises(SegmentCheckpointContractError, match="INTENT_KEY_MISMATCH"):
        validate_first_case(state, strokes, segment, events)


def test_different_lifecycle_binding_keys_fail_closed():
    state, strokes, segment, events = derive_first_case()
    events[1]["detail"]["segment_lifecycle_binding_key"] = "0" * 64
    with pytest.raises(SegmentCheckpointContractError, match="BINDING_KEY_CONTENT_MISMATCH"):
        validate_first_case(state, strokes, segment, events)


@pytest.mark.parametrize("field", ["source_stroke_logical_ids", "source_stroke_ids"])
def test_emission_binding_source_ids_mismatch_fails_closed(field):
    state, strokes, segment, events = derive_first_case()
    for event in events:
        event["detail"]["emission_binding"][field] = ("foreign",)
    with pytest.raises(SegmentCheckpointContractError, match="EMISSION_SOURCE"):
        validate_first_case(state, strokes, segment, events)


def test_emission_binding_confirmation_bar_mismatch_fails_closed():
    state, strokes, segment, events = derive_first_case()
    for event in events:
        event["detail"]["emission_binding"]["confirmation_bar"] += 1
    with pytest.raises(SegmentCheckpointContractError, match="CONFIRMATION_BAR_MISMATCH"):
        validate_first_case(state, strokes, segment, events)


@pytest.mark.parametrize("outcome_code", ZERO_EVENT_OUTCOMES)
def test_zero_event_outcome_freezes_no_segment_or_lifecycle(outcome_code):
    strokes = make_strokes([0, 10, 4])
    state = derive_segment_checkpoint_state(
        outcome_code=outcome_code,
        candidate_direction=StrokeDirection.UP,
        source_strokes=strokes,
        segment=None,
        lifecycle_events=[],
    )
    assert state.segment_id is None
    assert state.segment_object_id is None
    assert state.segment_logical_id is None
    assert state.segment_revision is None
    assert state.segment_content_hash is None
    assert state.segment_semantic_hash is None
    assert state.segment_created_at_bar is None
    assert state.segment_confirmed_at_bar is None
    assert state.lifecycle_intent_keys == ()
    assert state.lifecycle_binding_key is None
    assert state.lifecycle_event_semantic_hashes == ()


@pytest.mark.parametrize("outcome_code", ZERO_EVENT_OUTCOMES)
def test_zero_event_outcome_rejects_segment(outcome_code):
    _, strokes, segment, _ = derive_first_case()
    with pytest.raises(SegmentCheckpointContractError, match="HAS_SEGMENT"):
        derive_segment_checkpoint_state(
            outcome_code=outcome_code,
            candidate_direction=StrokeDirection.UP,
            source_strokes=strokes,
            segment=segment,
            lifecycle_events=[],
        )


@pytest.mark.parametrize("outcome_code", ZERO_EVENT_OUTCOMES)
def test_zero_event_outcome_rejects_lifecycle_event(outcome_code):
    _, strokes, _, events = derive_first_case()
    with pytest.raises(SegmentCheckpointContractError, match="HAS_EVENTS"):
        derive_segment_checkpoint_state(
            outcome_code=outcome_code,
            candidate_direction=StrokeDirection.UP,
            source_strokes=strokes,
            segment=None,
            lifecycle_events=events[:1],
        )


def test_unknown_outcome_fails_closed():
    strokes = make_strokes([0, 10, 4])
    with pytest.raises(SegmentCheckpointContractError, match="OUTCOME_UNSUPPORTED"):
        derive_segment_checkpoint_state(
            outcome_code="SEGMENT_FUTURE_OUTCOME",
            candidate_direction=StrokeDirection.UP,
            source_strokes=strokes,
            segment=None,
            lifecycle_events=[],
        )


def test_tampered_state_key_fails_validation_without_repair():
    state, strokes, segment, events = derive_first_case()
    tampered = replace(state, state_key="0" * 64)
    with pytest.raises(SegmentCheckpointContractError, match="STATE_MISMATCH"):
        validate_first_case(tampered, strokes, segment, events)
    assert tampered.state_key == "0" * 64


def test_tampered_semantic_field_with_old_state_key_fails_validation():
    state, strokes, segment, events = derive_first_case()
    tampered = replace(state, segment_object_id="segment:foreign")
    with pytest.raises(SegmentCheckpointContractError, match="STATE_MISMATCH"):
        validate_first_case(tampered, strokes, segment, events)
    assert tampered.state_key == state.state_key


def test_each_semantic_change_produces_a_different_state_key():
    strokes = make_strokes([0, 10, 4])
    first = derive_segment_checkpoint_state(
        outcome_code="SEGMENT_FEATURE_WINDOW_INCOMPLETE",
        candidate_direction=StrokeDirection.UP,
        source_strokes=strokes,
        segment=None,
        lifecycle_events=[],
    )
    changed = list(strokes)
    changed[-1] = replace(changed[-1], object_id="stroke_foreign_r1")
    second = derive_segment_checkpoint_state(
        outcome_code="SEGMENT_FEATURE_WINDOW_INCOMPLETE",
        candidate_direction=StrokeDirection.UP,
        source_strokes=changed,
        segment=None,
        lifecycle_events=[],
    )
    assert first != second
    assert first.state_key != second.state_key


def test_inputs_remain_unchanged_after_derive_and_validate():
    _, strokes, segment, events = derive_first_case()
    before = deepcopy((strokes, segment, events))
    state = derive_segment_checkpoint_state(
        outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
        candidate_direction=StrokeDirection.UP,
        source_strokes=strokes,
        segment=segment,
        lifecycle_events=events,
    )
    validate_first_case(state, strokes, segment, events)
    assert (strokes, segment, events) == before


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("confirmed_at_bar", 2),
        ("revision", 2),
        ("created_at_bar", 0),
        ("rule_profile", "alternate_strict_v1"),
        ("rule_version", "1.0.1"),
        ("max_price", 11),
        ("min_price", -1),
    ],
)
def test_full_source_semantic_mutation_changes_state_with_same_content_hash(
    field,
    value,
):
    state, strokes, segment, events = derive_first_case()
    original_content_hash = strokes[0].content_hash()
    strokes[0] = replace(strokes[0], **{field: value})
    assert strokes[0].content_hash() == original_content_hash
    with pytest.raises(SegmentCheckpointContractError, match="STATE_MISMATCH"):
        validate_first_case(state, strokes, segment, events)
    fresh = derive_segment_checkpoint_state(
        outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
        candidate_direction=StrokeDirection.UP,
        source_strokes=strokes,
        segment=segment,
        lifecycle_events=events,
    )
    assert fresh.source_stroke_semantic_hashes != state.source_stroke_semantic_hashes
    assert fresh.state_key != state.state_key


def test_plain_string_stroke_status_fails_closed():
    _, strokes, segment, events = derive_first_case()
    strokes[0] = replace(strokes[0], status="CONFIRMED")
    with pytest.raises(SegmentCheckpointContractError, match="SOURCE_STATUS_INVALID"):
        derive_segment_checkpoint_state(
            outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
            candidate_direction=StrokeDirection.UP,
            source_strokes=strokes,
            segment=segment,
            lifecycle_events=events,
        )


def test_plain_string_segment_status_fails_closed():
    _, strokes, segment, events = derive_first_case()
    segment = replace(segment, status="CONFIRMED")
    with pytest.raises(SegmentCheckpointContractError, match="SEGMENT_STATUS_INVALID"):
        derive_segment_checkpoint_state(
            outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
            candidate_direction=StrokeDirection.UP,
            source_strokes=strokes,
            segment=segment,
            lifecycle_events=events,
        )


@pytest.mark.parametrize("prefix_length", [1, 2])
def test_self_consistent_short_or_even_segment_prefix_fails_closed(prefix_length):
    _, strokes, segment, events = derive_first_case()
    forged = self_consistent_prefix_segment(segment, strokes, prefix_length)
    with pytest.raises(SegmentCheckpointContractError, match="CANDIDATE_PREFIX_INVALID"):
        derive_segment_checkpoint_state(
            outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
            candidate_direction=StrokeDirection.UP,
            source_strokes=strokes,
            segment=forged,
            lifecycle_events=events,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("segment_id", "segment_foreign"),
        ("object_id", "segment_foreign_r1"),
        ("logical_id", "segment:foreign"),
        ("revision", 2),
        ("rule_profile", "foreign_profile"),
        ("rule_version", "9.9.9"),
    ],
)
def test_synchronized_foreign_segment_identity_or_metadata_fails_rebinding(
    field,
    value,
):
    _, strokes, segment, events = derive_first_case()
    forged = replace(segment, **{field: value})
    synchronize_segment_detail(forged, events)
    with pytest.raises(SegmentCheckpointContractError, match="REBINDING_MISMATCH"):
        derive_segment_checkpoint_state(
            outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
            candidate_direction=StrokeDirection.UP,
            source_strokes=strokes,
            segment=forged,
            lifecycle_events=events,
        )


def test_segment_creation_confirmation_divergence_fails_closed():
    _, strokes, segment, events = derive_first_case()
    forged = replace(segment, created_at_bar=segment.confirmed_at_bar - 1)
    synchronize_segment_detail(forged, events)
    with pytest.raises(SegmentCheckpointContractError, match="CONFIRMATION_INVALID"):
        derive_segment_checkpoint_state(
            outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
            candidate_direction=StrokeDirection.UP,
            source_strokes=strokes,
            segment=forged,
            lifecycle_events=events,
        )


def test_wrong_occurred_at_bar_with_recomputed_intent_key_fails_closed():
    _, strokes, segment, events = derive_first_case()
    events[0]["occurred_at_bar_id"] = "bar_999999"
    refresh_intent_key(events[0])
    with pytest.raises(SegmentCheckpointContractError, match="occurred_at_bar_id"):
        derive_segment_checkpoint_state(
            outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
            candidate_direction=StrokeDirection.UP,
            source_strokes=strokes,
            segment=segment,
            lifecycle_events=events,
        )


@pytest.mark.parametrize("field", ["rule_profile", "rule_version"])
def test_wrong_synchronized_event_profile_or_version_fails_closed(field):
    _, strokes, segment, events = derive_first_case()
    for event in events:
        event[field] = "foreign"
        refresh_intent_key(event)
    with pytest.raises(SegmentCheckpointContractError, match=field):
        derive_segment_checkpoint_state(
            outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
            candidate_direction=StrokeDirection.UP,
            source_strokes=strokes,
            segment=segment,
            lifecycle_events=events,
        )


def test_non_none_replaced_by_fails_closed():
    _, strokes, segment, events = derive_first_case()
    events[0]["replaced_by"] = "segment:replacement"
    with pytest.raises(SegmentCheckpointContractError, match="replaced_by"):
        derive_segment_checkpoint_state(
            outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
            candidate_direction=StrokeDirection.UP,
            source_strokes=strokes,
            segment=segment,
            lifecycle_events=events,
        )


@pytest.mark.parametrize("location", ["event", "detail"])
def test_unknown_lifecycle_key_fails_closed(location):
    _, strokes, segment, events = derive_first_case()
    target = events[0] if location == "event" else events[0]["detail"]
    target["unknown"] = "forbidden"
    with pytest.raises(SegmentCheckpointContractError, match="KEYS_INVALID"):
        derive_segment_checkpoint_state(
            outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
            candidate_direction=StrokeDirection.UP,
            source_strokes=strokes,
            segment=segment,
            lifecycle_events=events,
        )


def test_arbitrary_intent_key_fails_closed():
    _, strokes, segment, events = derive_first_case()
    events[0]["detail"][checkpoint_contract._INTENT_KEY_FIELD] = (
        "segment_lifecycle:000000000000000000000000"
    )
    with pytest.raises(SegmentCheckpointContractError, match="INTENT_KEY_MISMATCH"):
        derive_segment_checkpoint_state(
            outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
            candidate_direction=StrokeDirection.UP,
            source_strokes=strokes,
            segment=segment,
            lifecycle_events=events,
        )


def test_modified_producer_detail_without_recomputed_key_fails_closed():
    _, strokes, segment, events = derive_first_case()
    events[0]["detail"]["primary_sequence_id"] = "primary:tampered"
    with pytest.raises(SegmentCheckpointContractError, match="INTENT_KEY_MISMATCH"):
        derive_segment_checkpoint_state(
            outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
            candidate_direction=StrokeDirection.UP,
            source_strokes=strokes,
            segment=segment,
            lifecycle_events=events,
        )


def test_recomputed_opaque_detail_is_frozen_by_full_event_digest():
    state, strokes, segment, events = derive_first_case()
    for event in events:
        event["detail"]["primary_sequence_id"] = "primary:rehydrated-opaque"
        refresh_intent_key(event)
    fresh = derive_segment_checkpoint_state(
        outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
        candidate_direction=StrokeDirection.UP,
        source_strokes=strokes,
        segment=segment,
        lifecycle_events=events,
    )
    assert fresh.lifecycle_event_semantic_hashes != (
        state.lifecycle_event_semantic_hashes
    )
    assert fresh.state_key != state.state_key
    with pytest.raises(SegmentCheckpointContractError, match="STATE_MISMATCH"):
        validate_first_case(state, strokes, segment, events)


def test_changed_event_id_is_frozen_by_full_event_digest():
    state, strokes, segment, events = derive_first_case()
    events[0]["event_id"] = "evt_90000001"
    fresh = derive_segment_checkpoint_state(
        outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
        candidate_direction=StrokeDirection.UP,
        source_strokes=strokes,
        segment=segment,
        lifecycle_events=events,
    )
    assert fresh.lifecycle_event_semantic_hashes != state.lifecycle_event_semantic_hashes
    assert fresh.state_key != state.state_key
    with pytest.raises(SegmentCheckpointContractError, match="STATE_MISMATCH"):
        validate_first_case(state, strokes, segment, events)


def test_primary_feature_visibility_must_match_source_confirmation():
    _, strokes, segment, events = derive_first_case()
    for event in events:
        item = event["detail"]["emission_binding"]["primary_feature_visibility"][0]
        item["visible_at_bar_index"] += 1
    refresh_binding_key(events)
    with pytest.raises(SegmentCheckpointContractError, match="VISIBILITY_MISMATCH"):
        derive_segment_checkpoint_state(
            outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
            candidate_direction=StrokeDirection.UP,
            source_strokes=strokes,
            segment=segment,
            lifecycle_events=events,
        )


def test_feature_provenance_global_order_tamper_fails_closed():
    _, strokes, segment, events = derive_first_case()
    for event in events:
        items = event["detail"]["emission_binding"]["primary_feature_visibility"]
        first_ids = items[0]["source_stroke_logical_ids"]
        second_ids = items[1]["source_stroke_logical_ids"]
        items[0]["source_stroke_logical_ids"] = second_ids
        items[0]["visible_at_bar_index"] = strokes[3].confirmed_at_bar
        items[1]["source_stroke_logical_ids"] = first_ids
        items[1]["visible_at_bar_index"] = strokes[1].confirmed_at_bar
    refresh_binding_key(events)
    with pytest.raises(SegmentCheckpointContractError, match="ORDER_MISMATCH"):
        derive_segment_checkpoint_state(
            outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
            candidate_direction=StrokeDirection.UP,
            source_strokes=strokes,
            segment=segment,
            lifecycle_events=events,
        )


def test_feature_provenance_direction_tamper_fails_closed():
    _, strokes, segment, events = derive_first_case()
    for event in events:
        item = event["detail"]["emission_binding"]["primary_feature_visibility"][0]
        item["source_stroke_logical_ids"] = (strokes[0].logical_id,)
        item["visible_at_bar_index"] = strokes[0].confirmed_at_bar
    refresh_binding_key(events)
    with pytest.raises(SegmentCheckpointContractError, match="DIRECTION_MISMATCH"):
        derive_segment_checkpoint_state(
            outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
            candidate_direction=StrokeDirection.UP,
            source_strokes=strokes,
            segment=segment,
            lifecycle_events=events,
        )


def test_destruction_evidence_mismatch_fails_closed():
    _, strokes, segment, events = derive_first_case()
    forged = replace(
        segment,
        destruction_evidence_stroke_ids=["stroke_000001"],
    )
    synchronize_segment_detail(forged, events)
    with pytest.raises(SegmentCheckpointContractError, match="DESTRUCTION_PROVENANCE"):
        derive_segment_checkpoint_state(
            outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
            candidate_direction=StrokeDirection.UP,
            source_strokes=strokes,
            segment=forged,
            lifecycle_events=events,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("endpoint_id", "fx:foreign"),
        ("bar_index", 999),
        ("price", 999.0),
    ],
)
def test_endpoint_identity_geometry_tamper_fails_closed(field, value):
    _, strokes, segment, events = derive_first_case()
    for event in events:
        event["detail"]["endpoint"][field] = value
        refresh_intent_key(event)
    with pytest.raises(SegmentCheckpointContractError, match="ENDPOINT_REBINDING"):
        derive_segment_checkpoint_state(
            outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
            candidate_direction=StrokeDirection.UP,
            source_strokes=strokes,
            segment=segment,
            lifecycle_events=events,
        )


def test_json_round_trip_lifecycle_pair_remains_valid_and_exact():
    state, strokes, segment, events = derive_first_case()
    rehydrated = json.loads(json.dumps(events))
    fresh = derive_segment_checkpoint_state(
        outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
        candidate_direction=StrokeDirection.UP,
        source_strokes=strokes,
        segment=segment,
        lifecycle_events=rehydrated,
    )
    assert fresh == state
    validate_first_case(state, strokes, segment, rehydrated)


def test_state_key_helper_requires_and_hashes_every_semantic_field():
    state, _, _, _ = derive_first_case()
    fields = dict(state.__dict__)
    del fields["state_key"]
    assert checkpoint_contract._state_key_for_fields(fields) == state.state_key
    for name, value in fields.items():
        changed = deepcopy(fields)
        if isinstance(value, tuple):
            changed[name] = (*value, "tampered")
        elif isinstance(value, StrokeDirection):
            changed[name] = (
                StrokeDirection.DOWN
                if value == StrokeDirection.UP
                else StrokeDirection.UP
            )
        elif type(value) is str:
            changed[name] = f"{value}:tampered"
        elif type(value) is int:
            changed[name] = value + 1
        elif value is None:
            changed[name] = "tampered"
        else:
            raise AssertionError(f"unhandled semantic field {name}")
        assert checkpoint_contract._state_key_for_fields(changed) != state.state_key


def test_valid_outcome_and_candidate_direction_changes_change_state_key():
    up_strokes = make_strokes([0, 10, 4])
    incomplete = derive_segment_checkpoint_state(
        outcome_code="SEGMENT_FEATURE_WINDOW_INCOMPLETE",
        candidate_direction=StrokeDirection.UP,
        source_strokes=up_strokes,
        segment=None,
        lifecycle_events=[],
    )
    pending = derive_segment_checkpoint_state(
        outcome_code="SEGMENT_SECOND_CASE_PENDING",
        candidate_direction=StrokeDirection.UP,
        source_strokes=up_strokes,
        segment=None,
        lifecycle_events=[],
    )
    down = derive_segment_checkpoint_state(
        outcome_code="SEGMENT_FEATURE_WINDOW_INCOMPLETE",
        candidate_direction=StrokeDirection.DOWN,
        source_strokes=make_strokes([10, 0, 6]),
        segment=None,
        lifecycle_events=[],
    )
    assert len({incomplete.state_key, pending.state_key, down.state_key}) == 3
