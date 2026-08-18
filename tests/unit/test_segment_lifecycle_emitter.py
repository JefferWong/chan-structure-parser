"""Unit tests for isolated Phase 2 Stage B Segment lifecycle emission."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import inspect
from pathlib import Path

import pytest
import yaml

from chan_parser.audit.event_log import EventLog
from chan_parser.domain.lifecycle import EventType, LifecycleEvent, StructureStatus, StrokeDirection
from chan_parser.domain.stroke import Stroke
from chan_parser.engine.segment import SegmentEngine, SegmentEngineResult
from chan_parser.engine.segment_lifecycle_emitter import (
    SegmentLifecycleEmissionError,
    SegmentLifecycleEmitter,
)


ROOT = Path(__file__).resolve().parents[2]
EMISSION_PROFILE = ROOT / "configs/profiles/minimal_segment_lifecycle_emission_v1.yaml"
ENGINE_PROFILE = ROOT / "configs/profiles/minimal_segment_engine_core_v1.yaml"


def emission_profile() -> dict:
    return yaml.safe_load(EMISSION_PROFILE.read_text(encoding="utf-8"))


def segment_profile() -> dict:
    return yaml.safe_load(ENGINE_PROFILE.read_text(encoding="utf-8"))


def make_strokes(
    points: list[float],
    *,
    visibility_overrides: dict[int, int] | None = None,
) -> list[Stroke]:
    visibility_overrides = visibility_overrides or {}
    strokes: list[Stroke] = []
    for index, (start, end) in enumerate(zip(points, points[1:])):
        direction = StrokeDirection.UP if start < end else StrokeDirection.DOWN
        strokes.append(Stroke(
            object_id=f"stroke_{index:06d}_r1",
            logical_id=f"stroke:{index}",
            revision=1,
            status=StructureStatus.CONFIRMED,
            created_at_bar=index + 1,
            confirmed_at_bar=visibility_overrides.get(index, index + 1),
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
        ))
    return strokes


def emitter() -> SegmentLifecycleEmitter:
    return SegmentLifecycleEmitter(emission_profile())


def first_case(
    direction: StrokeDirection = StrokeDirection.UP,
    *,
    visibility_overrides: dict[int, int] | None = None,
) -> tuple[list[Stroke], SegmentEngineResult]:
    points = (
        [0, 10, 4, 12, 6, 11, 5]
        if direction == StrokeDirection.UP
        else [12, 2, 8, 0, 6, 1, 7]
    )
    strokes = make_strokes(points, visibility_overrides=visibility_overrides)
    result = SegmentEngine(segment_profile()).process_primary(
        strokes,
        sequence_id=f"primary:{direction.value.lower()}",
    )
    assert result.reason_code == "SEGMENT_FIRST_CASE_CONFIRMED"
    return strokes, result


def other_first_case() -> tuple[list[Stroke], SegmentEngineResult]:
    strokes = make_strokes([0, 10, 4, 12, 6, 11, 5])
    shifted: list[Stroke] = []
    for index, stroke in enumerate(strokes):
        assert stroke.confirmed_at_bar is not None
        shifted.append(replace(
            stroke,
            object_id=f"other_stroke_{index:06d}_r1",
            logical_id=f"other-stroke:{index}",
            stroke_id=f"other_stroke_{index:06d}",
            start_fractal_id=f"other-fx:{index}",
            end_fractal_id=f"other-fx:{index + 1}",
            created_at_bar=stroke.created_at_bar + 10,
            confirmed_at_bar=stroke.confirmed_at_bar + 10,
            start_bar_index=stroke.start_bar_index + 10,
            end_bar_index=stroke.end_bar_index + 10,
        ))
    result = SegmentEngine(segment_profile()).process_primary(
        shifted,
        sequence_id="primary:other",
    )
    assert result.reason_code == "SEGMENT_FIRST_CASE_CONFIRMED"
    return shifted, result


class CapturingEventLog(EventLog):
    def __init__(self):
        super().__init__()
        self.input_event_ids: list[str] = []

    def record(self, event: LifecycleEvent) -> LifecycleEvent:
        self.input_event_ids.append(event.event_id)
        return super().record(event)


class FailOnSecondRecordEventLog(EventLog):
    def __init__(self):
        super().__init__()
        self.record_attempts = 0

    def record(self, event: LifecycleEvent) -> LifecycleEvent:
        self.record_attempts += 1
        if self.record_attempts == 2:
            raise RuntimeError("injected second record failure")
        return super().record(event)


def emitted_history(
    strokes: list[Stroke],
    result: SegmentEngineResult,
) -> list[LifecycleEvent]:
    log = EventLog()
    emitter().emit(result=result, source_strokes=strokes, event_log=log)
    assert result.segment is not None
    return log.get_object_lifecycle(result.segment.logical_id)


def test_up_first_case_emits_created_then_confirmed_with_binding():
    strokes, result = first_case()
    log = CapturingEventLog()
    events = emitter().emit(result=result, source_strokes=strokes, event_log=log)

    assert tuple(event.event_type for event in events) == (
        EventType.CREATED,
        EventType.CONFIRMED,
    )
    assert tuple(event.event_id for event in events) == (
        "evt_00000001",
        "evt_00000002",
    )
    assert log.input_event_ids == ["", ""]
    assert len(log) == 2
    assert len({event.occurred_at_bar_id for event in events}) == 1
    assert tuple(event.reason_code for event in events) == (
        "SEGMENT_FIRST_CASE_CREATED",
        "SEGMENT_FIRST_CASE_CONFIRMED",
    )
    intent_keys = tuple(
        event.detail["segment_lifecycle_intent_key"] for event in events
    )
    assert intent_keys[0] != intent_keys[1]
    binding_keys = {
        event.detail["segment_lifecycle_binding_key"] for event in events
    }
    assert len(binding_keys) == 1
    assert len(binding_keys.pop()) == 64
    assert events[0].detail["emission_binding"] == events[1].detail["emission_binding"]
    binding = events[0].detail["emission_binding"]
    assert binding["source_stroke_ids"] == tuple(stroke.stroke_id for stroke in strokes)
    assert len(binding["primary_feature_visibility"]) == 3
    assert binding["confirmation_bar"] == result.segment.confirmed_at_bar


def test_down_first_case_emits_the_same_canonical_lifecycle_shape():
    strokes, result = first_case(StrokeDirection.DOWN)
    events = emitter().emit(
        result=result,
        source_strokes=strokes,
        event_log=EventLog(),
    )
    assert tuple(event.event_type for event in events) == (
        EventType.CREATED,
        EventType.CONFIRMED,
    )
    assert result.segment is not None
    assert events[0].object_id == result.segment.object_id
    assert events[0].logical_id == result.segment.logical_id


def test_repeated_exact_emit_is_idempotent():
    strokes, result = first_case()
    log = EventLog()
    instance = emitter()
    first = instance.emit(result=result, source_strokes=strokes, event_log=log)
    before = log.to_list()
    second = instance.emit(result=result, source_strokes=strokes, event_log=log)
    assert len(first) == 2
    assert second == ()
    assert log.to_list() == before


def test_created_only_history_recovers_confirmed_only():
    strokes, result = first_case()
    created = emitted_history(strokes, result)[0]
    created.event_id = ""
    log = EventLog()
    log.record(created)
    events = emitter().emit(result=result, source_strokes=strokes, event_log=log)
    assert len(events) == 1
    assert events[0].event_type == EventType.CONFIRMED
    assert events[0].event_id == "evt_00000002"
    assert len(log) == 2


def test_second_record_failure_restores_log_and_next_sequence():
    strokes, result = first_case()
    log = FailOnSecondRecordEventLog()
    before = log.to_list()
    before_length = len(log)

    with pytest.raises(
        SegmentLifecycleEmissionError,
        match="EMISSION_RECORD_FAILED",
    ):
        emitter().emit(result=result, source_strokes=strokes, event_log=log)

    assert log.to_list() == before
    assert len(log) == before_length
    probe = log.record(LifecycleEvent(object_type="probe", object_id="probe:1"))
    assert probe.event_id == "evt_00000001"


def test_created_only_recovery_records_one_event_without_atomic_failure():
    strokes, result = first_case()
    created = emitted_history(strokes, result)[0]
    created.event_id = ""
    seed_log = EventLog()
    seed_log.record(created)

    log = FailOnSecondRecordEventLog()
    log.restore(seed_log.snapshot())
    events = emitter().emit(result=result, source_strokes=strokes, event_log=log)

    assert len(events) == 1
    assert events[0].event_type == EventType.CONFIRMED
    assert events[0].event_id == "evt_00000002"
    assert len(log) == 2


def test_confirmed_only_history_fails_closed_without_mutation():
    strokes, result = first_case()
    confirmed = emitted_history(strokes, result)[1]
    confirmed.event_id = ""
    log = EventLog()
    log.record(confirmed)
    before = log.to_list()
    with pytest.raises(
        SegmentLifecycleEmissionError,
        match="NOT_CANONICAL_PREFIX",
    ):
        emitter().emit(result=result, source_strokes=strokes, event_log=log)
    assert log.to_list() == before


def test_duplicate_intent_key_history_fails_closed():
    strokes, result = first_case()
    created = emitted_history(strokes, result)[0]
    log = EventLog()
    for _ in range(2):
        duplicate = deepcopy(created)
        duplicate.event_id = ""
        log.record(duplicate)
    before = log.to_list()
    with pytest.raises(SegmentLifecycleEmissionError, match="DUPLICATE"):
        emitter().emit(result=result, source_strokes=strokes, event_log=log)
    assert log.to_list() == before


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ("binding", "CONFLICT:detail"),
        ("object_id", "CONFLICT:object_id"),
        ("unknown_key", "INTENT_KEY_UNKNOWN"),
    ],
)
def test_conflicting_relevant_history_fails_closed(mutation, match):
    strokes, result = first_case()
    created = emitted_history(strokes, result)[0]
    if mutation == "binding":
        created.detail["segment_lifecycle_binding_key"] = "0" * 64
    elif mutation == "object_id":
        created.object_id = "segment:conflicting-object"
    else:
        created.detail["segment_lifecycle_intent_key"] = "segment_lifecycle:unknown"
    created.event_id = ""
    log = EventLog()
    log.record(created)
    before = log.to_list()
    with pytest.raises(SegmentLifecycleEmissionError, match=match):
        emitter().emit(result=result, source_strokes=strokes, event_log=log)
    assert log.to_list() == before


def test_same_object_wrong_logical_id_history_fails_closed():
    strokes, result = first_case()
    created = emitted_history(strokes, result)[0]
    created.logical_id = "segment:wrong-logical"
    created.event_id = ""
    log = EventLog()
    log.record(created)
    before = log.to_list()

    with pytest.raises(
        SegmentLifecycleEmissionError,
        match="CONFLICT:logical_id",
    ):
        emitter().emit(result=result, source_strokes=strokes, event_log=log)

    assert log.to_list() == before


def test_same_intent_key_wrong_both_identities_fails_closed():
    strokes, result = first_case()
    created = emitted_history(strokes, result)[0]
    created.logical_id = "segment:wrong-logical"
    created.object_id = "segment_wrong_object"
    created.event_id = ""
    log = EventLog()
    log.record(created)
    before = log.to_list()

    with pytest.raises(
        SegmentLifecycleEmissionError,
        match="CONFLICT:object_id",
    ):
        emitter().emit(result=result, source_strokes=strokes, event_log=log)

    assert log.to_list() == before


def test_canonical_intent_key_wrong_reason_and_identities_fails_closed():
    strokes, result = first_case()
    created = emitted_history(strokes, result)[0]
    created.reason_code = "TAMPERED_REASON"
    created.logical_id = "segment:wrong"
    created.object_id = "segment_wrong"
    created.event_id = ""
    log = EventLog()
    log.record(created)
    before = log.to_list()

    with pytest.raises(
        SegmentLifecycleEmissionError,
        match="CONFLICT:object_id",
    ):
        emitter().emit(result=result, source_strokes=strokes, event_log=log)

    assert log.to_list() == before


def test_current_identity_stage_b_marker_wrong_reason_fails_closed():
    strokes, result = first_case()
    created = emitted_history(strokes, result)[0]
    created.reason_code = "TAMPERED_REASON"
    created.detail["segment_lifecycle_intent_key"] = (
        "segment_lifecycle:tampered"
    )
    created.event_id = ""
    log = EventLog()
    log.record(created)
    before = log.to_list()

    with pytest.raises(
        SegmentLifecycleEmissionError,
        match="INTENT_KEY_UNKNOWN",
    ):
        emitter().emit(result=result, source_strokes=strokes, event_log=log)

    assert log.to_list() == before


def test_truly_unrelated_non_stage_b_event_is_allowed():
    strokes, result = first_case()
    log = EventLog()
    log.record(LifecycleEvent(
        event_type="OTHER_EVENT",
        object_type="other",
        object_id="other_object",
        logical_id="other:logical",
        reason_code="UNRELATED_REASON",
        detail={"note": "not a Segment lifecycle event"},
    ))
    before = log.to_list()

    events = emitter().emit(
        result=result,
        source_strokes=strokes,
        event_log=log,
    )

    assert tuple(event.event_type for event in events) == (
        EventType.CREATED,
        EventType.CONFIRMED,
    )
    assert log.to_list()[:1] == before
    assert len(log) == 3


def test_unrelated_other_segment_stage_b_history_is_allowed():
    strokes, result = first_case()
    other_strokes, other_result = other_first_case()
    current_created = emitted_history(strokes, result)[0]
    other_created = emitted_history(other_strokes, other_result)[0]
    assert other_created.logical_id != current_created.logical_id
    assert other_created.object_id != current_created.object_id
    assert (
        other_created.detail["segment_lifecycle_intent_key"]
        != current_created.detail["segment_lifecycle_intent_key"]
    )

    other_created.event_id = ""
    log = EventLog()
    log.record(other_created)
    before = log.to_list()
    events = emitter().emit(
        result=result,
        source_strokes=strokes,
        event_log=log,
    )

    assert tuple(event.event_type for event in events) == (
        EventType.CREATED,
        EventType.CONFIRMED,
    )
    assert log.to_list()[:1] == before
    assert len(log) == 3


def test_same_geometry_with_wrong_segment_provenance_fails_closed():
    strokes, result = first_case()
    assert result.segment is not None
    tampered = replace(result.segment, logical_id="segment:wrong->provenance")
    with pytest.raises(SegmentLifecycleEmissionError, match="logical_id"):
        emitter().emit(
            result=replace(result, segment=tampered),
            source_strokes=strokes,
            event_log=EventLog(),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("segment_id", "segment_999999_999999_U"),
        ("object_id", "segment_000001_000004_U_r99"),
        ("revision", 2),
    ],
)
def test_forged_segment_identity_fails_closed(field, value):
    strokes, result = first_case()
    assert result.segment is not None
    tampered = replace(result.segment, **{field: value})
    with pytest.raises(
        SegmentLifecycleEmissionError,
        match=f"SEGMENT_IDENTITY_MISMATCH:{field}",
    ):
        emitter().emit(
            result=replace(result, segment=tampered),
            source_strokes=strokes,
            event_log=EventLog(),
        )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("stroke_ids", ["stroke_000000", "stroke_000002"], "stroke_ids"),
        ("feature_sequence_stroke_ids", ["stroke_000001"], "FEATURE_PROVENANCE"),
        ("destruction_evidence_stroke_ids", ["stroke_000005"], "DESTRUCTION_PROVENANCE"),
    ],
)
def test_tampered_segment_ordered_provenance_fails_closed(field, value, match):
    strokes, result = first_case()
    assert result.segment is not None
    tampered = replace(result.segment, **{field: value})
    with pytest.raises(SegmentLifecycleEmissionError, match=match):
        emitter().emit(
            result=replace(result, segment=tampered),
            source_strokes=strokes,
            event_log=EventLog(),
        )


def test_missing_primary_feature_element_fails_closed():
    strokes, result = first_case()
    assert result.primary_evidence is not None
    missing_id = result.primary_evidence.primary_element_logical_ids[1]
    elements = tuple(
        item for item in result.feature_elements if item.logical_id != missing_id
    )
    with pytest.raises(SegmentLifecycleEmissionError, match="MATCH_COUNT"):
        emitter().emit(
            result=replace(result, feature_elements=elements),
            source_strokes=strokes,
            event_log=EventLog(),
        )


def test_duplicate_primary_feature_element_mapping_fails_closed():
    strokes, result = first_case()
    assert result.primary_evidence is not None
    target_id = result.primary_evidence.primary_element_logical_ids[0]
    duplicate = next(
        item for item in result.feature_elements if item.logical_id == target_id
    )
    with pytest.raises(SegmentLifecycleEmissionError, match="MATCH_COUNT"):
        emitter().emit(
            result=replace(
                result,
                feature_elements=result.feature_elements + (duplicate,),
            ),
            source_strokes=strokes,
            event_log=EventLog(),
        )


def test_tampered_full_feature_visibility_fails_closed():
    strokes, result = first_case()
    assert result.primary_evidence is not None
    target_id = result.primary_evidence.primary_element_logical_ids[0]
    elements = tuple(
        replace(item, visible_at_bar_index=item.visible_at_bar_index + 20)
        if item.logical_id == target_id
        else item
        for item in result.feature_elements
    )
    with pytest.raises(SegmentLifecycleEmissionError, match="FEATURE_VISIBILITY"):
        emitter().emit(
            result=replace(result, feature_elements=elements),
            source_strokes=strokes,
            event_log=EventLog(),
        )


def test_feature_visibility_must_be_authenticated_from_source_strokes():
    strokes, result = first_case(visibility_overrides={3: 9})
    assert result.segment is not None
    assert result.primary_evidence is not None
    assert result.segment.confirmed_at_bar == 9

    target_id = next(
        item.logical_id
        for item in result.feature_elements
        if "stroke:3" in item.interval.source_stroke_logical_ids
    )
    elements = tuple(
        replace(item, visible_at_bar_index=6)
        if item.logical_id == target_id
        else item
        for item in result.feature_elements
    )
    self_consistent_but_forged_segment = replace(
        result.segment,
        created_at_bar=6,
        confirmed_at_bar=6,
    )

    with pytest.raises(
        SegmentLifecycleEmissionError,
        match="FEATURE_VISIBILITY_SOURCE_MISMATCH",
    ):
        emitter().emit(
            result=replace(
                result,
                feature_elements=elements,
                segment=self_consistent_but_forged_segment,
            ),
            source_strokes=strokes,
            event_log=EventLog(),
        )


@pytest.mark.parametrize("field", ["logical_id", "stroke_id"])
def test_source_identity_mismatch_fails_closed(field):
    strokes, result = first_case()
    replacement = (
        "stroke:other" if field == "logical_id" else "stroke_other"
    )
    strokes[1] = replace(strokes[1], **{field: replacement})
    with pytest.raises(SegmentLifecycleEmissionError):
        emitter().emit(result=result, source_strokes=strokes, event_log=EventLog())


def test_nonconfirmed_source_stroke_fails_closed():
    strokes, result = first_case()
    strokes[1] = replace(
        strokes[1],
        status=StructureStatus.PROVISIONAL,
        confirmed_at_bar=None,
    )
    with pytest.raises(SegmentLifecycleEmissionError, match="NOT_CONFIRMED"):
        emitter().emit(result=result, source_strokes=strokes, event_log=EventLog())


@pytest.mark.parametrize("mutation", ["nonalternating", "endpoint", "price"])
def test_nonalternating_or_discontinuous_source_fails_closed(mutation):
    strokes, result = first_case()
    if mutation == "nonalternating":
        strokes[2] = replace(
            strokes[2],
            direction=StrokeDirection.DOWN,
            start_price=12,
            end_price=6,
        )
    elif mutation == "endpoint":
        strokes[2] = replace(strokes[2], start_fractal_id="fx:other")
    else:
        strokes[2] = replace(strokes[2], start_price=11.5)
    with pytest.raises(SegmentLifecycleEmissionError):
        emitter().emit(result=result, source_strokes=strokes, event_log=EventLog())


@pytest.mark.parametrize(
    "result_factory",
    [
        lambda: SegmentEngine(segment_profile()).process_primary(
            make_strokes([0, 10, 4, 12, 6]),
            sequence_id="primary:incomplete",
        ),
        lambda: SegmentEngineResult(
            "SEGMENT_PRIMARY_FRACTAL_NOT_FOUND",
            StrokeDirection.UP,
            (),
        ),
        lambda: SegmentEngine(segment_profile()).process_primary(
            make_strokes([0, 3, 1, 8, 5, 7, 4]),
            sequence_id="primary:pending",
        ),
    ],
)
def test_zero_event_outcomes_leave_event_log_unchanged(result_factory):
    result = result_factory()
    log = EventLog()
    before = log.to_list()
    assert emitter().emit(result=result, source_strokes=(), event_log=log) == ()
    assert log.to_list() == before
    assert len(log) == 0


def test_api_accepts_only_engine_result_source_strokes_and_event_log():
    parameters = inspect.signature(SegmentLifecycleEmitter.emit).parameters
    assert tuple(parameters) == ("self", "result", "source_strokes", "event_log")
    assert all(
        parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        for name in ("result", "source_strokes", "event_log")
    )
    public_methods = {
        name
        for name, value in inspect.getmembers(
            SegmentLifecycleEmitter,
            predicate=inspect.isfunction,
        )
        if not name.startswith("_")
    }
    assert public_methods == {"emit"}


@pytest.mark.parametrize(
    "mutation",
    [
        lambda loaded: loaded.update({"status": "INTEGRATED"}),
        lambda loaded: loaded["binding"].update(
            {"caller_supplied_intents_allowed": True}
        ),
        lambda loaded: loaded["emission"].update(
            {"event_id_authority": "Emitter"}
        ),
        lambda loaded: loaded["integration"].update(
            {"parser_integration_enabled": True}
        ),
        lambda loaded: loaded.update({"unknown": False}),
    ],
)
def test_profile_mutations_fail_closed(mutation):
    loaded = emission_profile()
    mutation(loaded)
    with pytest.raises(SegmentLifecycleEmissionError):
        SegmentLifecycleEmitter(loaded)
