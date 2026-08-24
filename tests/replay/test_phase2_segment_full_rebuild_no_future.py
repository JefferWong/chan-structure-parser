"""PR11 Phase 2 raw replay and no-future contract."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from chan_parser.domain.lifecycle import StructureStatus, StrokeDirection
from chan_parser.domain.stroke import Stroke
from chan_parser.engine.full_rebuild import FullRebuildEngine
from chan_parser.engine.segment import SegmentEngine, SegmentEngineCoreError


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "configs/profiles/minimal_segment_engine_core_v1.yaml"


def make_strokes(
    points: list[float],
    *,
    visibility_overrides: dict[int, int] | None = None,
    raw_visibility_overrides: dict[int, tuple[int, int]] | None = None,
) -> list[Stroke]:
    visibility_overrides = visibility_overrides or {}
    raw_visibility_overrides = raw_visibility_overrides or {}
    strokes: list[Stroke] = []
    for index, (start, end) in enumerate(zip(points, points[1:])):
        direction = StrokeDirection.UP if start < end else StrokeDirection.DOWN
        confirmed_at = visibility_overrides.get(index, index + 1)
        raw_created, raw_confirmed = raw_visibility_overrides.get(index, (None, None))
        strokes.append(Stroke(
            object_id=f"stroke_{index:06d}_r1",
            logical_id=f"stroke:{index}",
            revision=1,
            status=StructureStatus.CONFIRMED,
            created_at_bar=index + 1,
            confirmed_at_bar=confirmed_at,
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
            created_at_raw_bar_index=raw_created,
            confirmed_at_raw_bar_index=raw_confirmed,
        ))
    return strokes


def segment_engine() -> SegmentEngine:
    return SegmentEngine(yaml.safe_load(PROFILE.read_text(encoding="utf-8")))


def phase1_profile():
    return yaml.safe_load(
        (ROOT / "configs/profiles/minimal_strict_v1.yaml").read_text()
    )


def raw_strokes():
    return make_strokes(
        [0, 10, 4, 12, 6, 11, 5, 9],
        visibility_overrides={3: 8},
        raw_visibility_overrides={
            index: (100 + index, 100 + index)
            for index in range(7)
        },
    )


def replay_segments(engine, strokes, watermark):
    segments, _ = engine._reference_segments(
        strokes,
        raw_watermark=watermark,
    )
    return segments


def test_raw_watermark_prefix_blocks_future_strokes_and_is_data_driven():
    strokes = raw_strokes()
    replay = FullRebuildEngine(
        phase1_profile(), segment_reference_enabled=True
    )
    segments_1 = replay_segments(replay, strokes[:6], 105)
    segments_2 = replay_segments(replay, strokes, 106)

    assert all(
        segment.confirmed_at_raw_bar_index <= 105
        for segment in segments_1
    )
    assert all(
        segment.confirmed_at_raw_bar_index <= 106
        for segment in segments_2
    )
    assert all(
        "stroke_000006" not in segment.stroke_ids
        for segment in segments_1
    )


def test_same_segment_structural_axis_is_unchanged_across_watermarks():
    strokes = raw_strokes()
    replay = FullRebuildEngine(
        phase1_profile(), segment_reference_enabled=True
    )
    first = replay_segments(replay, strokes[:6], 105)
    later = replay_segments(replay, strokes, 106)
    assert first and later
    left, right = first[0], later[0]
    assert (left.segment_id, left.logical_id,
            left.start_bar_index, left.end_bar_index,
            left.created_at_bar, left.confirmed_at_bar) == (
                right.segment_id, right.logical_id,
                right.start_bar_index, right.end_bar_index,
                right.created_at_bar, right.confirmed_at_bar,
            )
    assert right.confirmed_at_raw_bar_index >= left.confirmed_at_raw_bar_index


def test_raw_replay_requires_complete_valid_monotonic_raw_visibility():
    for mutation in (
        lambda values: values.__setitem__(
            2, replace(values[2], confirmed_at_raw_bar_index=True)
        ),
        lambda values: values.__setitem__(
            2, replace(values[2], confirmed_at_raw_bar_index=-1)
        ),
        lambda values: values.__setitem__(
            2, replace(values[2], confirmed_at_raw_bar_index=100)
        ),
    ):
        strokes = raw_strokes()
        mutation(strokes)
        with pytest.raises(
            SegmentEngineCoreError,
            match="SEGMENT_RAW_REPLAY_VISIBILITY_INVALID",
        ):
            replay = FullRebuildEngine(
                phase1_profile(), segment_reference_enabled=True
            )
            replay_segments(replay, strokes, 106)


@pytest.mark.parametrize("watermark", [True, -1, 1.5])
def test_invalid_raw_watermark_fails_closed(watermark):
    replay = FullRebuildEngine(
        phase1_profile(), segment_reference_enabled=True
    )
    with pytest.raises(
        SegmentEngineCoreError,
        match="SEGMENT_RAW_REPLAY_VISIBILITY_INVALID",
    ):
        replay.process([], raw_watermark=watermark)


def test_raw_replay_watermark_is_monotonic_per_engine():
    strokes = raw_strokes()
    replay = FullRebuildEngine(
        phase1_profile(), segment_reference_enabled=True
    )
    replay_segments(replay, strokes[:6], 105)
    with pytest.raises(
        SegmentEngineCoreError,
        match="SEGMENT_RAW_REPLAY_VISIBILITY_INVALID",
    ):
        replay_segments(replay, strokes[:6], 104)


def test_tail_reason_remains_valid_when_replay_prefix_is_truncated():
    for points, expected in (
        ([0, 10, 4, 12, 6], "SEGMENT_FEATURE_WINDOW_INCOMPLETE"),
        ([0, 10, 4, 11, 5, 12, 6], "SEGMENT_PRIMARY_FRACTAL_NOT_FOUND"),
        ([0, 3, 1, 8, 5, 7, 4], "SEGMENT_SECOND_CASE_PENDING"),
    ):
        result = segment_engine().process_primary(
            make_strokes(points), sequence_id="raw-replay-tail"
        )
        assert result.reason_code == expected
        assert result.segment is None
        assert result.completed is False


def test_second_case_pending_keeps_raw_evidence_without_materialization():
    result = segment_engine().process_primary(
        make_strokes(
            [0, 3, 1, 8, 5, 7, 4],
            raw_visibility_overrides={
                index: (100 + index, 100 + index)
                for index in range(6)
            },
        ),
        sequence_id="raw-replay-second-case",
    )
    assert result.reason_code == "SEGMENT_SECOND_CASE_PENDING"
    assert result.segment is None
    assert result.pending_second_case is not None


def test_default_full_rebuild_does_not_activate_raw_replay():
    data = []
    default = FullRebuildEngine(phase1_profile()).process(data)
    explicit = FullRebuildEngine(
        phase1_profile(), segment_reference_enabled=False
    ).process(data)
    assert default == explicit
    with pytest.raises(
        SegmentEngineCoreError,
        match="SEGMENT_RAW_REPLAY_REQUIRES_REFERENCE",
    ):
        FullRebuildEngine(phase1_profile()).process(data, raw_watermark=0)
