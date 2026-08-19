"""
合并K线（Merged Bar）领域对象。

经包含关系处理后的标准化K线，一根合并K线可能由多根原始K线合并而成。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .lifecycle import StructureObject, StructureStatus


@dataclass
class MergedBar(StructureObject):
    """一根经包含处理后的合并K线。"""

    bar_id: str = ""                         # 如 "mbar_000042"
    bar_index: int = -1                      # 在合并K线序列中的索引

    # OHLC 数据
    timestamp: Optional[datetime] = None     # 取组成K线中最晚的时间戳
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0

    # 谱系追踪
    source_raw_bar_ids: list[str] = field(default_factory=list)
    # 组成此合并K线的原始K线ID列表
    source_raw_bar_indices: list[int] = field(default_factory=list)
    # 原始K线索引，与 source_raw_bar_ids 保持位置一一对应
    visible_at_raw_bar_index: int = -1
    # 该合并K线最后一根原始K线变得可见的 raw 轴位置
    merge_direction: str = ""                # 合并时的趋势方向: "UP" / "DOWN"

    def to_dict(self) -> dict:
        return {
            "object_id": self.object_id,
            "logical_id": self.logical_id,
            "revision": self.revision,
            "status": self.status.value,
            "bar_id": self.bar_id,
            "bar_index": self.bar_index,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "source_raw_bar_ids": self.source_raw_bar_ids,
            "merge_direction": self.merge_direction,
            "created_at_bar": self.created_at_bar,
            "confirmed_at_bar": self.confirmed_at_bar,
            "rule_profile": self.rule_profile,
            "rule_version": self.rule_version,
        }

    def content_hash(self) -> str:
        """确定性内容哈希。"""
        payload = (
            f"{self.bar_id}|{self.open}|{self.high}|{self.low}|{self.close}|"
            f"{'|'.join(sorted(self.source_raw_bar_ids))}|{self.merge_direction}"
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def includes_raw_bar(self, raw_bar_id: str) -> bool:
        """检查此合并K线是否包含指定的原始K线。"""
        return raw_bar_id in self.source_raw_bar_ids
