"""Focused production integration gates for PR25."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import replace

import pytest
import yaml

from chan_parser.domain.lifecycle import StructureStatus, StrokeDirection
from chan_parser.domain.raw_bar import RawBar
from chan_parser.domain.stroke import Stroke
from chan_parser.engine.incremental import IncrementalEngine
from chan_parser.engine.segment import SegmentEngine

ROOT = Path(__file__).resolve().parents[2]


def profile():
    return yaml.safe_load((ROOT / "configs/profiles/minimal_strict_v1.yaml").read_text())


def bars(start=0):
    when = datetime(2024, 1, 2, 9, 30)
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
