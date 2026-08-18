"""Unit coverage for the explicit merged/raw visibility axes."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from chan_parser.domain.fractal import Fractal
from chan_parser.domain.lifecycle import FractalType, StructureStatus
from chan_parser.domain.merged_bar import MergedBar
from chan_parser.domain.raw_bar import RawBar
from chan_parser.engine.inclusion import InclusionEngine
from chan_parser.engine.stroke import StrokeEngine


def raw(bar_id, index, open_price, high, low, close):
    return RawBar(bar_id, index, datetime(2024, 1, 1) + timedelta(minutes=index),
                  open_price, high, low, close)


def merged(index, *, visible=None):
    visible = index + 10 if visible is None else visible
    return MergedBar(
        bar_id=f"mbar_{index:06d}", bar_index=index,
        timestamp=datetime(2024, 1, 1), open=100, high=105, low=98, close=103,
        source_raw_bar_ids=[f"raw-{index}"], source_raw_bar_indices=[visible],
        visible_at_raw_bar_index=visible,
        logical_id=f"mbar:idx_{index}", status=StructureStatus.CONFIRMED,
    )


def fractal(fid, kind, index, price):
    return Fractal(
        fractal_id=fid, fractal_type=kind, merged_bar_id=f"mbar_{index:06d}",
        merged_bar_index=index, price=price,
        window_indices=[index - 1, index, index + 1],
        logical_id=f"fractal:{kind.value}:{index}",
    )


def stroke_engine():
    return StrokeEngine({
        "mode": "strict", "alternating_fractals_required": True,
        "minimum_merged_bar_count": 5, "endpoint_extreme_required": True,
        "allow_unconfirmed_tail": True,
    })


def test_nonformatted_raw_ids_are_registry_keys_and_merge_keeps_both_axes():
    bars = [
        raw("alpha", 10, 100, 105, 98, 103),
        raw("source-A", 11, 102, 104, 100, 101),
        raw("x9", 12, 106, 109, 103, 108),
    ]
    merged_bars, _ = InclusionEngine({"equal_high_low_policy": "explicit"}).process(bars)
    first = merged_bars[0]
    assert first.bar_index == 0
    assert first.source_raw_bar_ids == ["alpha", "source-A"]
    assert first.source_raw_bar_indices == [10, 11]
    assert first.visible_at_raw_bar_index == 11
    assert first.source_raw_bar_ids[-1] == "source-A"


def test_duplicate_or_unresolvable_raw_identity_fails_closed():
    duplicate = [
        raw("alpha", 10, 100, 105, 98, 103),
        raw("alpha", 11, 102, 104, 100, 101),
    ]
    with pytest.raises(ValueError):
        InclusionEngine({}).process(duplicate)
    unresolved = raw("alpha", 10, 100, 105, 98, 103)
    unresolved.source_raw_bar_ids = ["not-in-registry"]
    with pytest.raises(ValueError):
        InclusionEngine({}).process([unresolved])


def test_stroke_creation_and_confirmation_use_right_fractal_raw_visibility():
    bars = [merged(index) for index in range(20)]
    fx = [
        fractal("fx1", FractalType.BOTTOM, 1, 98),
        fractal("fx2", FractalType.TOP, 8, 108),
        fractal("fx3", FractalType.BOTTOM, 15, 95),
    ]
    strokes, _ = stroke_engine().process(fx, bars, 20)
    assert strokes[0].created_at_bar == 9
    assert strokes[0].created_at_raw_bar_index == 19
    assert strokes[0].confirmed_at_bar == 15
    assert strokes[0].confirmed_at_raw_bar_index == 26
    assert strokes[0].confirmed_at_raw_bar_index >= strokes[0].created_at_raw_bar_index


def test_raw_visibility_respects_global_merged_index_offset():
    bars = [merged(index, visible=100 + index) for index in range(10, 30)]
    fx = [
        fractal("fx1", FractalType.BOTTOM, 11, 98),
        fractal("fx2", FractalType.TOP, 18, 108),
        fractal("fx3", FractalType.BOTTOM, 25, 95),
    ]
    strokes, _ = stroke_engine().process(fx, bars, 30, bar_index_offset=10)
    assert strokes[0].created_at_raw_bar_index == 119
    assert strokes[0].confirmed_at_raw_bar_index == 126


def test_bar_index_offset_confirmation_uses_raw_visibility():
    bars = [merged(index, visible=100 + index) for index in range(10, 30)]
    confirming = fractal("fx3", FractalType.BOTTOM, 25, 95)
    strokes, _ = stroke_engine().process([
        fractal("fx1", FractalType.BOTTOM, 11, 98),
        fractal("fx2", FractalType.TOP, 18, 108),
        confirming,
    ], bars, 30, bar_index_offset=10)

    previous = strokes[0]
    assert previous.status is StructureStatus.CONFIRMED
    assert previous.confirmed_at_raw_bar_index == 126
    assert previous.confirmed_at_raw_bar_index == bars[16].visible_at_raw_bar_index
    assert previous.confirmed_at_raw_bar_index != confirming.merged_bar_index - 10
    assert previous.confirmed_at_raw_bar_index != strokes[1].end_bar_index
    assert previous.confirmed_at_bar == strokes[1].end_bar_index
    assert previous.confirmed_at_raw_bar_index >= previous.created_at_raw_bar_index


def test_invalid_merged_raw_visibility_fails_closed():
    bars = [merged(index) for index in range(20)]
    bars[9].visible_at_raw_bar_index = -1
    fx = [
        fractal("fx1", FractalType.BOTTOM, 1, 98),
        fractal("fx2", FractalType.TOP, 8, 108),
    ]
    with pytest.raises(ValueError):
        stroke_engine().process(fx, bars, 20)
