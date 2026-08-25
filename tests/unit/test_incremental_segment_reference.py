"""Focused PR13 Incremental reference-only Segment tests."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

from chan_parser.domain.lifecycle import StructureStatus, StrokeDirection
from chan_parser.domain.raw_bar import RawBar
from chan_parser.domain.stroke import Stroke
from chan_parser.engine.incremental import IncrementalEngine
from chan_parser.engine.segment import SegmentEngine


ROOT = Path(__file__).resolve().parents[2]


def profile() -> dict:
    return yaml.safe_load(
        (ROOT / "configs/profiles/minimal_strict_v1.yaml").read_text(
            encoding="utf-8"
        )
    )


def bars(count: int = 1, start_index: int = 0) -> list[RawBar]:
    start = datetime(2024, 1, 2, 9, 30)
    return [
        RawBar(
            f"bar_{start_index + index + 1:06d}",
            start_index + index,
            start + timedelta(minutes=30 * (start_index + index)),
            100 + start_index + index,
            102 + start_index + index,
            99 + start_index + index,
            101 + start_index + index,
        )
        for index in range(count)
    ]


def make_strokes(points: list[float]) -> list[Stroke]:
    return [
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
            direction=(
                StrokeDirection.UP if start < end else StrokeDirection.DOWN
            ),
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
        for index, (start, end) in enumerate(zip(points, points[1:]))
    ]


def prepared_engine(strokes, *, segment_reference_enabled=False) -> IncrementalEngine:
    engine = IncrementalEngine(
        profile(),
        segment_reference_enabled=segment_reference_enabled,
    )
    engine.inclusion_engine.process = lambda valid: ([], [])
    engine.fractal_engine.process = lambda merged, raw_count: ([], [])
    engine.stroke_engine.process = lambda fractals, merged, raw_count: (
        strokes,
        [],
    )
    return engine


def test_default_incremental_does_not_call_segment_engine(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("default Incremental must not call SegmentEngine")

    monkeypatch.setattr(SegmentEngine, "process_primary", fail)
    result = IncrementalEngine(profile()).append_batch(bars())
    assert set(result["structures"]) == {"merged_bars", "fractals", "strokes"}
    assert "segment_reference" not in result["audit"]


def test_reference_calls_segment_engine_once_with_confirmed_source_only(monkeypatch):
    strokes = make_strokes([0, 10, 4, 12, 6, 11, 5])
    strokes[-1] = replace(strokes[-1], status=StructureStatus.PROVISIONAL)
    captured = []
    original = SegmentEngine.process_primary

    def counted(instance, source, **kwargs):
        captured.append((tuple(source), kwargs))
        return original(instance, source, **kwargs)

    monkeypatch.setattr(SegmentEngine, "process_primary", counted)
    engine = prepared_engine(strokes, segment_reference_enabled=True)
    result = engine.append_batch(bars())

    assert len(captured) == 1
    source, kwargs = captured[0]
    assert all(stroke.status == StructureStatus.CONFIRMED for stroke in source)
    assert tuple(stroke.stroke_id for stroke in source) == tuple(
        stroke.stroke_id for stroke in strokes[:-1]
    )
    assert kwargs == {"sequence_id": "incremental:primary"}
    assert "segments" not in result["structures"]
    assert result["audit"]["event_count"] == 0
    assert result["audit"]["segment_reference"] == engine.get_segment_reference_result()


def test_reference_result_is_deterministic_and_not_production_output():
    strokes = make_strokes([0, 10, 4, 12, 6, 11, 5])
    first = prepared_engine(strokes, segment_reference_enabled=True)
    second = prepared_engine(deepcopy(strokes), segment_reference_enabled=True)

    first_state = first.append_batch(bars())
    second_state = second.append_batch(bars())

    assert first.get_segment_reference_result() == second.get_segment_reference_result()
    assert first_state["structures"] == second_state["structures"]
    assert "segments" not in first_state["structures"]


def test_reference_state_resets_when_same_engine_is_disabled_or_has_no_result():
    state = {"strokes": make_strokes([0, 10, 4, 12, 6, 11, 5])}
    engine = prepared_engine(state["strokes"], segment_reference_enabled=True)
    engine.stroke_engine.process = lambda fractals, merged, raw_count: (
        state["strokes"],
        [],
    )

    engine.append_batch(bars(start_index=0))
    assert engine.get_segment_reference_result()["reason_code"] == (
        "SEGMENT_FIRST_CASE_CONFIRMED"
    )

    engine.segment_reference_enabled = False
    state["strokes"] = []
    engine.append_batch(bars(start_index=1))
    assert engine.get_segment_reference_result() is None

    engine.segment_reference_enabled = True
    engine.append_batch(bars(start_index=2))
    current = engine.get_segment_reference_result()
    assert current["reason_code"] is None
    assert current["segment"] is None


def test_reference_state_resets_before_fail_closed_evaluation(monkeypatch):
    strokes = make_strokes([0, 10, 4, 12, 6, 11, 5])
    engine = prepared_engine(strokes, segment_reference_enabled=True)
    engine.append_batch(bars(start_index=0))
    assert engine.get_segment_reference_result()["reason_code"] == (
        "SEGMENT_FIRST_CASE_CONFIRMED"
    )

    def fail_closed(*args, **kwargs):
        raise ValueError("reference evaluation failed closed")

    monkeypatch.setattr(SegmentEngine, "process_primary", fail_closed)
    with pytest.raises(ValueError, match="reference evaluation failed closed"):
        engine.append_batch(bars(start_index=1))
    current = engine.get_segment_reference_result()
    assert current["reason_code"] is None
    assert current["segment"] is None


def test_reference_mode_does_not_call_lifecycle_or_checkpoint(monkeypatch):
    strokes = make_strokes([0, 10, 4, 12, 6, 11, 5])
    engine = prepared_engine(strokes, segment_reference_enabled=True)
    lifecycle_calls = []
    checkpoint_calls = []

    monkeypatch.setattr(
        "chan_parser.engine.segment_lifecycle_emitter.SegmentLifecycleEmitter.emit",
        lambda *args, **kwargs: lifecycle_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        "chan_parser.contracts.segment_checkpoint.derive_segment_checkpoint_state",
        lambda *args, **kwargs: checkpoint_calls.append((args, kwargs)),
    )

    result = engine.append_batch(bars())

    assert lifecycle_calls == []
    assert checkpoint_calls == []
    assert [event for event in result["events"] if event["object_type"] == "segment"] == []


def test_second_case_pending_remains_unmaterialized_and_event_free():
    strokes = make_strokes([0, 3, 1, 8, 5, 7, 4])
    engine = prepared_engine(strokes, segment_reference_enabled=True)
    result = engine.append_batch(bars())
    reference = engine.get_segment_reference_result()

    assert reference["reason_code"] == "SEGMENT_SECOND_CASE_PENDING"
    assert reference["pending_second_case"] is True
    assert reference["segment"] is None
    assert "segments" not in result["structures"]
    assert result["events"] == []


@pytest.mark.parametrize(
    "value", [1, 0, "true", "false", None, [], {}, object()]
)
def test_reference_opt_in_requires_exact_bool(value):
    with pytest.raises(TypeError, match="segment_reference_enabled must be a bool"):
        IncrementalEngine(profile(), segment_reference_enabled=value)


def test_default_behavior_is_exactly_preserved():
    data = bars()
    first = IncrementalEngine(profile()).append_batch(data)
    second = IncrementalEngine(
        profile(), segment_reference_enabled=False
    ).append_batch(data)
    assert first == second
