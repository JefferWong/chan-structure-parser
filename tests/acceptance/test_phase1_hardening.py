"""第一阶段硬门禁：生命周期、严格笔、增量路径、检查点与未来函数。"""
from datetime import datetime, timedelta
from pathlib import Path
import random

import pytest
import yaml

from chan_parser.audit.consistency import ConsistencyChecker
from chan_parser.domain.fractal import Fractal
from chan_parser.domain.lifecycle import FractalType, StructureStatus
from chan_parser.domain.merged_bar import MergedBar
from chan_parser.domain.raw_bar import RawBar
from chan_parser.engine.full_rebuild import FullRebuildEngine
from chan_parser.engine.incremental import IncrementalEngine
from chan_parser.engine.stroke import StrokeEngine


PROFILE_PATH = (
    Path(__file__).resolve().parents[2]
    / "configs"
    / "profiles"
    / "minimal_strict_v1.yaml"
)


def profile():
    with PROFILE_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def bars(count=160, seed=13):
    rng = random.Random(seed); price = 100.0; start = datetime(2024, 1, 2, 9, 30); out=[]
    for i in range(count):
        delta = rng.gauss(0, 1.5); o=price; c=price+delta
        h=max(o,c)+abs(rng.gauss(0, .7)); l=min(o,c)-abs(rng.gauss(0, .7))
        out.append(RawBar(f"bar_{i+1:06d}", i, start+timedelta(minutes=30*i), round(o,4), round(h,4), round(l,4), round(c,4)))
        price=c
    return out


def mbar(i, high, low):
    return MergedBar(bar_id=f"mbar_{i+1:06d}", bar_index=i, timestamp=datetime(2024,1,1),
                     open=(high+low)/2, high=high, low=low, close=(high+low)/2,
                     source_raw_bar_ids=[f"bar_{i+1:06d}"], logical_id=f"mbar:idx_{i}",
                     status=StructureStatus.CONFIRMED)


def fx(fid, kind, idx, price):
    return Fractal(fractal_id=fid, fractal_type=kind, merged_bar_id=f"mbar_{idx+1:06d}",
                   merged_bar_index=idx, price=price, left_bar_id="", right_bar_id=f"mbar_{idx+2:06d}",
                   window_indices=[idx-1, idx, idx+1], logical_id=f"fractal:{kind.value}:{idx}")


def test_lifecycle_is_non_empty_and_deterministic():
    p=profile(); data=bars(120)
    a=FullRebuildEngine(p).process(data); b=FullRebuildEngine(p).process(data)
    assert a["events"] and a["audit"]["event_count"] == len(a["events"])
    assert a["audit"]["event_log_sha256"] == b["audit"]["event_log_sha256"]
    assert any(e["event_type"] == "OBJECT_CREATED" for e in a["events"])
    assert any(e["event_type"] in {"OBJECT_CONFIRMED", "STATUS_CHANGED"} for e in a["events"])


def test_strict_endpoint_failure_never_creates_or_confirms_stroke():
    p=profile(); engine=StrokeEngine(p["stroke"])
    seq=[mbar(i, 110 if i==4 else 105, 90 if i==3 else 95) for i in range(10)]
    fractals=[fx("fx_a", FractalType.BOTTOM, 1, 95), fx("fx_b", FractalType.TOP, 8, 108)]
    strokes, events=engine.process(fractals, seq, 10)
    assert strokes == []
    assert any(e.event_type == "CANDIDATE_REJECTED" and e.reason_code == "ENDPOINT_NOT_INTERVAL_EXTREME" for e in events)


def test_incremental_and_full_use_independent_paths_and_match():
    p=profile(); data=bars(150)
    full=FullRebuildEngine(p).process(data)
    inc=IncrementalEngine(p); result=None
    for i in range(0, len(data), 7): result=inc.append_batch(data[i:i+7])
    checked=ConsistencyChecker().check(full, result)
    assert checked["pass"], checked["differences"]
    assert result["meta"]["calculation_mode"] == "incremental_frozen_prefix_bounded_tail"
    assert result["runtime_state"]["rebuild_count"] > 0
    assert result["runtime_state"]["local_rebuild_to"] - result["runtime_state"]["local_rebuild_from"] + 1 <= p["runtime"]["max_rebuild_distance"] + 7


