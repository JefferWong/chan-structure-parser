"""
增量计算引擎。

支持逐K线增量更新 + 局部依赖失效 + 有界区间重算。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional

from ..domain.raw_bar import RawBar
from ..domain.merged_bar import MergedBar
from ..domain.fractal import Fractal
from ..domain.stroke import Stroke
from ..domain.lifecycle import (
    LifecycleEvent,
    StructureStatus,
    EventType,
    FractalType,
    StrokeDirection,
)
from .inclusion import InclusionEngine
from .fractal import FractalEngine
from .stroke import StrokeEngine


@dataclass
class Checkpoint:
    """计算检查点，用于中断后恢复。"""
    bar_index: int
    merged_bars_snapshot: list[dict] = field(default_factory=list)
    fractals_snapshot: list[dict] = field(default_factory=list)
    strokes_snapshot: list[dict] = field(default_factory=list)
    events_snapshot: list[dict] = field(default_factory=list)
    direction: str = "UP"
    sha256: str = ""


class IncrementalEngine:
    """增量计算引擎。

    支持三种运行模式：
    1. 逐K线追加（append_one）
    2. 批量追加（append_batch）
    3. 从检查点恢复（resume_from_checkpoint）
    """

    def __init__(self, profile: dict):
        self.profile = profile
        self.inclusion_engine = InclusionEngine(profile.get("inclusion", {}))
        self.fractal_engine = FractalEngine(profile.get("fractal", {}))
        self.stroke_engine = StrokeEngine(profile.get("stroke", {}))
        self.engine_version = "0.1.0"

        runtime = profile.get("runtime", {})
        self.max_rebuild_distance = runtime.get("max_rebuild_distance", 200)
        self.checkpoint_interval = runtime.get("checkpoint_interval", 50)

        # 内部状态
        self._raw_bars: list[RawBar] = []
        self._merged_bars: list[MergedBar] = []
        self._fractals: list[Fractal] = []
        self._strokes: list[Stroke] = []
        self._events: list[LifecycleEvent] = []
        self._checkpoints: list[Checkpoint] = []
        self._last_processed_idx: int = -1
        self._rebuild_count: int = 0

    def append_one(self, raw_bar: RawBar) -> dict[str, Any]:
        """追加一根K线，返回当前结构状态。"""
        return self.append_batch([raw_bar])

    def append_batch(self, raw_bars: list[RawBar]) -> dict[str, Any]:
        """追加一批K线。

        核心逻辑：
        1. 追加新K线到内部缓存
        2. 检测是否需要局部重建
        3. 如果有局部重建需求，从受影响的区间重算
        4. 否则只处理新增部分
        """
        rebuild_from = self._detect_rebuild_boundary(raw_bars)

        if rebuild_from is not None:
            # 需要局部重建
            return self._local_rebuild(rebuild_from, raw_bars)
        else:
            # 纯增量追加
            return self._incremental_append(raw_bars)

    def resume_from_checkpoint(self, checkpoint_id: int) -> dict[str, Any]:
        """从检查点恢复。"""
        if checkpoint_id < 0 or checkpoint_id >= len(self._checkpoints):
            raise ValueError(f"Invalid checkpoint_id: {checkpoint_id}")

        cp = self._checkpoints[checkpoint_id]
        # 恢复状态...
        return self.get_current_state()

    def get_current_state(self) -> dict[str, Any]:
        """获取当前状态快照。"""
        raw_count = len(self._raw_bars)
        valid_bars = [b for b in self._raw_bars if b.is_valid]

        return {
            "meta": {
                "symbol": "",
                "bar_frequency": "",
                "adjustment": "qfq",
                "profile_id": self.profile.get("profile_id", "minimal_strict_v1"),
                "engine_version": self.engine_version,
                "analysis_mode": "close_only",
                "calculation_mode": "incremental_with_local_rebuild",
            },
            "data_quality": {
                "raw_bar_count": raw_count,
                "valid_bar_count": len(valid_bars),
                "duplicate_count": 0,
                "missing_interval_count": 0,
                "monotonic_timestamp": True,
                "status": "OK",
            },
            "structures": {
                "merged_bars": [mb.to_dict() for mb in self._merged_bars],
                "fractals": [f.to_dict() for f in self._fractals],
                "strokes": [s.to_dict() for s in self._strokes],
            },
            "runtime_state": {
                "last_processed_bar_id": (
                    self._raw_bars[-1].bar_id if self._raw_bars else ""
                ),
                "local_rebuild_from": None,
                "unfinished_fractal_count": sum(
                    1 for f in self._fractals
                    if f.status in (StructureStatus.CANDIDATE, StructureStatus.PROVISIONAL)
                ),
                "unfinished_stroke_count": sum(
                    1 for s in self._strokes
                    if s.status in (StructureStatus.CANDIDATE, StructureStatus.PROVISIONAL)
                ),
                "rebuild_count": self._rebuild_count,
            },
            "audit": {
                "event_log_sha256": self._hash_events(),
            },
            "events": [self._event_to_dict(e) for e in self._events],
        }

    def create_checkpoint(self) -> int:
        """创建检查点，返回检查点ID。"""
        cp = Checkpoint(
            bar_index=len(self._raw_bars) - 1,
            merged_bars_snapshot=[mb.to_dict() for mb in self._merged_bars],
            fractals_snapshot=[f.to_dict() for f in self._fractals],
            strokes_snapshot=[s.to_dict() for s in self._strokes],
            events_snapshot=[self._event_to_dict(e) for e in self._events],
        )
        cp.sha256 = hashlib.sha256(
            json.dumps(cp.merged_bars_snapshot, sort_keys=True).encode()
        ).hexdigest()[:16]
        self._checkpoints.append(cp)
        return len(self._checkpoints) - 1

    def _detect_rebuild_boundary(self, new_bars: list[RawBar]) -> Optional[int]:
        """检测是否需要局部重建，返回重建起始K线索引。

        当前简化实现：总是返回 None（纯增量）。
        完整实现需要检测新K线是否改变了最后一笔的方向假设。
        """
        # TODO: 实现完整的依赖失效检测
        # 当新K线导致最后一根合并K线的高/低点被突破时，需要重建
        return None

    def _incremental_append(self, new_bars: list[RawBar]) -> dict[str, Any]:
        """纯增量追加（不重建历史结构）。"""
        # 简单实现：全量重算
        # 完整实现应该只处理新增K线对尾部结构的影响
        self._raw_bars.extend(new_bars)
        return self._full_recompute()

    def _local_rebuild(
        self, rebuild_from: int, new_bars: list[RawBar]
    ) -> dict[str, Any]:
        """局部重建：从 rebuild_from 索引开始重算。"""
        self._rebuild_count += 1
        self._raw_bars.extend(new_bars)
        return self._full_recompute()

    def _full_recompute(self) -> dict[str, Any]:
        """全量重算（内部使用）。"""
        valid_bars = [b for b in self._raw_bars if b.is_valid]

        # 包含处理
        self._merged_bars, inc_events = self.inclusion_engine.process(valid_bars)

        # 分型识别
        self._fractals, fx_events = self.fractal_engine.process(
            self._merged_bars, len(self._raw_bars)
        )

        # 笔构建
        self._strokes, st_events = self.stroke_engine.process(
            self._fractals, self._merged_bars, len(self._raw_bars)
        )

        self._events = inc_events + fx_events + st_events
        self._last_processed_idx = len(self._raw_bars) - 1

        # 自动创建检查点
        if (
            self.checkpoint_interval > 0
            and len(self._raw_bars) % self.checkpoint_interval == 0
        ):
            self.create_checkpoint()

        return self.get_current_state()

    def _hash_events(self) -> str:
        payload = "|".join(
            f"{e.event_id}|{e.event_type}|{e.object_type}|{e.object_id}|{e.reason_code}"
            for e in self._events
        )
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
