"""Focused production integration gates for PR25."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import replace
from copy import deepcopy

import pytest
import yaml

from chan_parser.domain.lifecycle import StructureStatus, StrokeDirection
from chan_parser.domain.raw_bar import RawBar
from chan_parser.domain.stroke import Stroke
from chan_parser.engine.incremental import IncrementalEngine
from chan_parser.engine.segment import SegmentEngine
from chan_parser.engine.segment import SegmentEngineResult
from chan_parser.audit.event_log import EventLog
from chan_parser.engine.segment_lifecycle_emitter import SegmentLifecycleEmitter

ROOT = Path(__file__).resolve().parents[2]


def profile():
    return yaml.safe_load((ROOT / "configs/profiles/minimal_strict_v1.yaml").read_text())


def bars(start=0):
    when = datetime(2024, 1, 2, 9, 30) + timedelta(minutes=30 * start)
    return [RawBar(f"bar_{start + 1:06d}", start, when, 100, 102, 99, 101)]


def strokes(points):
    return [Stroke(
        object_id=f"stroke_{i:06d}_r1", logical_id=f"stroke:{i}", revision=1,
        status=StructureStatus.CONFIRMED, created_at_bar=i + 1,
        confirmed_at_bar=i + 1, rule_profile="minimal_strict_v1", rule_version="1.0.0",
        stroke_id=f"stroke_{i:06d}",
        direction=StrokeDirection.UP if a < b else StrokeDirection.DOWN,
        start_fractal_id=f"fx:{i}", end_fractal_id=f"fx:{i + 1}",
        start_price=a, end_price=b, start_bar_index=i, end_bar_index=i + 1,
        merged_bar_count=2, max_price=max(a, b), min_price=min(a, b),
        price_range=abs(b - a), confirmation_requirements=[], repaint_risk="NONE",
    ) for i, (a, b) in enumerate(zip(points, points[1:]))]


def prepared(source, *, production=False, reference=False):
    engine = IncrementalEngine(
        profile(), segment_production_enabled=production,
        segment_reference_enabled=reference,
    )
    engine.inclusion_engine.process = lambda valid: ([], [])
    engine.fractal_engine.process = lambda merged, raw_count: ([], [])
    engine.stroke_engine.process = lambda fractals, merged, raw_count: (source, [])
    return engine


def test_default_and_reference_outputs_remain_segment_free():
    source = strokes([0, 10, 4, 12, 6, 11, 5])
    assert set(prepared(source).append_batch(bars())["structures"]) == {
        "merged_bars", "fractals", "strokes"
    }
    reference = prepared(source, reference=True).append_batch(bars())
    assert "segments" not in reference["structures"]
    assert "segment_reference" in reference["audit"]


@pytest.mark.parametrize("value", [1, 0, "true", None])
def test_production_flag_requires_exact_bool(value):
    with pytest.raises(TypeError, match="segment_production_enabled must be a bool"):
        IncrementalEngine(profile(), segment_production_enabled=value)


def test_reference_and_production_modes_conflict():
    with pytest.raises(ValueError, match="SEGMENT_PRODUCTION_REFERENCE_MODE_CONFLICT"):
        IncrementalEngine(profile(), segment_reference_enabled=True,
                          segment_production_enabled=True)


def test_initial_first_case_is_owned_and_emitted_independently():
    source = strokes([0, 10, 4, 12, 6, 11, 5])
    engine = prepared(source, production=True)
    result = engine.append_batch(bars())
    assert len(engine._segments) == 1
    assert result["structures"]["segments"][0]["revision"] == 1
    assert [e["event_type"] for e in result["events"] if e["object_type"] == "segment"] == [
        "OBJECT_CREATED", "OBJECT_CONFIRMED"
    ]
    source[0].start_price = 999
    assert engine._segment_source_strokes[0].start_price != 999
    assert engine._segments[0] is not result["structures"]["segments"][0]


def test_no_previous_incomplete_is_successful_and_event_free():
    engine = prepared(strokes([0, 10, 4]), production=True)
    result = engine.append_batch(bars())
    assert engine._segments == []
    assert "segments" in result["structures"] and result["structures"]["segments"] == []
    assert not any(e["object_type"] == "segment" for e in result["events"])


def test_second_case_is_fail_closed_without_production_mutation():
    engine = prepared(strokes([0, 3, 1, 8, 5, 7, 4]), production=True)
    before = engine.get_current_state()
    with pytest.raises(ValueError, match="SEGMENT_SECOND_CASE_PENDING"):
        engine.append_batch(bars())
    after = engine.get_current_state()
    assert after["structures"] == before["structures"]
    assert engine._segments == []
    assert not any(e["object_type"] == "segment" for e in after["events"])


def test_production_checkpoint_restores_formal_segment():
    engine = prepared(strokes([0, 10, 4, 12, 6, 11, 5]), production=True)
    engine.checkpoint_interval = 0
    engine.append_batch(bars())
    checkpoint = engine.create_checkpoint()
    expected = engine._segments[0].to_dict()
    engine._segments = []
    restored = engine.resume_from_checkpoint(checkpoint)
    assert restored["structures"]["segments"] == [expected]


def test_rn_lifecycle_history_is_scoped_to_exact_object_revision():
    source = strokes([0, 10, 4, 12, 6, 11, 5])
    current = SegmentEngine(SegmentEngine.reference_profile()).process_primary(
        source, sequence_id="incremental:primary"
    )
    log = EventLog()
    emitter = SegmentLifecycleEmitter(SegmentLifecycleEmitter.production_profile())
    emitter.emit(result=current, source_strokes=source, event_log=log)
    r2 = replace(current.segment, revision=2,
                 object_id=f"{current.segment.segment_id}_r2")
    emitter.emit(result=replace(current, segment=r2), source_strokes=source, event_log=log)
    before = len(log)
    emitter.emit(result=replace(current, segment=r2), source_strokes=source, event_log=log)
    assert len(log) == before
    assert [e["object_id"] for e in log.to_list() if e["object_type"] == "segment"] == [
        current.segment.object_id, current.segment.object_id, r2.object_id, r2.object_id
    ]


def test_empty_current_source_with_previous_fails_closed_and_rolls_back():
    source = strokes([0, 10, 4, 12, 6, 11, 5])
    engine = prepared(source, production=True)
    engine.checkpoint_interval = 0
    engine.append_batch(bars())
    checkpoint = engine.create_checkpoint()
    before = engine.get_current_state()
    engine.stroke_engine.process = lambda fractals, merged, raw_count: ([], [])
    with pytest.raises(ValueError, match="SEGMENT_SOURCE_EMPTY_WITH_PREVIOUS"):
        engine.append_batch(bars(1))
    assert engine.get_current_state() == before
    assert engine._checkpoints[checkpoint].segments[0].to_dict() == engine._segments[0].to_dict()


def test_checkpoint_rejects_extra_segments_before_live_mutation():
    source = strokes([0, 10, 4, 12, 6, 11, 5])
    engine = prepared(source, production=True)
    engine.checkpoint_interval = 0
    engine.append_batch(bars())
    checkpoint = engine.create_checkpoint()
    before = engine.get_current_state()
    engine._checkpoints[checkpoint].segments.append(deepcopy(engine._segments[0]))
    with pytest.raises(ValueError, match="SEGMENT_CHECKPOINT_SINGLETON_INVALID"):
        engine.resume_from_checkpoint(checkpoint)
    assert engine.get_current_state() == before


def test_production_profile_enables_only_incremental_integration():
    profile = SegmentLifecycleEmitter.production_profile()
    assert profile["integration"] == {
        "full_rebuild_reference_integration_enabled": True,
        "parser_integration_enabled": False,
        "checkpoint_integration_enabled": True,
        "bounded_tail_integration_enabled": False,
        "full_incremental_integration_enabled": True,
        "second_case_confirmation_enabled": False,
        "center_or_zhongshu_enabled": False,
    }
    assert SegmentLifecycleEmitter.reference_profile()["integration"][
        "full_incremental_integration_enabled"
    ] is False


def _engine_sequence(sources, results):
    engine = prepared(sources[0], production=True)
    engine.checkpoint_interval = 0
    current = {"source": sources[0]}
    engine.stroke_engine.process = lambda fractals, merged, raw_count: (current["source"], [])
    calls = iter(results)
    original = SegmentEngine.process_primary
    SegmentEngine.process_primary = lambda self, source, **kwargs: next(calls)
    return engine, current, original


def test_incremental_reuse_has_zero_segment_lifecycle_delta():
    source = strokes([0, 10, 4, 12, 6, 11, 5])
    canonical = SegmentEngine(SegmentEngine.reference_profile()).process_primary(
        source, sequence_id="incremental:primary"
    )
    engine, _, original = _engine_sequence([source], [canonical, canonical])
    try:
        engine.append_batch(bars(0))
        before = len([e for e in engine.get_current_state()["events"] if e["object_type"] == "segment"])
        state = engine.append_batch(bars(1))
    finally:
        SegmentEngine.process_primary = original
    after = len([e for e in state["events"] if e["object_type"] == "segment"])
    assert after == before
    assert state["structures"]["segments"][0]["revision"] == 1


def test_incremental_revise_then_reuse_uses_rn_materialization():
    source1 = strokes([0, 10, 4, 12, 6, 11, 5])
    source2 = deepcopy(source1)
    for stroke in source2:
        stroke.start_price += 1
        stroke.end_price += 1
        stroke.max_price += 1
        stroke.min_price += 1
    first = SegmentEngine(SegmentEngine.reference_profile()).process_primary(source1, sequence_id="incremental:primary")
    second = SegmentEngine(SegmentEngine.reference_profile()).process_primary(source2, sequence_id="incremental:primary")
    engine, current, original = _engine_sequence([source1, source2], [first, second, second])
    try:
        engine.append_batch(bars(0))
        current["source"] = source2
        revised = engine.append_batch(bars(1))
        before = len([e for e in revised["events"] if e["object_type"] == "segment"])
        reused = engine.append_batch(bars(2))
    finally:
        SegmentEngine.process_primary = original
    segment = reused["structures"]["segments"][0]
    delta = [e for e in reused["events"] if e["object_type"] == "segment"][before:]
    assert segment["logical_id"] == first.segment.logical_id
    assert segment["revision"] == 2
    assert segment["object_id"] == f"{second.segment.segment_id}_r2"
    assert [(e["event_type"], e["object_id"], e["replaced_by"]) for e in
            revised["events"] if e["object_type"] == "segment"][-3:] == [
        ("OBJECT_CREATED", segment["object_id"], None),
        ("OBJECT_CONFIRMED", segment["object_id"], None),
        ("STRUCTURE_REPLACED", first.segment.object_id, segment["object_id"]),
    ]
    assert delta == []


def test_incremental_replace_required_uses_pr24_materializer():
    source1 = strokes([0, 10, 4, 12, 6, 11, 5])
    source2 = deepcopy(source1)
    for i, stroke in enumerate(source2):
        stroke.logical_id = f"other:{i}"
        stroke.start_bar_index += 10
        stroke.end_bar_index += 10
        stroke.created_at_bar += 10
        stroke.confirmed_at_bar += 10
    first = SegmentEngine(SegmentEngine.reference_profile()).process_primary(source1, sequence_id="incremental:primary")
    second = SegmentEngine(SegmentEngine.reference_profile()).process_primary(source2, sequence_id="incremental:primary")
    engine, current, original = _engine_sequence([source1, source2], [first, second])
    try:
        engine.append_batch(bars(0))
        current["source"] = source2
        state = engine.append_batch(bars(1))
    finally:
        SegmentEngine.process_primary = original
    events = [e for e in state["events"] if e["object_type"] == "segment"][-3:]
    assert state["structures"]["segments"][0]["logical_id"] != first.segment.logical_id
    assert state["structures"]["segments"][0]["revision"] == 1
    assert [e["event_type"] for e in events] == ["OBJECT_CREATED", "OBJECT_CONFIRMED", "STRUCTURE_REPLACED"]
    assert events[-1]["reason_code"] == "SEGMENT_RECONCILIATION_LOGICAL_ID_CHANGED"


@pytest.mark.parametrize("reason", ["SEGMENT_FEATURE_WINDOW_INCOMPLETE", "SEGMENT_PRIMARY_FRACTAL_NOT_FOUND"])
def test_incremental_transient_preserves_previous_formal_state(reason):
    source = strokes([0, 10, 4, 12, 6, 11, 5])
    first = SegmentEngine(SegmentEngine.reference_profile()).process_primary(source, sequence_id="incremental:primary")
    transient = SegmentEngineResult(reason, source[0].direction, ())
    engine, _, original = _engine_sequence([source], [first, transient])
    try:
        engine.append_batch(bars(0))
        before = deepcopy(engine._segments[0].to_dict())
        state = engine.append_batch(bars(1))
    finally:
        SegmentEngine.process_primary = original
    assert engine._segments[0].to_dict() == before
    assert not [e for e in state["events"] if e["object_type"] == "segment"][2:]


def test_incremental_transient_broken_rolls_back_all_append_state():
    source = strokes([0, 10, 4, 12, 6, 11, 5])
    first = SegmentEngine(SegmentEngine.reference_profile()).process_primary(source, sequence_id="incremental:primary")
    transient = SegmentEngineResult("SEGMENT_FEATURE_WINDOW_INCOMPLETE", source[0].direction, ())
    broken_source = deepcopy(source)
    broken_source[0].logical_id = "broken:0"
    engine, current, original = _engine_sequence([source, broken_source], [first, transient])
    try:
        engine.append_batch(bars(0))
        before = (engine.get_current_state(), deepcopy(engine._checkpoints), engine._next_checkpoint_id)
        current["source"] = broken_source
        with pytest.raises(ValueError, match="SEGMENT_TRANSIENT_SOURCE_CONTINUITY_BROKEN"):
            engine.append_batch(bars(1))
    finally:
        SegmentEngine.process_primary = original
    assert engine.get_current_state() == before[0]
    assert engine._checkpoints == before[1]
    assert engine._next_checkpoint_id == before[2]


def test_second_case_with_previous_rolls_back_and_emits_nothing():
    source = strokes([0, 10, 4, 12, 6, 11, 5])
    first = SegmentEngine(SegmentEngine.reference_profile()).process_primary(source, sequence_id="incremental:primary")
    pending = SegmentEngineResult("SEGMENT_SECOND_CASE_PENDING", source[0].direction, (), pending_second_case=object())
    engine, _, original = _engine_sequence([source], [first, pending])
    try:
        engine.append_batch(bars(0))
        before = engine.get_current_state()
        with pytest.raises(ValueError, match="SEGMENT_SECOND_CASE_PENDING"):
            engine.append_batch(bars(1))
    finally:
        SegmentEngine.process_primary = original
    assert engine.get_current_state() == before


def test_lifecycle_sentinel_identity_is_preserved_by_append_rollback():
    source = strokes([0, 10, 4, 12, 6, 11, 5])
    engine = prepared(source, production=True)
    sentinel = RuntimeError("sentinel")
    engine._segment_lifecycle_emitter.emit = lambda **kwargs: (_ for _ in ()).throw(sentinel)
    before = engine.get_current_state()
    with pytest.raises(RuntimeError) as raised:
        engine.append_batch(bars(0))
    assert raised.value is sentinel
    assert engine.get_current_state() == before


def test_production_rn_checkpoint_uses_versioned_profile():
    source1 = strokes([0, 10, 4, 12, 6, 11, 5])
    source2 = deepcopy(source1)
    for stroke in source2:
        stroke.start_price += 1; stroke.end_price += 1
        stroke.max_price += 1; stroke.min_price += 1
    first = SegmentEngine(SegmentEngine.reference_profile()).process_primary(source1, sequence_id="incremental:primary")
    second = SegmentEngine(SegmentEngine.reference_profile()).process_primary(source2, sequence_id="incremental:primary")
    engine, current, original = _engine_sequence([source1, source2], [first, second])
    try:
        engine.append_batch(bars(0)); current["source"] = source2; engine.append_batch(bars(1))
    finally:
        SegmentEngine.process_primary = original
    checkpoint = engine.create_checkpoint()
    expected = deepcopy(engine._segments[0].to_dict())
    engine._segments = []
    restored = engine.resume_from_checkpoint(checkpoint)
    assert restored["structures"]["segments"] == [expected]


def test_production_replacement_r1_checkpoint_restores_without_duplicate_events():
    source1 = strokes([0, 10, 4, 12, 6, 11, 5])
    source2 = deepcopy(source1)
    for i, stroke in enumerate(source2):
        stroke.logical_id = f"other:{i}"
        stroke.start_bar_index += 10; stroke.end_bar_index += 10
        stroke.created_at_bar += 10; stroke.confirmed_at_bar += 10
    first = SegmentEngine(SegmentEngine.reference_profile()).process_primary(source1, sequence_id="incremental:primary")
    second = SegmentEngine(SegmentEngine.reference_profile()).process_primary(source2, sequence_id="incremental:primary")
    engine, current, original = _engine_sequence([source1, source2], [first, second])
    try:
        engine.append_batch(bars(0)); current["source"] = source2; engine.append_batch(bars(1))
    finally:
        SegmentEngine.process_primary = original
    checkpoint = engine.create_checkpoint()
    expected = deepcopy(engine._segments[0].to_dict())
    before_events = [e for e in engine.get_current_state()["events"] if e["object_type"] == "segment"]
    engine._segments = []
    restored = engine.resume_from_checkpoint(checkpoint)
    assert restored["structures"]["segments"] == [expected]
    assert [e for e in restored["events"] if e["object_type"] == "segment"] == before_events


def test_checkpoint_source_and_semantic_tamper_fail_before_live_mutation():
    engine = prepared(strokes([0, 10, 4, 12, 6, 11, 5]), production=True)
    engine.checkpoint_interval = 0
    engine.append_batch(bars(0))
    checkpoint = engine.create_checkpoint()
    before = engine.get_current_state()
    cp = engine._checkpoints[checkpoint]
    cp.segment_source_strokes = (replace(cp.segment_source_strokes[0], start_price=999),) + cp.segment_source_strokes[1:]
    with pytest.raises(ValueError):
        engine.resume_from_checkpoint(checkpoint)
    assert engine.get_current_state() == before

    checkpoint = engine.create_checkpoint()
    before = engine.get_current_state()
    cp = engine._checkpoints[checkpoint]
    cp.segment_checkpoint_state = replace(cp.segment_checkpoint_state, state_key="tampered")
    with pytest.raises(ValueError):
        engine.resume_from_checkpoint(checkpoint)
    assert engine.get_current_state() == before


def test_checkpoint_semantic_state_requires_exactly_one_segment():
    engine = prepared(strokes([0, 10, 4, 12, 6, 11, 5]), production=True)
    engine.checkpoint_interval = 0
    engine.append_batch(bars(0))
    checkpoint = engine.create_checkpoint()
    cp = engine._checkpoints[checkpoint]
    cp.segments = []
    with pytest.raises(ValueError, match="SEGMENT_CHECKPOINT_STATE_MISSING"):
        engine.resume_from_checkpoint(checkpoint)


def test_no_previous_rejects_non_r1_candidate():
    source = strokes([0, 10, 4, 12, 6, 11, 5])
    canonical = SegmentEngine(SegmentEngine.reference_profile()).process_primary(
        source, sequence_id="incremental:primary"
    )
    forged = replace(canonical.segment, revision=2,
                     object_id=f"{canonical.segment.segment_id}_r2")
    engine, _, original = _engine_sequence([source], [replace(canonical, segment=forged)])
    try:
        with pytest.raises(ValueError, match="SEGMENT_FIRST_CASE_CANDIDATE_R1_REQUIRED"):
            engine.append_batch(bars(0))
    finally:
        SegmentEngine.process_primary = original
    assert engine._segments == []
