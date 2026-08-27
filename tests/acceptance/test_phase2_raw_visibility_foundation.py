"""Acceptance gates for the raw visibility foundation only."""
from __future__ import annotations

import ast
import hashlib
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from chan_parser.domain.raw_bar import RawBar
from chan_parser.engine.full_rebuild import FullRebuildEngine
from chan_parser.engine.incremental import IncrementalEngine

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/chan_parser"
BASELINE = {
    SOURCE / "contracts/segment_checkpoint.py": "bd432a08e0fe1f03ff0180b0607c23eb1ec38ff64717391f3270de8dfd8957ed",
}


def bars():
    start = datetime(2024, 1, 1, 9, 30)
    return [RawBar(f"bar_{i:06d}", i, start + timedelta(minutes=i),
                   100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(12)]


def profile():
    return yaml.safe_load((ROOT / "configs/profiles/minimal_strict_v1.yaml").read_text())


def _production_users(symbol: str) -> set[Path]:
    return {
        path.relative_to(ROOT)
        for path in SOURCE.rglob("*.py")
        if symbol in path.read_text(encoding="utf-8")
    }


def test_raw_visibility_authority_sets_are_durable_and_exact():
    assert _production_users("source_raw_bar_indices") == {
        Path("src/chan_parser/domain/merged_bar.py"),
        Path("src/chan_parser/engine/inclusion.py"),
        Path("src/chan_parser/engine/stroke.py"),
    }
    assert _production_users("visible_at_raw_bar_index") == {
        Path("src/chan_parser/domain/merged_bar.py"),
        Path("src/chan_parser/engine/inclusion.py"),
        Path("src/chan_parser/engine/stroke.py"),
    }
    assert _production_users("created_at_raw_bar_index") == {
        Path("src/chan_parser/domain/stroke.py"),
        Path("src/chan_parser/domain/segment.py"),
        Path("src/chan_parser/engine/stroke.py"),
        Path("src/chan_parser/engine/segment.py"),
        Path("src/chan_parser/engine/full_rebuild.py"),
    }
    assert _production_users("confirmed_at_raw_bar_index") == {
        Path("src/chan_parser/domain/stroke.py"),
        Path("src/chan_parser/domain/segment.py"),
        Path("src/chan_parser/engine/stroke.py"),
        Path("src/chan_parser/engine/segment.py"),
        Path("src/chan_parser/engine/full_rebuild.py"),
    }


def test_frozen_raw_visibility_files_remain_byte_exact_and_outputs_do_not_serialize_raw_fields():
    for path, expected in BASELINE.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
    full = FullRebuildEngine(profile()).process(bars())
    incremental = IncrementalEngine(profile()).append_batch(bars())
    assert set(full["structures"]) == {"merged_bars", "fractals", "strokes"}
    assert set(incremental["structures"]) == {"merged_bars", "fractals", "strokes"}
    for output in (full, incremental):
        for item in output["structures"]["merged_bars"]:
            assert "source_raw_bar_indices" not in item
            assert "visible_at_raw_bar_index" not in item
        for item in output["structures"]["strokes"]:
            assert "created_at_raw_bar_index" not in item
            assert "confirmed_at_raw_bar_index" not in item


def test_raw_visibility_field_is_not_consumed_by_forbidden_production_modules():
    forbidden = (
        SOURCE / "engine/incremental.py",
        SOURCE / "contracts/segment_checkpoint.py",
        SOURCE / "contracts/segment_lifecycle.py",
        SOURCE / "engine/segment_lifecycle_emitter.py",
    )
    for path in forbidden:
        assert "created_at_raw_bar_index" not in path.read_text(encoding="utf-8")
        assert "confirmed_at_raw_bar_index" not in path.read_text(encoding="utf-8")


def test_segment_engine_is_the_only_segment_raw_visibility_consumer():
    segment = (SOURCE / "engine/segment.py").read_text(encoding="utf-8")
    assert "created_at_raw_bar_index" in segment
    assert "confirmed_at_raw_bar_index" in segment


def test_deferred_full_rebuild_reference_markers_are_absent():
    forbidden_markers = (
        "FullRebuild" + "SegmentReferenceError",
        "R1_FIRST_CASE_" + "VISIBILITY_REPLAY",
        "FULL_REBUILD_" + "SEGMENT_BACKFILL_FORBIDDEN",
    )
    for path in ROOT.rglob("*.py"):
        if "__pycache__" not in path.parts and path != Path(__file__):
            text = path.read_text(encoding="utf-8")
            assert not any(marker in text for marker in forbidden_markers), path


def test_legacy_confirmed_at_remains_merged_axis():
    tree = ast.parse((SOURCE / "engine/stroke.py").read_text(encoding="utf-8"))
    mark_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "mark_confirmed"
    ]
    assert len(mark_calls) == 1
    for call in mark_calls:
        assert isinstance(call.func.value, ast.Name)
        assert call.func.value.id == "previous"
        assert len(call.args) == 1
        argument = call.args[0]
        assert isinstance(argument, ast.Attribute)
        assert isinstance(argument.value, ast.Name)
        assert argument.value.id == "stroke"
        assert argument.attr == "end_bar_index"
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "mark_confirmed"
        and any(
            isinstance(arg, ast.Attribute)
            and arg.attr in {"confirmed_at_raw_bar_index", "created_at_raw_bar_index"}
            for arg in node.args
        )
        for node in ast.walk(tree)
    )
    assert "confirmed_at_bar" in (SOURCE / "domain/stroke.py").read_text(encoding="utf-8")