def test_checkpoint_restore_and_replay_are_deterministic():
    p=profile(); data=bars(120); inc=IncrementalEngine(p)
    inc.append_batch(data[:60]); cp=inc.create_checkpoint(); checkpoint_hash=inc.get_current_state()["audit"]["output_sha256"]
    final1=inc.append_batch(data[60:])["audit"]["output_sha256"]
    restored=inc.resume_from_checkpoint(cp)
    assert restored["audit"]["output_sha256"] == checkpoint_hash
    final2=inc.append_batch(data[60:])["audit"]["output_sha256"]
    assert final1 == final2


def test_historical_snapshot_is_immutable():
    p=profile(); data=bars(100); inc=IncrementalEngine(p)
    inc.append_batch(data[:50]); snap=inc.get_historical_snapshot(50)
    inc.append_batch(data[50:])
    assert inc.get_historical_snapshot(50) == snap


def test_confirmed_objects_do_not_reference_future_bars():
    p=profile(); data=bars(90)
    for n in range(20, 91, 10):
        result=FullRebuildEngine(p).process(data[:n]); merged_count=len(result["structures"]["merged_bars"])
        for item in result["structures"]["fractals"]:
            assert item["created_at_bar"] < merged_count
            if item["confirmed_at_bar"] is not None: assert item["confirmed_at_bar"] < merged_count
        for item in result["structures"]["strokes"]:
            assert item["end_bar_index"] < merged_count
            if item["confirmed_at_bar"] is not None: assert item["confirmed_at_bar"] < merged_count


def test_actual_engine_inputs_are_bounded_and_prefix_is_reused():
    p = profile()
    p["runtime"]["max_rebuild_distance"] = 80
    data = bars(600, seed=13)
    inc = IncrementalEngine(p)
    observed = {
        "inclusion_raw_bars": [],
        "fractal_merged_bars": [],
        "stroke_fractals": [],
        "stroke_merged_bars": [],
    }

    original_inclusion = inc.inclusion_engine.process
    original_fractal = inc.fractal_engine.process
    original_stroke = inc.stroke_engine.process

    def inclusion_spy(raw_bars, *args, **kwargs):
        observed["inclusion_raw_bars"].append(len(raw_bars))
        return original_inclusion(raw_bars, *args, **kwargs)

    def fractal_spy(merged_bars, *args, **kwargs):
        observed["fractal_merged_bars"].append(len(merged_bars))
        return original_fractal(merged_bars, *args, **kwargs)

    def stroke_spy(fractals, merged_bars, *args, **kwargs):
        observed["stroke_fractals"].append(len(fractals))
        observed["stroke_merged_bars"].append(len(merged_bars))
        return original_stroke(fractals, merged_bars, *args, **kwargs)

    inc.inclusion_engine.process = inclusion_spy
    inc.fractal_engine.process = fractal_spy
    inc.stroke_engine.process = stroke_spy

    chunk_size = 20
    result = None
    for i in range(0, len(data), chunk_size):
        result = inc.append_batch(data[i:i + chunk_size])

    allowed = p["runtime"]["max_rebuild_distance"] + chunk_size
    assert all(observed.values())
    assert max(max(values) for values in observed.values()) <= allowed
    assert result["runtime_state"]["max_engine_input_sizes"] == {
        key: max(values) for key, values in observed.items()
    }
    assert result["runtime_state"]["frozen_prefix"]["merged_bars"] > 0
    assert result["runtime_state"]["frozen_prefix"]["fractals"] > 0
    assert result["runtime_state"]["frozen_prefix"]["strokes"] > 0

    full = FullRebuildEngine(p).process(data)
    checked = ConsistencyChecker().check(full, result)
    assert checked["pass"], checked["differences"]


def test_equal_boundary_preserves_direction_seed_across_incremental_split():
    p = profile()
    start = datetime(2024, 1, 2, 9, 30)
    data = [
        RawBar("bar_000001", 0, start, 99, 101, 96, 98),
        RawBar("bar_000002", 1, start + timedelta(minutes=30), 98, 100, 95, 97),
        # Equal high means no inclusion in explicit mode. The carried direction must remain DOWN.
        RawBar("bar_000003", 2, start + timedelta(minutes=60), 97, 100, 94, 96),
        # Strictly contained by bar 3; DOWN merge must produce high=99, low=94.
        RawBar("bar_000004", 3, start + timedelta(minutes=90), 96, 99, 95, 96),
    ]

    full = FullRebuildEngine(p).process(data)
    inc = IncrementalEngine(p)
    before = inc.append_batch(data[:3])
    assert before["structures"]["merged_bars"][-1]["merge_direction"] == "DOWN"
    result = inc.append_one(data[3])

    checked = ConsistencyChecker().check(full, result)
    assert checked["pass"], checked["differences"]
    last = result["structures"]["merged_bars"][-1]
    assert (last["high"], last["low"], last["merge_direction"]) == (99, 94, "DOWN")


