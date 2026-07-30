"""
全量重建引擎。

从原始K线开始，一次性全量处理，输出完整结构。
用于验证增量计算结果的一致性。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..domain.raw_bar import RawBar
from ..domain.merged_bar import MergedBar
from ..domain.fractal import Fractal
from ..domain.stroke import Stroke
from ..domain.lifecycle import LifecycleEvent, StructureStatus
from .inclusion import InclusionEngine
from .fractal import FractalEngine
from .stroke import StrokeEngine


class FullRebuildEngine:
    """全量重建引擎。

    一次性处理所有原始K线，输出完整结构。
    结果用作增量引擎的一致性对照基准。
    """

    def __init__(self, profile: dict):
        self.profile = profile
        self.inclusion_engine = InclusionEngine(profile.get("inclusion", {}))
        self.fractal_engine = FractalEngine(profile.get("fractal", {}))
        self.stroke_engine = StrokeEngine(profile.get("stroke", {}))
        self.engine_version = "0.1.0"

    def process(self, raw_bars: list[RawBar]) -> dict[str, Any]:
        """全量处理。

        Returns:
            包含 meta, data_quality, structures, audit 的完整输出字典
        """
        valid_bars = [b for b in raw_bars if b.is_valid]
        raw_count = len(raw_bars)
        valid_count = len(valid_bars)

        # Step 1: 包含处理
        merged_bars, inc_events = self.inclusion_engine.process(valid_bars)

        # Step 2: 分型识别
        fractals, fx_events = self.fractal_engine.process(merged_bars, raw_count)

        # Step 3: 笔构建
        strokes, st_events = self.stroke_engine.process(fractals, merged_bars, raw_count)

        # 合并所有事件
        all_events = inc_events + fx_events + st_events

        # 构建输出
        output = {
            "meta": {
                "symbol": "",
                "bar_frequency": "",
                "adjustment": "qfq",
                "profile_id": self.profile.get("profile_id", "minimal_strict_v1"),
                "engine_version": self.engine_version,
                "analysis_mode": "close_only",
                "calculation_mode": "full_rebuild",
            },
            "data_quality": {
                "raw_bar_count": raw_count,
                "valid_bar_count": valid_count,
                "duplicate_count": 0,
                "missing_interval_count": 0,
                "monotonic_timestamp": True,
                "status": "OK" if valid_count == raw_count else "WARNING",
            },
            "structures": {
                "merged_bars": [mb.to_dict() for mb in merged_bars],
                "fractals": [f.to_dict() for f in fractals],
                "strokes": [s.to_dict() for s in strokes],
            },
            "runtime_state": {
                "last_processed_bar_id": valid_bars[-1].bar_id if valid_bars else "",
                "unfinished_fractal_count": sum(
                    1 for f in fractals
                    if f.status in (StructureStatus.CANDIDATE, StructureStatus.PROVISIONAL)
                ),
                "unfinished_stroke_count": sum(
                    1 for s in strokes
                    if s.status in (StructureStatus.CANDIDATE, StructureStatus.PROVISIONAL)
                ),
            },
            "audit": {
                "event_log_sha256": self._hash_events(all_events),
                "output_sha256": self._hash_structures(merged_bars, fractals, strokes),
            },
            "events": [self._event_to_dict(e) for e in all_events],
        }

        return output

    def _hash_events(self, events: list[LifecycleEvent]) -> str:
        """计算事件日志的确定性哈希。"""
        payload = "|".join(
            f"{e.event_id}|{e.event_type}|{e.object_type}|{e.object_id}|{e.reason_code}"
            for e in events
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def _hash_structures(
        self,
        merged_bars: list[MergedBar],
        fractals: list[Fractal],
        strokes: list[Stroke],
    ) -> str:
        """计算已确认结构的确定性哈希。"""
        confirmed_mb = [mb.content_hash() for mb in merged_bars if mb.is_confirmed()]
        confirmed_fx = [f.content_hash() for f in fractals if f.is_confirmed()]
        confirmed_st = [s.content_hash() for s in strokes if s.is_confirmed()]
        payload = "|".join(confirmed_mb + confirmed_fx + confirmed_st)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def _event_to_dict(self, event: LifecycleEvent) -> dict:
        return {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "object_type": event.object_type,
            "object_id": event.object_id,
            "logical_id": event.logical_id,
            "occurred_at_bar_id": event.occurred_at_bar_id,
            "reason_code": event.reason_code,
            "replaced_by": event.replaced_by,
            "rule_profile": event.rule_profile,
            "rule_version": event.rule_version,
            "detail": event.detail,
        }
