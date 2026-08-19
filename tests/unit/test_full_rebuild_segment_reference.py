"""Focused tests for the opt-in FullRebuild Segment reference replay."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from chan_parser.domain.lifecycle import StructureStatus, StrokeDirection
from chan_parser.domain.segment import Segment
from chan_parser.domain.stroke import Stroke
from chan_parser.engine.full_rebuild import FullRebuildEngine, FullRebuildSegmentReferenceError


ROOT = Path(__file__).resolve().parents[2]


def profile(name: str) -> dict:
    return yaml.safe_load((ROOT / "configs/profiles" / name).read_text())


def stroke(index: int, confirmed: int | None, direction: StrokeDirection | None = None) -> Stroke:
    direction = direction or (StrokeDirection.UP if index % 2 == 0 else StrokeDirection.DOWN)
    return Stroke(
        object_id=f"stroke_{index}_r1", logical_id=f"stroke:{index}", revision=1,
        status=StructureStatus.CONFIRMED if confirmed is not None else StructureStatus.PROVISIONAL,
        created_at_bar=index, confirmed_at_bar=confirmed, rule_profile="minimal_strict_v1",
        rule_version="1.0.0", stroke_id=f"stroke_{index}", direction=direction,
        start_fractal_id=f"fx:{index}", end_fractal_id=f"fx:{index + 1}",
        start_price=100 + index, end_price=101 + index,
        start_bar_index=index, end_bar_index=index + 1, merged_bar_count=2,
        max_price=101 + index, min_price=100 + index, price_range=1,
        confirmation_requirements=[], repaint_risk="NONE",
    )


def engine() -> FullRebuildEngine:
    return FullRebuildEngine(
        profile("minimal_strict_v1.yaml"),
        segment_engine_profile=profile("minimal_segment_engine_core_v1.yaml"),
        segment_lifecycle_profile=profile("minimal_segment_lifecycle_emission_v1.yaml"),
    )


def test_legacy_constructor_is_opt_out_and_partial_configuration_fails():
    legacy = FullRebuildEngine(profile("minimal_strict_v1.yaml"))
    assert legacy.segment_reference_enabled is False
    assert engine().segment_reference_enabled is True
    with pytest.raises(FullRebuildSegmentReferenceError):
        FullRebuildEngine(profile("minimal_strict_v1.yaml"),
                          segment_engine_profile=profile("minimal_segment_engine_core_v1.yaml"))


def test_sequence_id_is_derived_from_candidate_start(monkeypatch):
    instance = engine()
    seen = []

    class FakeSegment:
        def process_primary(self, source, *, sequence_id):
            seen.append((tuple(x.stroke_id for x in source), sequence_id))
            return type("R", (), {"reason_code": "SEGMENT_FEATURE_WINDOW_INCOMPLETE"})()

    instance.segment_engine = FakeSegment()
    instance._replay_segments([stroke(0, 3), stroke(1, 4), stroke(2, 5)], None)
    assert seen[0][1] == "segment-primary:stroke:0"


def test_confirmed_visibility_prefix_rejects_scattered_and_regressing_rows():
    instance = engine()
    with pytest.raises(FullRebuildSegmentReferenceError):
        instance._replay_segments([stroke(0, 3), stroke(1, None), stroke(2, 5)], None)
    with pytest.raises(FullRebuildSegmentReferenceError):
        instance._replay_segments([stroke(0, 5), stroke(1, 4)], None)
    with pytest.raises(FullRebuildSegmentReferenceError):
        instance._replay_segments([replace(stroke(0, 3), status="CONFIRMED")], None)


def test_unknown_outcome_fails_closed():
    instance = engine()

    class FakeSegment:
        def process_primary(self, source, *, sequence_id):
            return type("R", (), {"reason_code": "UNKNOWN"})()

    instance.segment_engine = FakeSegment()
    with pytest.raises(FullRebuildSegmentReferenceError):
        instance._replay_segments([stroke(0, 3)], None)


def test_backfill_is_rejected_before_emission():
    instance = engine()
    class FakeSegment:
        def process_primary(self, source, *, sequence_id):
            return type("R", (), {
                "reason_code": "SEGMENT_FIRST_CASE_CONFIRMED",
                "segment": Segment(segment_id="s", object_id="o", logical_id="l",
                                    status=StructureStatus.CONFIRMED, direction=source[0].direction,
                                    end_stroke_id=source[-1].stroke_id, confirmed_at_bar=2,
                                    created_at_bar=2),
            })()
    instance.segment_engine = FakeSegment()
    with pytest.raises(FullRebuildSegmentReferenceError, match="BACKFILL"):
        instance._replay_segments([stroke(0, 3), stroke(1, 4), stroke(2, 5)], None)


def test_second_case_pending_is_terminal_and_emits_nothing():
    instance = engine()
    class FakeSegment:
        def process_primary(self, source, *, sequence_id):
            return type("R", (), {"reason_code": "SEGMENT_SECOND_CASE_PENDING"})()
    instance.segment_engine = FakeSegment()
    segments, count, reason, start, total, consumed = instance._replay_segments(
        [stroke(0, 3), stroke(1, 4), stroke(2, 5)], None
    )
    assert segments == [] and count == 0 and reason == "SEGMENT_SECOND_CASE_PENDING"
    assert start == "stroke_0" and total == 3 and consumed == 0


def test_no_confirmed_strokes_is_empty_reference_state():
    assert engine()._replay_segments([stroke(0, None)], None) == ([], 0, "", "", 0, 0)


class _TraceEngine:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def process_primary(self, source, *, sequence_id):
        self.calls.append((tuple(item.stroke_id for item in source), sequence_id))
        return self.results.pop(0)


class _TraceEmitter:
    def __init__(self):
        self.calls = []

    def emit(self, *, result, source_strokes, event_log):
        self.calls.append(tuple(item.stroke_id for item in source_strokes))
        return [object(), object()]


def _result(reason, segment=None):
    return type("Result", (), {"reason_code": reason, "segment": segment})()


def _synthetic_segment(start, end, direction, start_bar, end_bar, start_price, end_price):
    return Segment(
        segment_id=f"segment_{start}_{end}", object_id=f"segment_{start}_{end}_r1",
        logical_id=f"segment:{start}->{end}", status=StructureStatus.CONFIRMED,
        direction=direction, start_stroke_id=start, end_stroke_id=end,
        stroke_ids=[start, end], start_bar_index=start_bar, end_bar_index=end_bar,
        start_price=start_price, end_price=end_price,
        created_at_bar=6, confirmed_at_bar=6,
    )


def test_visibility_replay_retries_and_advances_boundary_plus_one_with_evidence_reuse():
    instance = engine()
    first = _synthetic_segment("stroke_0", "stroke_1", StrokeDirection.UP, 0, 2, 100, 102)
    first = replace(first, created_at_bar=5, confirmed_at_bar=5)
    second = _synthetic_segment("stroke_2", "stroke_3", StrokeDirection.DOWN, 2, 4, 102, 104)
    second = replace(second, created_at_bar=8, confirmed_at_bar=8)
    trace_engine = _TraceEngine([
        _result("SEGMENT_FIRST_CASE_CONFIRMED", first),
        _result("SEGMENT_FEATURE_WINDOW_INCOMPLETE"),
        _result("SEGMENT_FIRST_CASE_CONFIRMED", second),
        _result("SEGMENT_PRIMARY_FRACTAL_NOT_FOUND"),
    ])
    trace_emitter = _TraceEmitter()
    instance.segment_engine = trace_engine
    instance.segment_lifecycle_emitter = trace_emitter
    source = [stroke(0, 5), stroke(1, 5), stroke(2, 5), stroke(3, 5),
              stroke(4, 8), stroke(5, 8)]
    segments, event_count, reason, tail, total, consumed = instance._replay_segments(source, object())
    assert [item.segment_id for item in segments] == [first.segment_id, second.segment_id]
    assert segments[0].direction != segments[1].direction
    assert segments[0].end_bar_index == segments[1].start_bar_index
    assert segments[0].end_price == segments[1].start_price
    assert event_count == 4
    assert trace_engine.calls == [
        (("stroke_0", "stroke_1", "stroke_2", "stroke_3"), "segment-primary:stroke:0"),
        (("stroke_2", "stroke_3"), "segment-primary:stroke:2"),
        (("stroke_2", "stroke_3", "stroke_4", "stroke_5"), "segment-primary:stroke:2"),
        (("stroke_4", "stroke_5"), "segment-primary:stroke:4"),
    ]
    assert trace_emitter.calls == [
        ("stroke_0", "stroke_1", "stroke_2", "stroke_3"),
        ("stroke_2", "stroke_3", "stroke_4", "stroke_5"),
    ]
    assert consumed == 4 and total == 6 and reason == "SEGMENT_PRIMARY_FRACTAL_NOT_FOUND"
    assert tail == "stroke_4"


def test_no_fractal_retries_at_later_watermark_and_pending_is_terminal():
    instance = engine()
    seg = _synthetic_segment("stroke_0", "stroke_1", StrokeDirection.UP, 0, 2, 100, 102)
    seg = replace(seg, created_at_bar=6, confirmed_at_bar=6)
    trace = _TraceEngine([
        _result("SEGMENT_PRIMARY_FRACTAL_NOT_FOUND"),
        _result("SEGMENT_FIRST_CASE_CONFIRMED", seg),
        _result("SEGMENT_FEATURE_WINDOW_INCOMPLETE"),
    ])
    instance.segment_engine = trace
    instance.segment_lifecycle_emitter = _TraceEmitter()
    segments, count, reason, _, _, _ = instance._replay_segments(
        [stroke(0, 3), stroke(1, 3), stroke(2, 6)], object()
    )
    assert len(segments) == 1 and count == 2
    assert trace.calls[0][1] == trace.calls[1][1] == "segment-primary:stroke:0"


def test_only_emitter_profile_also_fails_closed():
    with pytest.raises(FullRebuildSegmentReferenceError):
        FullRebuildEngine(profile("minimal_strict_v1.yaml"),
                          segment_lifecycle_profile=profile("minimal_segment_lifecycle_emission_v1.yaml"))


def test_segment_content_changes_output_hash():
    first = _synthetic_segment("stroke_0", "stroke_1", StrokeDirection.UP, 0, 2, 100, 102)
    changed = replace(first, end_price=103)
    assert FullRebuildEngine._structure_hash([], [], [], [first]) != \
        FullRebuildEngine._structure_hash([], [], [], [changed])
