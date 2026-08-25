"""Focused PR12 FullRebuild-only Segment lifecycle bridge tests."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import yaml

from chan_parser.audit.event_log import EventLog
from chan_parser.domain.lifecycle import (
    EventType,
    LifecycleEvent,
    StructureStatus,
    StrokeDirection,
)
from chan_parser.domain.raw_bar import RawBar
from chan_parser.domain.stroke import Stroke
from chan_parser.engine.full_rebuild import FullRebuildEngine
from chan_parser.engine.segment import SegmentEngine, SegmentEngineCoreError
from chan_parser.engine.segment_lifecycle_emitter import (
    SegmentLifecycleEmissionError,
    SegmentLifecycleEmitter,
)

ROOT = Path(__file__).resolve().parents[2]


def profile() -> dict:
    return yaml.safe_load(
        (ROOT / "configs/profiles/minimal_strict_v1.yaml").read_text()
    )


def raw_bars() -> list[RawBar]:
    return [
        RawBar(
            "bar_000001",
            0,
            datetime(2024, 1, 1, 9, 30),
            100.0,
            101.0,
            99.0,
            100.5,
        )
    ]


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


def prepared_engine(strokes, phase1_events=()):
    instance = FullRebuildEngine(
        profile(),
        segment_reference_enabled=True,
        segment_lifecycle_emission_enabled=True,
    )
    instance.inclusion_engine.process = lambda valid: ([], list(phase1_events))
    instance.fractal_engine.process = lambda merged, raw_count: ([], [])
    instance.stroke_engine.process = lambda fractals, merged, raw_count: (
        strokes,
        [],
    )
    return instance


def test_full_rebuild_first_case_bridge_is_ordered_and_eventlog_owned(monkeypatch):
    strokes = make_strokes([0, 10, 4, 12, 6, 11, 5])
    phase1 = LifecycleEvent(
        event_type=EventType.CREATED,
        object_type="phase1",
        object_id="phase1:1",
        logical_id="phase1:1",
        occurred_at_bar_id="bar_000001",
        reason_code="PHASE1_TEST",
    )
    instance = prepared_engine(strokes, [phase1])
    original = SegmentEngine.process_primary
    calls = []

    def counted(engine, source, **kwargs):
        calls.append(tuple(source))
        return original(engine, source, **kwargs)

    monkeypatch.setattr(SegmentEngine, "process_primary", counted)
    result = instance.process(raw_bars())

    segment_events = [
        event for event in result["events"] if event["object_type"] == "segment"
    ]
    assert len(calls) == 1
    assert len(segment_events) == 2
    assert [event["reason_code"] for event in segment_events] == [
        "SEGMENT_FIRST_CASE_CREATED",
        "SEGMENT_FIRST_CASE_CONFIRMED",
    ]
    assert all(event["event_id"] for event in segment_events)
    assert result["events"][0]["object_type"] == "phase1"
    assert result["events"][1]["object_type"] == "segment"
    assert result["audit"]["event_count"] == len(result["events"])
    assert result["audit"]["event_log_sha256"] == _event_log_digest(result["events"])


def test_bridge_requires_reference_and_closes_raw_replay():
    with pytest.raises(
        SegmentEngineCoreError,
        match="SEGMENT_LIFECYCLE_REQUIRES_REFERENCE",
    ):
        FullRebuildEngine(
            profile(),
            segment_lifecycle_emission_enabled=True,
        )

    instance = FullRebuildEngine(
        profile(),
        segment_reference_enabled=True,
        segment_lifecycle_emission_enabled=True,
    )
    with pytest.raises(
        SegmentEngineCoreError,
        match="SEGMENT_LIFECYCLE_RAW_REPLAY_NOT_INTEGRATED",
    ):
        instance.process(raw_bars(), raw_watermark=0)


@pytest.mark.parametrize("value", [None, 0, 1, "true", []])
def test_lifecycle_flag_requires_exact_bool(value):
    with pytest.raises(TypeError):
        FullRebuildEngine(profile(), segment_lifecycle_emission_enabled=value)


@pytest.mark.parametrize(
    "points, expected",
    [
        ([0, 10, 4], "SEGMENT_FEATURE_WINDOW_INCOMPLETE"),
        ([0, 3, 1, 8, 5, 7, 4], "SEGMENT_SECOND_CASE_PENDING"),
    ],
)
def test_non_first_case_emits_no_segment_events(points, expected):
    instance = prepared_engine(make_strokes(points))
    observed = []
    original = SegmentEngine.process_primary

    def capture(engine, source, **kwargs):
        result = original(engine, source, **kwargs)
        observed.append(result.reason_code)
        return result

    # This also proves the non-first-case result came from the single reference evaluation.
    import pytest as _pytest
    monkeypatch = _pytest.MonkeyPatch()
    monkeypatch.setattr(SegmentEngine, "process_primary", capture)
    try:
        result = instance.process(raw_bars())
    finally:
        monkeypatch.undo()
    assert result["events"] == []
    assert result["audit"]["event_count"] == 0
    assert result["structures"]["segments"] == []
    assert observed == [expected]


def test_emitter_failure_fails_closed(monkeypatch):
    instance = prepared_engine(make_strokes([0, 10, 4, 12, 6, 11, 5]))

    def fail(*args, **kwargs):
        raise SegmentLifecycleEmissionError("bridge failure")

    monkeypatch.setattr(SegmentLifecycleEmitter, "emit", fail)
    with pytest.raises(SegmentLifecycleEmissionError, match="bridge failure"):
        instance.process(raw_bars())


def test_emitter_reference_profile_is_an_isolated_exact_copy():
    first = SegmentLifecycleEmitter.reference_profile()
    second = SegmentLifecycleEmitter.reference_profile()
    assert first == second
    assert first["integration"]["full_rebuild_reference_integration_enabled"] is True
    first["integration"]["full_rebuild_reference_integration_enabled"] = False
    assert (
        SegmentLifecycleEmitter.reference_profile()["integration"]
        ["full_rebuild_reference_integration_enabled"]
        is True
    )


def _event_log_digest(events: list[dict]) -> str:
    log = EventLog()
    for event in events:
        log.record(LifecycleEvent(**event))
    return log.compute_sha256()
