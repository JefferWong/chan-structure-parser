"""PR28 regressions against the frozen real-market qualification prefixes.

The CSVs are the 2026-08-28 Eastmoney primary / Tencent-or-Sina cross-check
qualification artifacts, in RAW_UNADJUSTED mode.  They intentionally remain
outside the repository; these tests are skipped when that local evidence is
not available and never access a network provider.
"""

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


def _assert_confirmed_chain(strokes):
    confirmed = _confirmed(strokes)
    for previous, current in zip(confirmed, confirmed[1:]):
        assert previous.direction != current.direction
        assert previous.end_fractal_id == current.start_fractal_id
        assert previous.end_bar_index == current.start_bar_index
        assert previous.end_price == current.start_price


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
def test_real_prefix_production_has_no_old_phase1_source_error(symbol):
    profile = _profile()
    bars = _bars(symbol)
    engine = IncrementalEngine(profile, segment_production_enabled=True)

    error = None
    for bar in bars:
        try:
            engine.append_one(bar)
        except Exception as caught:  # assertion below narrows the result
            error = caught
            break

    assert error is None or str(error) not in {
        "SEGMENT_SOURCE_DIRECTION_NOT_ALTERNATING",
        "SEGMENT_SOURCE_ENDPOINT_NOT_CONTIGUOUS",
        "SEGMENT_SOURCE_ENDPOINT_PRICE_NOT_CONTIGUOUS",
    }
    _assert_confirmed_chain(engine._strokes)
