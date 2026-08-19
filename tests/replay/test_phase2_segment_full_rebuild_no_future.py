"""Real Segment reference replay must not backfill from future raw bars."""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path
import yaml

from chan_parser.domain.raw_bar import RawBar
from chan_parser.engine.full_rebuild import FullRebuildEngine

ROOT = Path(__file__).resolve().parents[2]


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


def test_raw_visibility_axis_mismatch_is_explicitly_fail_closed():
    bars = real_bars()
    full = segment_engine().process(bars)
    assert full["structures"]["segments"]
    for expected in full["structures"]["segments"]:
        confirmation = expected["confirmed_at_bar"]
        before = segment_engine().process(bars[:confirmation])
        at = segment_engine().process(bars[:confirmation + 1])
        later = segment_engine().process(bars)
        assert not any(item["segment_id"] == expected["segment_id"]
                       for item in before["structures"]["segments"])
        assert not any(item["segment_id"] == expected["segment_id"]
                       for item in at["structures"]["segments"])
        first_visible = segment_engine().process(bars[:62])
        assert any(item["segment_id"] == expected["segment_id"]
                   for item in first_visible["structures"]["segments"])
        assert confirmation == 46
        assert 62 - 1 != confirmation
        later_segment = next(item for item in later["structures"]["segments"]
                             if item["segment_id"] == expected["segment_id"])
        assert later_segment == expected
        for event in later["events"]:
            if (event["object_type"] == "segment"
                    and event["object_id"] == expected["object_id"]):
                assert event["occurred_at_bar_id"] == f"bar_{confirmation + 1:06d}"