def test_incremental_ids_are_unique_and_do_not_false_confirm_disconnected_strokes():
    p = profile()
    p["runtime"]["max_rebuild_distance"] = 1000
    data = bars(160, seed=12)
    full = FullRebuildEngine(p).process(data)
    inc = IncrementalEngine(p)
    result = None
    for i in range(0, len(data), 2):
        result = inc.append_batch(data[i:i + 2])

    fractals = result["structures"]["fractals"]
    strokes = result["structures"]["strokes"]
    assert len({x["fractal_id"] for x in fractals}) == len(fractals)
    assert len({x["object_id"] for x in fractals}) == len(fractals)
    assert len({x["stroke_id"] for x in strokes}) == len(strokes)
    assert len({x["object_id"] for x in strokes}) == len(strokes)

    fractal_ids = {x["fractal_id"] for x in fractals}
    assert all(x["start_fractal_id"] in fractal_ids for x in strokes)
    assert all(x["end_fractal_id"] in fractal_ids for x in strokes)

    checked = ConsistencyChecker().check(full, result)
    assert checked["pass"], checked["differences"]


def test_incremental_records_new_tail_candidate_rejections_after_bootstrap():
    p = profile()
    data = bars(160, seed=13)
    inc = IncrementalEngine(p)
    first = inc.append_batch(data[:20])
    before = sum(
        event["event_type"] == "CANDIDATE_REJECTED"
        for event in first["events"]
    )
    result = first
    for i in range(20, len(data), 7):
        result = inc.append_batch(data[i:i + 7])
    after = sum(
        event["event_type"] == "CANDIDATE_REJECTED"
        for event in result["events"]
    )
    assert after > before



def test_snapshot_and_checkpoint_retention_are_bounded():
    p = profile()
    p["runtime"].update({
        "checkpoint_interval": 10,
        "snapshot_retention": 3,
        "checkpoint_retention": 2,
    })
    data = bars(80, seed=21)
    inc = IncrementalEngine(p)
    result = None
    for i in range(0, len(data), 10):
        result = inc.append_batch(data[i:i + 10])

    runtime = result["runtime_state"]
    assert runtime["historical_snapshot_count"] == 3
    assert runtime["checkpoint_count"] == 2
    assert runtime["retained_checkpoint_ids"] == [6, 7]
    with pytest.raises(KeyError, match="not retained"):
        inc.get_historical_snapshot(10)

    latest_snapshot = inc.get_historical_snapshot(80)
    assert latest_snapshot["output_sha256"] == result["audit"]["output_sha256"]

    restored = inc.resume_from_checkpoint(6)
    assert restored["runtime_state"]["last_processed_bar_id"] == "bar_000070"
    assert restored["runtime_state"]["checkpoint_count"] == 1
    assert restored["runtime_state"]["retained_checkpoint_ids"] == [6]


def test_incremental_data_quality_matches_full_for_invalid_bars():
    p = profile()
    start = datetime(2024, 1, 2, 9, 30)
    data = [
        RawBar("bar_000001", 0, start, 100, 105, 98, 103),
        RawBar("bar_000002", 1, start + timedelta(minutes=30), 100, 99, 98, 103),
    ]
    assert data[-1].is_valid is False

    full = FullRebuildEngine(p).process(data)
    incremental = IncrementalEngine(p).append_batch(data)
    assert incremental["data_quality"] == full["data_quality"]
    assert incremental["data_quality"]["status"] == "WARNING"


def test_phase1_workflow_uses_read_only_token_without_persisted_credentials():
    workflow_path = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "phase1-gates.yml"
    workflow = workflow_path.read_text(encoding="utf-8")
    assert "permissions:\n      contents: read" in workflow
    assert "persist-credentials: false" in workflow



@pytest.mark.parametrize(
    "override, message",
    [
        ({"window_size": 5}, "window_size=3"),
        ({"use_merged_bars": False}, "use_merged_bars=true"),
        ({"minimum_distance": 2}, "minimum_distance=1"),
    ],
)
def test_unsupported_phase1_fractal_config_fails_closed(override, message):
    p = profile()
    p["fractal"].update(override)
    with pytest.raises(ValueError, match=message):
        FullRebuildEngine(p)
