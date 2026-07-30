"""Phase 2 contract must remain disconnected from Phase 1 parser output."""
from datetime import datetime, timedelta
from pathlib import Path
import random

import yaml

from chan_parser.domain.raw_bar import RawBar
from chan_parser.engine.full_rebuild import FullRebuildEngine
from chan_parser.engine.incremental import IncrementalEngine


PHASE1_PROFILE_PATH = (
    Path(__file__).resolve().parents[2]
    / "configs"
    / "profiles"
    / "minimal_strict_v1.yaml"
)


def phase1_profile() -> dict:
    with PHASE1_PROFILE_PATH.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def bars(count: int = 80, seed: int = 23) -> list[RawBar]:
    rng = random.Random(seed)
    price = 100.0
    start = datetime(2024, 1, 2, 9, 30)
    result = []
    for index in range(count):
        delta = rng.gauss(0, 1.3)
        open_price = price
        close_price = price + delta
        high = max(open_price, close_price) + abs(rng.gauss(0, 0.6))
        low = min(open_price, close_price) - abs(rng.gauss(0, 0.6))
        result.append(
            RawBar(
                f"bar_{index + 1:06d}",
                index,
                start + timedelta(minutes=30 * index),
                round(open_price, 4),
                round(high, 4),
                round(low, 4),
                round(close_price, 4),
            )
        )
        price = close_price
    return result


def test_full_rebuild_output_has_no_segments_key():
    result = FullRebuildEngine(phase1_profile()).process(bars())
    assert "segments" not in result["structures"]
    assert set(result["structures"]) == {"merged_bars", "fractals", "strokes"}


def test_incremental_output_has_no_segments_key():
    engine = IncrementalEngine(phase1_profile())
    data = bars()
    for start in range(0, len(data), 20):
        result = engine.append_batch(data[start:start + 20])
    assert "segments" not in result["structures"]
    assert set(result["structures"]) == {"merged_bars", "fractals", "strokes"}
