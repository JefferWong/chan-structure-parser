"""PR28 regressions against the frozen real-market qualification prefixes."""

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from chan_parser.adapters import CSVAdapter
from chan_parser.domain.lifecycle import StructureStatus
from chan_parser.engine.full_rebuild import FullRebuildEngine
from chan_parser.engine.incremental import IncrementalEngine


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "pr28_real_market"
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"
PREFIX_LENGTHS = {
    "600519.SH": 89,
    "300750.SZ": 183,
    "510300.SH": 214,
}
PROFILE_PATH = Path(__file__).parents[2] / "configs/profiles/minimal_strict_v1.yaml"
OLD_PHASE1_SOURCE_ERRORS = {
    "SEGMENT_SOURCE_DIRECTION_NOT_ALTERNATING",
    "SEGMENT_SOURCE_ENDPOINT_NOT_CONTIGUOUS",
    "SEGMENT_SOURCE_ENDPOINT_PRICE_NOT_CONTIGUOUS",
}


def _profile():
    return yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))


def _bars(symbol):
    path = FIXTURE_ROOT / f"{symbol}.csv"
    assert path.is_file(), f"missing committed PR28 fixture: {path}"
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entry = manifest["fixtures"][symbol]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["fixture_sha256"]
    bars, quality = CSVAdapter(str(path)).load()
    assert quality["status"] == "OK"
    assert len(bars) == entry["fixture_prefix_row_count"]
    assert bars[0].timestamp.date().isoformat() == entry["fixture_first_date"]
    assert bars[-1].timestamp.date().isoformat() == entry["fixture_last_date"]
    return bars[: PREFIX_LENGTHS[symbol]]


def _seam262_bars():
    path = FIXTURE_ROOT / "600519.SH.phase1_262.csv"
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entry = manifest["fixtures"]["600519.SH"]["phase1_262_fixture"]
    assert path.is_file()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["fixture_sha256"]
    bars, quality = CSVAdapter(str(path)).load()
    assert quality["status"] == "OK"
    assert len(bars) == entry["row_count"]
    assert bars[-1].timestamp.date().isoformat() == entry["last_date"]
    return bars


def _confirmed(strokes):
    return [stroke for stroke in strokes if stroke.status == StructureStatus.CONFIRMED]


def _signature(stroke):
    return (
        stroke.logical_id,
        stroke.direction.value,
        stroke.start_fractal_id,
        stroke.end_fractal_id,
        stroke.start_bar_index,
        stroke.end_bar_index,
        stroke.start_price,
        stroke.end_price,
        stroke.status.value,
    )


def _phase1_projection(items):
    return [item.to_dict() if hasattr(item, "to_dict") else item for item in items]


def _merged_phase1_projection(items):
    # Incremental inclusion carries its tail direction seed and may revise
    # merged-bar identities during ordinary reconciliation.  Phase1 geometry
    # and source ownership are the equivalence authority; stroke identities
    # remain compared exactly below.
    ignored = {"merge_direction", "object_id", "revision"}
    return [
        {key: value for key, value in item.items() if key not in ignored}
        for item in _phase1_projection(items)
    ]


def _assert_confirmed_chain(strokes):
    confirmed = _confirmed(strokes)
    for previous, current in zip(confirmed, confirmed[1:]):
        assert previous.direction != current.direction
        assert previous.end_fractal_id == current.start_fractal_id
        assert previous.end_bar_index == current.start_bar_index
        assert previous.end_price == current.start_price


def _run_production_prefix(engine, bars):
    successful = 0
    caught = None
    caught_bar_number = None
    caught_date = None
    for number, bar in enumerate(bars, start=1):
        try:
            engine.append_one(bar)
            successful += 1
        except Exception as exc:
            caught = exc
            caught_bar_number = number
            caught_date = bar.timestamp.date().isoformat()
            break
    return successful, caught, caught_bar_number, caught_date


def _assert_production_frontier(engine, bars, expected):
    expected_outcome = expected["production_expected_outcome"]
    assert expected_outcome not in OLD_PHASE1_SOURCE_ERRORS
    successful, caught, caught_bar_number, caught_date = _run_production_prefix(
        engine, bars
    )
    if expected_outcome == "PASS":
        assert caught is None
        assert successful == len(bars)
        assert expected["production_successful_bars_before_stop"] == len(bars)
        assert expected["production_frontier_bar_number"] is None
        assert expected["production_frontier_date"] is None
    else:
        assert caught is not None
        assert str(caught) == expected_outcome
        assert successful == expected["production_successful_bars_before_stop"]
        assert caught_bar_number == expected["production_frontier_bar_number"]
        assert caught_date == expected["production_frontier_date"]


