"""独立全量重建基准引擎。"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from ..audit.event_log import EventLog
from ..domain.lifecycle import StructureStatus
from ..domain.raw_bar import RawBar
from .fractal import FractalEngine
from .inclusion import InclusionEngine
from .stroke import StrokeEngine


class FullRebuildEngine:
    def __init__(self, profile: dict):
        self.profile = profile
        self.inclusion_engine = InclusionEngine(profile.get("inclusion", {}))
        self.fractal_engine = FractalEngine(profile.get("fractal", {}))
        self.stroke_engine = StrokeEngine(profile.get("stroke", {}))
        self.engine_version = "0.2.0"

    def process(self, raw_bars: list[RawBar]) -> dict[str, Any]:
        valid = [b for b in raw_bars if b.is_valid]
        merged, inc_events = self.inclusion_engine.process(valid)
        fractals, fx_events = self.fractal_engine.process(merged, len(raw_bars))
        strokes, st_events = self.stroke_engine.process(fractals, merged, len(raw_bars))
        log = EventLog(); log.record_many(inc_events + fx_events + st_events)
        quality = self._data_quality(raw_bars)
        return {
            "meta": {"symbol": "", "bar_frequency": "", "adjustment": "qfq",
                     "profile_id": self.profile.get("profile_id", "minimal_strict_v1"),
                     "engine_version": self.engine_version, "analysis_mode": "close_only",
                     "calculation_mode": "full_rebuild"},
            "data_quality": quality,
            "structures": {"merged_bars": [x.to_dict() for x in merged],
                           "fractals": [x.to_dict() for x in fractals],
                           "strokes": [x.to_dict() for x in strokes]},
            "runtime_state": {"last_processed_bar_id": valid[-1].bar_id if valid else "",
                              "unfinished_fractal_count": sum(x.status != StructureStatus.CONFIRMED for x in fractals),
                              "unfinished_stroke_count": sum(x.status != StructureStatus.CONFIRMED for x in strokes)},
            "audit": {"input_sha256": self._input_hash(raw_bars),
                      "event_log_sha256": log.compute_sha256(),
                      "output_sha256": self._structure_hash(merged, fractals, strokes),
                      "event_count": len(log)},
            "events": log.to_list(),
        }

    @staticmethod
    def _input_hash(bars: list[RawBar]) -> str:
        return hashlib.sha256("|".join(b.content_hash() for b in bars).encode()).hexdigest()[:16]

    @staticmethod
    def _structure_hash(merged, fractals, strokes) -> str:
        payload = [x.content_hash() for x in merged]
        payload += [x.content_hash() for x in fractals]
        payload += [x.content_hash() for x in strokes]
        return hashlib.sha256("|".join(payload).encode()).hexdigest()[:16]

    @staticmethod
    def _data_quality(bars: list[RawBar]) -> dict:
        timestamps = [b.timestamp for b in bars]
        duplicate_count = len(timestamps) - len(set(timestamps))
        monotonic = all(a < b for a, b in zip(timestamps, timestamps[1:]))
        invalid = sum(not b.is_valid for b in bars)
        status = "OK" if duplicate_count == 0 and monotonic and invalid == 0 else "WARNING"
        return {"raw_bar_count": len(bars), "valid_bar_count": len(bars) - invalid,
                "duplicate_count": duplicate_count, "missing_interval_count": 0,
                "monotonic_timestamp": monotonic, "status": status}
