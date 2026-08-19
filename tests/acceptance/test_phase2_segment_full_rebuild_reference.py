"""Public FullRebuild Segment reference-path acceptance."""
from __future__ import annotations

import ast
import random
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from chan_parser.domain.raw_bar import RawBar
from chan_parser.engine.full_rebuild import FullRebuildEngine

ROOT = Path(__file__).resolve().parents[2]
FULL = ROOT / "src/chan_parser/engine/full_rebuild.py"


def loaded(name: str) -> dict:
    return yaml.safe_load((ROOT / "configs/profiles" / name).read_text(encoding="utf-8"))


def real_bars() -> list[RawBar]:
    rng = random.Random(777)
    price = 100.0
    start = datetime(2024, 1, 2, 9, 30)
    result = []
    for index in range(80):
        delta = rng.gauss(0, 1.5)
        open_price, close_price = price, price + delta
        high = max(open_price, close_price) + abs(rng.gauss(0, 0.5))
        low = max(0.1, min(open_price, close_price) - abs(rng.gauss(0, 0.5)))
        result.append(RawBar(
            f"bar_{index + 1:06d}", index, start + timedelta(minutes=30 * index),
            round(open_price, 2), round(high, 2), round(low, 2), round(close_price, 2),
        ))
        price = close_price
    return result


def segment_engine() -> FullRebuildEngine:
    return FullRebuildEngine(
        loaded("minimal_strict_v1.yaml"),
        segment_engine_profile=loaded("minimal_segment_engine_core_v1.yaml"),
        segment_lifecycle_profile=loaded("minimal_segment_lifecycle_emission_v1.yaml"),
    )


def test_segment_real_full_rebuild_e2e_is_deterministic():
    first = segment_engine().process(real_bars())
    second = segment_engine().process(real_bars())
    assert first == second
    assert first["meta"]["engine_version"] == "0.3.0"
    assert first["meta"]["segment_reference_mode"] == "R1_FIRST_CASE_VISIBILITY_REPLAY"
    segments = first["structures"]["segments"]
    assert segments and all(item["status"] == "CONFIRMED" for item in segments)
    segment_events = [event for event in first["events"] if event["object_type"] == "segment"]
    assert len(segment_events) == 2 * len(segments)
    assert [event["event_type"] for event in segment_events] == [
        value for _ in segments for value in ("OBJECT_CREATED", "OBJECT_CONFIRMED")
    ]
    assert first["audit"]["segment_event_count"] == len(segment_events)
    assert first["audit"]["segment_event_count"] == 2 * first["runtime_state"]["confirmed_segment_count"]
    assert not any(event["object_type"] == "checkpoint" for event in first["events"])


def test_legacy_full_rebuild_schema_remains_exactly_segment_free():
    result = FullRebuildEngine(loaded("minimal_strict_v1.yaml")).process(real_bars())
    assert set(result["structures"]) == {"merged_bars", "fractals", "strokes"}
    assert result["meta"]["engine_version"] == "0.2.0"
    assert "segment_reference_mode" not in result["meta"]


def test_full_rebuild_orchestration_imports_only_authorized_segment_runtime():
    tree = ast.parse(FULL.read_text(encoding="utf-8"))
    imports = {
        f'{"." * node.level}{node.module or ""}'
        for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    assert ".segment" in imports
    assert ".segment_lifecycle_emitter" in imports
    assert not any("segment_rules" in path or "segment_checkpoint" in path for path in imports)
    calls = {
        node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    }
    assert not calls.intersection({
        "build_feature_sequence", "classify_primary_destruction_case", "confirmation_bar",
        "merge_included_intervals", "derive_inclusion_seed", "classify_interval_relation",
        "validate_segment_boundaries", "derive_segment_checkpoint_state",
    })
