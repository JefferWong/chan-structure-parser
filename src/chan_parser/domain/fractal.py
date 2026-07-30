"""
分型（Fractal）领域对象。

基于合并K线识别顶分型和底分型。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from .lifecycle import StructureObject, StructureStatus, FractalType


@dataclass
class Fractal(StructureObject):
    """一个顶分型或底分型。"""

    fractal_id: str = ""                     # 如 "fx_000031"
    fractal_type: FractalType = FractalType.TOP

    # 定位信息
    merged_bar_id: str = ""                  # 分型所在合并K线ID
    merged_bar_index: int = -1               # 分型所在合并K线索引
    price: float = 0.0                       # 分型价格（顶=高点，底=低点）

    # 窗口信息
    left_bar_id: str = ""                    # 左侧确认K线
    right_bar_id: str = ""                   # 右侧确认K线
    window_indices: list[int] = field(default_factory=list)  # 窗口内合并K线索引

    # 确认条件
    confirmation_requirements: list[str] = field(default_factory=list)
    repaint_risk: str = "NONE"               # NONE / LOW / MEDIUM / HIGH

    def to_dict(self) -> dict:
        return {
            "object_id": self.object_id,
            "logical_id": self.logical_id,
            "revision": self.revision,
            "status": self.status.value,
            "fractal_id": self.fractal_id,
            "fractal_type": self.fractal_type.value,
            "merged_bar_id": self.merged_bar_id,
            "merged_bar_index": self.merged_bar_index,
            "price": self.price,
            "left_bar_id": self.left_bar_id,
            "right_bar_id": self.right_bar_id,
            "window_indices": self.window_indices,
            "repaint_risk": self.repaint_risk,
            "confirmation_requirements": self.confirmation_requirements,
            "created_at_bar": self.created_at_bar,
            "confirmed_at_bar": self.confirmed_at_bar,
            "rule_profile": self.rule_profile,
            "rule_version": self.rule_version,
        }

    def content_hash(self) -> str:
        payload = (
            f"{self.fractal_id}|{self.fractal_type.value}|{self.merged_bar_id}|"
            f"{self.merged_bar_index}|{self.price}"
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]
