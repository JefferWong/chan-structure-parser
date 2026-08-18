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
    SOURCE / "engine/full_rebuild.py": "2afef353341c5e35092885b4b48d0c4944dc9e8ae57d66f949c5b4538e39b36d",
    SOURCE / "engine/incremental.py": "aef46f378607560c0c2fb0015f0041b8c7092a01292560c1a5fb6c1563cd9cd8",
    SOURCE / "engine/segment.py": "26c3c48edcdd71f5856af0ffde90e670899ae8372be229dff3b2fef7d97ce9ea",
    SOURCE / "engine/segment_lifecycle_emitter.py": "5049668739f6b71633e083d5ba14145964c536dd1e20e879e3ad7c4e39497f73",
    SOURCE / "contracts/segment_checkpoint.py": "2079d159ef134a032d4d45c04d580797491e08c5ce01ca827107776297ad5880",
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
        Path("src/chan_parser/engine/stroke.py"),
    }
    assert _production_users("confirmed_at_raw_bar_index") == {
        Path("src/chan_parser/domain/stroke.py"),
        Path("src/chan_parser/engine/stroke.py"),
    }


def test_legacy_runtime_files_remain_byte_exact_and_outputs_do_not_serialize_raw_fields():
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
        SOURCE / "engine/segment.py",
        SOURCE / "engine/full_rebuild.py",
        SOURCE / "engine/incremental.py",
        SOURCE / "contracts/segment_checkpoint.py",
        SOURCE / "contracts/segment_lifecycle.py",
        SOURCE / "engine/segment_lifecycle_emitter.py",
    )
    for path in forbidden:
        assert "created_at_raw_bar_index" not in path.read_text(encoding="utf-8")
        assert "confirmed_at_raw_bar_index" not in path.read_text(encoding="utf-8")


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
