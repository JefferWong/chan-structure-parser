"""Focused PR13 Incremental reference-only Segment tests."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

from chan_parser.domain.lifecycle import StructureStatus
from chan_parser.domain.raw_bar import RawBar
from chan_parser.engine.incremental import IncrementalEngine
from chan_parser.engine.segment import SegmentEngine

from tests.unit.test_segment_engine import make_strokes


ROOT = Path(__file__).resolve().parents[2]


def profile() -> dict:
    return yaml.safe_load(
        (ROOT / "configs/profiles/minimal_strict_v1.yaml").read_text(
            encoding="utf-8"
        )
    )


def bars(count: int = 1) -> list[RawBar]:
    start = datetime(2024, 1, 2, 9, 30)
    return [
        RawBar(
            f"bar_{index + 1:06d}",
            index,
            start + timedelta(minutes=30 * index),
            100 + index,
            102 + index,
            99 + index,
            101 + index,
        )
        for index in range(count)
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


@pytest.mark.parametrize("value", [1, 0, "true", "false", None, []])
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
