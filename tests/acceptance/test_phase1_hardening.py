"""第一阶段硬门禁：生命周期、严格笔、增量路径、检查点与未来函数。"""
from datetime import datetime, timedelta
import random
import yaml

from chan_parser.audit.consistency import ConsistencyChecker
from chan_parser.domain.fractal import Fractal
from chan_parser.domain.lifecycle import FractalType, StructureStatus
from chan_parser.domain.merged_bar import MergedBar
from chan_parser.domain.raw_bar import RawBar
from chan_parser.engine.full_rebuild import FullRebuildEngine
from chan_parser.engine.incremental import IncrementalEngine
from chan_parser.engine.stroke import StrokeEngine


def profile():
    with open("configs/profiles/minimal_strict_v1.yaml", encoding="utf-8") as f:
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
