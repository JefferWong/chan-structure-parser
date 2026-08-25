"""PR11 Phase 1 opt-in FullRebuild Segment reference contract."""
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from chan_parser.domain.raw_bar import RawBar
from chan_parser.engine.full_rebuild import FullRebuildEngine


ROOT = Path(__file__).resolve().parents[2]


def profile():
    return yaml.safe_load((ROOT / "configs/profiles/minimal_strict_v1.yaml").read_text())


def bars(count=80, seed=23):
    import random
    rng = random.Random(seed)
    price = 100.0
    start = datetime(2024, 1, 2, 9, 30)
    result = []
    for index in range(count):
        delta = rng.gauss(0, 1.3)
        opening = price
        closing = price + delta
        high = max(opening, closing) + abs(rng.gauss(0, 0.6))
        low = min(opening, closing) - abs(rng.gauss(0, 0.6))
        result.append(RawBar(f"bar_{index + 1:06d}", index,
                             start + timedelta(minutes=30 * index), opening,
                             high, low, closing))
        price = closing
    return result


def test_default_full_rebuild_unchanged():
    data = bars()
    first = FullRebuildEngine(profile()).process(data)
    explicit_default = FullRebuildEngine(
        profile(), segment_reference_enabled=False
    ).process(data)
    assert first == explicit_default
    assert "segments" not in first["structures"]


def test_opt_in_reference_output_and_no_lifecycle_events():
    result = FullRebuildEngine(
        profile(), segment_reference_enabled=True
    ).process(bars())
    assert "segments" in result["structures"]
    assert all(event["object_type"] != "segment" for event in result["events"])
    assert isinstance(result["structures"]["segments"], list)


def test_raw_structural_axis_separation():
    from tests.unit.test_segment_engine import engine, make_strokes

    strokes = make_strokes(
        [0, 10, 4, 12, 6, 11, 5],
        visibility_overrides={3: 8},
        raw_visibility_overrides={index: (index + 10, index + 10)
                                  for index in range(6)},
    )
    segment = engine().process_primary(strokes, sequence_id="raw-axis").segment
    assert segment is not None
    assert segment.confirmed_at_bar != segment.confirmed_at_raw_bar_index
    assert segment.created_at_bar != segment.created_at_raw_bar_index


def test_tail_reason_preserved_until_explicit_completion():
    from tests.unit.test_segment_engine import engine, make_strokes

    for points, reason in (
        ([0, 10, 4, 12, 6], "SEGMENT_FEATURE_WINDOW_INCOMPLETE"),
        ([0, 3, 1, 8, 5, 7, 4], "SEGMENT_SECOND_CASE_PENDING"),
        ([0, 10, 4, 11, 5, 12, 6], "SEGMENT_PRIMARY_FRACTAL_NOT_FOUND"),
    ):
        result = engine().process_primary(make_strokes(points), sequence_id="tail")
        assert result.reason_code == reason
        assert result.completed is False


def test_incremental_checkpoint_and_emitter_are_not_integration_dependencies():
    source = (ROOT / "src/chan_parser/engine/full_rebuild.py").read_text()
    assert "IncrementalEngine" not in source
    assert "segment_checkpoint" not in source
    assert "segment_lifecycle_emitter" not in source
