"""Acceptance contract for the opt-in FullRebuild Segment reference path."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_only_full_rebuild_and_reference_test_are_new_integration_surface():
    source = (ROOT / "src/chan_parser/engine/full_rebuild.py").read_text()
    assert "segment_reference_enabled" in source
    assert "SegmentLifecycleEmitter" not in source
    assert "IncrementalEngine" not in source
    assert "segment_checkpoint" not in source