@pytest.mark.parametrize("symbol", PREFIX_LENGTHS)
def test_real_prefix_full_and_incremental_phase1_are_equivalent(symbol):
    profile = _profile()
    bars = _bars(symbol)

    full = FullRebuildEngine(profile).process(bars)
    full_confirmed = [
        stroke for stroke in full["structures"]["strokes"]
        if stroke["status"] == StructureStatus.CONFIRMED.value
    ]

    incremental = IncrementalEngine(profile)
    for bar in bars:
        incremental.append_one(bar)
    incremental_confirmed = _confirmed(incremental._strokes)

    _assert_confirmed_chain(incremental._strokes)
    assert [_signature(stroke) for stroke in incremental_confirmed] == [
        (
            stroke["logical_id"],
            stroke["direction"],
            stroke["start_fractal_id"],
            stroke["end_fractal_id"],
            stroke["start_bar_index"],
            stroke["end_bar_index"],
            stroke["start_price"],
            stroke["end_price"],
            stroke["status"],
        )
        for stroke in full_confirmed
    ]


@pytest.mark.parametrize("symbol", PREFIX_LENGTHS)
def test_real_prefix_production_reaches_expected_frontier(symbol):
    profile = _profile()
    bars = _bars(symbol)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    expected = manifest["fixtures"][symbol]
    engine = IncrementalEngine(profile, segment_production_enabled=True)

    _assert_production_frontier(engine, bars, expected)
    _assert_confirmed_chain(engine._strokes)


def test_600519_phase1_seam_262_full_incremental_equivalent():
    profile = _profile()
    bars = _seam262_bars()
    full = FullRebuildEngine(profile).process(bars)
    incremental = IncrementalEngine(profile)
    for bar in bars:
        incremental.append_one(bar)
        _assert_confirmed_chain(incremental._strokes)

    assert _merged_phase1_projection(incremental._merged_bars) == _merged_phase1_projection(
        full["structures"]["merged_bars"]
    )
    assert _phase1_projection(incremental._fractals) == full["structures"]["fractals"]
    assert _phase1_projection(incremental._strokes) == full["structures"]["strokes"]


def test_600519_phase1_seam_262_rolls_back_orphaned_confirmation():
    profile = _profile()
    bars = _seam262_bars()
    engine = IncrementalEngine(profile)
    for bar in bars:
        engine.append_one(bar)

    predecessor = next(
        stroke for stroke in engine._strokes
        if stroke.object_id == "stroke_000181_000185_U_r1"
    )
    assert predecessor.status == StructureStatus.PROVISIONAL
    assert predecessor.confirmed_at_bar is None
    assert predecessor.confirmed_at_raw_bar_index is None
    assert predecessor.repaint_risk == "HIGH"
    assert predecessor.confirmation_requirements == ["next strict stroke must confirm"]
    _assert_confirmed_chain(engine._strokes)


def test_early_unrelated_exception_cannot_false_pass():
    from datetime import datetime, timedelta
    from types import SimpleNamespace

    class EarlyFailureEngine:
        def __init__(self):
            self.calls = 0

        def append_one(self, _bar):
            self.calls += 1
            if self.calls == 100:
                raise RuntimeError("UNRELATED_EARLY_FAILURE")

    bars = [
        SimpleNamespace(timestamp=datetime(2020, 1, 1) + timedelta(days=i))
        for i in range(183)
    ]
    expected = {
        "production_expected_outcome": "SEGMENT_FEATURE_INCLUSION_UNSEEDED",
        "production_successful_bars_before_stop": 151,
        "production_frontier_bar_number": 152,
        "production_frontier_date": "2020-05-31",
    }

    with pytest.raises(AssertionError):
        _assert_production_frontier(EarlyFailureEngine(), bars, expected)


def test_wrong_frontier_cannot_false_pass():
    from datetime import datetime, timedelta
    from types import SimpleNamespace

    class WrongFrontierEngine:
        def __init__(self):
            self.calls = 0

        def append_one(self, _bar):
            self.calls += 1
            if self.calls == 152:
                raise RuntimeError("SEGMENT_FEATURE_INCLUSION_UNSEEDED")

    bars = [
        SimpleNamespace(timestamp=datetime(2020, 1, 1) + timedelta(days=i))
        for i in range(183)
    ]
    expected = {
        "production_expected_outcome": "SEGMENT_FEATURE_INCLUSION_UNSEEDED",
        "production_successful_bars_before_stop": 151,
        "production_frontier_bar_number": 153,
        "production_frontier_date": "2020-06-01",
    }

    with pytest.raises(AssertionError):
        _assert_production_frontier(WrongFrontierEngine(), bars, expected)
