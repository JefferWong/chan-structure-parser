"""Stage A lifecycle contract remains disconnected from all runtime paths."""
from __future__ import annotations

import ast
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from chan_parser.domain.raw_bar import RawBar
from chan_parser.engine.full_rebuild import FullRebuildEngine
from chan_parser.engine.incremental import IncrementalEngine


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "src/chan_parser/contracts/segment_lifecycle.py"
SEGMENT_ENGINE = ROOT / "src/chan_parser/engine/segment.py"
FULL = ROOT / "src/chan_parser/engine/full_rebuild.py"
INCREMENTAL = ROOT / "src/chan_parser/engine/incremental.py"
ENGINE_INIT = ROOT / "src/chan_parser/engine/__init__.py"
ENGINE_PROFILE = ROOT / "configs/profiles/minimal_segment_engine_core_v1.yaml"


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = f'{"." * node.level}{node.module or ""}'
            result.add(module)
            result.update(alias.name for alias in node.names)
    return result


def bars() -> list[RawBar]:
    start = datetime(2024, 1, 2, 9, 30)
    return [
        RawBar(
            f"bar_{index + 1:06d}", index,
            start + timedelta(minutes=30 * index),
            100 + index, 102 + index, 99 + index, 101 + index,
        )
        for index in range(12)
    ]


def phase1_profile() -> dict:
    path = ROOT / "configs/profiles/minimal_strict_v1.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_lifecycle_contract_has_no_runtime_imports():
    forbidden = {
        "SegmentEngine", "FullRebuildEngine", "IncrementalEngine",
        "EventLog", "Checkpoint",
    }
    imported = imports(CONTRACT)
    assert not forbidden.intersection(imported)
    text = CONTRACT.read_text(encoding="utf-8")
    assert "chan_parser.engine" not in text
    assert "audit.event_log" not in text


def test_contract_does_not_construct_lifecycle_events():
    tree = ast.parse(CONTRACT.read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "LifecycleEvent"
        for node in ast.walk(tree)
    )
    assert "event_id" not in CONTRACT.read_text(encoding="utf-8")


def test_segment_engine_does_not_import_lifecycle_contract():
    for path in (ROOT / "src/chan_parser").rglob("*.py"):
        if path == CONTRACT:
            continue
        assert "segment_lifecycle" not in path.read_text(encoding="utf-8"), path


def test_full_and_incremental_do_not_import_segment_engine():
    for path in (FULL, INCREMENTAL):
        text = path.read_text(encoding="utf-8")
        assert "SegmentEngine" not in text
        assert "engine.segment" not in text
        assert "from .segment" not in text


def test_engine_package_does_not_export_segment_engine():
    text = ENGINE_INIT.read_text(encoding="utf-8")
    assert "SegmentEngine" not in text
    assert "segment" not in text.lower()


def test_segment_engine_profile_keeps_all_runtime_integration_disabled():
    loaded = yaml.safe_load(ENGINE_PROFILE.read_text(encoding="utf-8"))
    implementation = loaded["implementation"]
    assert implementation["lifecycle_events_enabled"] is False
    assert implementation["parser_integration_enabled"] is False
    assert implementation["checkpoint_integration_enabled"] is False
    assert implementation["full_incremental_integration_enabled"] is False
    assert implementation["second_case_orchestration_enabled"] is False


def test_full_and_incremental_outputs_remain_segment_free():
    data = bars()
    full = FullRebuildEngine(phase1_profile()).process(data)
    incremental = IncrementalEngine(phase1_profile()).append_batch(data)
    expected = {"merged_bars", "fractals", "strokes"}
    assert set(full["structures"]) == expected
    assert set(incremental["structures"]) == expected


def test_no_center_or_zhongshu_implementation_exists():
    production = ROOT / "src/chan_parser"
    implementation_paths = [
        path.relative_to(production).as_posix().lower()
        for path in production.rglob("*.py")
    ]
    assert not any(
        "center" in path or "zhongshu" in path
        for path in implementation_paths
    )
