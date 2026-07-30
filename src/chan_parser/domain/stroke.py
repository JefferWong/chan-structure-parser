"""
笔（Stroke）领域对象。

由相邻的顶底分型构成，是走势分解的基本单元。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from .lifecycle import StructureObject, StructureStatus, StrokeDirection


@dataclass
class Stroke(StructureObject):
    """一根笔，连接一个顶分型和一个底分型。"""

    stroke_id: str = ""                      # 如 "stroke_000017"
    direction: StrokeDirection = StrokeDirection.UP

    # 端点信息
    start_fractal_id: str = ""               # 起始分型ID
    end_fractal_id: str = ""                 # 结束分型ID
    start_price: float = 0.0
    end_price: float = 0.0
    start_bar_index: int = -1
    end_bar_index: int = -1

    # 笔内信息
    merged_bar_count: int = 0                # 笔内含有的合并K线数
    max_price: float = 0.0                   # 笔内最高价
    min_price: float = 0.0                   # 笔内最低价
    price_range: float = 0.0                 # 涨跌幅

    # 确认条件
    confirmation_requirements: list[str] = field(default_factory=list)
    repaint_risk: str = "NONE"               # NONE / LOW / MEDIUM / HIGH

    def to_dict(self) -> dict:
        return {
            "object_id": self.object_id,
            "logical_id": self.logical_id,
            "revision": self.revision,
            "status": self.status.value,
            "stroke_id": self.stroke_id,
            "direction": self.direction.value,
            "start_fractal_id": self.start_fractal_id,
            "end_fractal_id": self.end_fractal_id,
            "start_price": self.start_price,
            "end_price": self.end_price,
            "start_bar_index": self.start_bar_index,
            "end_bar_index": self.end_bar_index,
            "merged_bar_count": self.merged_bar_count,
            "max_price": self.max_price,
            "min_price": self.min_price,
            "price_range": self.price_range,
            "repaint_risk": self.repaint_risk,
            "confirmation_requirements": self.confirmation_requirements,
            "created_at_bar": self.created_at_bar,
            "confirmed_at_bar": self.confirmed_at_bar,
            "rule_profile": self.rule_profile,
            "rule_version": self.rule_version,
        }

    def content_hash(self) -> str:
        payload = (
            f"{self.stroke_id}|{self.direction.value}|{self.start_fractal_id}|"
            f"{self.end_fractal_id}|{self.start_price}|{self.end_price}|"
            f"{self.start_bar_index}|{self.end_bar_index}"
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]
