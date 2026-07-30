"""
分型识别引擎。

基于合并K线序列识别顶分型和底分型。
"""

from __future__ import annotations

from typing import Optional

from ..domain.fractal import Fractal
from ..domain.lifecycle import (
    EventType,
    FractalType,
    LifecycleEvent,
    StructureStatus,
)
from ..domain.merged_bar import MergedBar


class FractalEngine:
    """分型识别引擎。

    算法：
    1. 在合并K线序列上滑动3-K线窗口
    2. 顶分型：中间K线高点严格最高
    3. 底分型：中间K线低点严格最低
    4. 连续同类型分型取最极值
    """

    def __init__(self, config: dict):
        """
        Args:
            config: fractal 配置节（来自 profile）
        """
        self.window_size = config.get("window_size", 3)
        self.allow_equal_high = config.get("allow_equal_high", False)
        self.allow_equal_low = config.get("allow_equal_low", False)
        self.use_merged_bars = config.get("use_merged_bars", True)
        self.minimum_distance = config.get("minimum_distance", 1)
        self.default_status = StructureStatus.CANDIDATE
        self.rule_profile = "minimal_strict_v1"
        self.rule_version = "1.0.0"

    def process(
        self, merged_bars: list[MergedBar], raw_bar_count: int
    ) -> tuple[list[Fractal], list[LifecycleEvent]]:
        """识别分型。

        Args:
            merged_bars: 合并K线序列
            raw_bar_count: 原始K线总数（用于计算 created_at_bar）

        Returns:
            (fractals, events): 分型列表, 事件列表
        """
        if len(merged_bars) < self.window_size:
            return [], []

        events: list[LifecycleEvent] = []
        candidates: list[Fractal] = []
        fx_counter = 1

        # 滑动窗口识别分型候选
        for i in range(1, len(merged_bars) - 1):
            left = merged_bars[i - 1]
            mid = merged_bars[i]
            right = merged_bars[i + 1]

            top_fractal = self._check_top_fractal(left, mid, right)
            bottom_fractal = self._check_bottom_fractal(left, mid, right)

            if top_fractal:
                fractal = Fractal(
                    fractal_id=f"fx_{fx_counter:06d}",
                    fractal_type=FractalType.TOP,
                    merged_bar_id=mid.bar_id,
                    merged_bar_index=i,
                    price=mid.high,
                    left_bar_id=left.bar_id,
                    right_bar_id=right.bar_id,
                    window_indices=[i - 1, i, i + 1],
                    status=StructureStatus.CANDIDATE,
                    created_at_bar=i,
                    repaint_risk="LOW",
                    confirmation_requirements=[
                        "opposite fractal confirmed at lower level"
                    ],
                    rule_profile=self.rule_profile,
                    rule_version=self.rule_version,
                )
                fractal.logical_id = f"fractal:top:{mid.bar_id}"
                candidates.append(fractal)
                fx_counter += 1

            if bottom_fractal:
                fractal = Fractal(
                    fractal_id=f"fx_{fx_counter:06d}",
                    fractal_type=FractalType.BOTTOM,
                    merged_bar_id=mid.bar_id,
                    merged_bar_index=i,
                    price=mid.low,
                    left_bar_id=left.bar_id,
                    right_bar_id=right.bar_id,
                    window_indices=[i - 1, i, i + 1],
                    status=StructureStatus.CANDIDATE,
                    created_at_bar=i,
                    repaint_risk="LOW",
                    confirmation_requirements=[
                        "opposite fractal confirmed at higher level"
                    ],
                    rule_profile=self.rule_profile,
                    rule_version=self.rule_version,
                )
                fractal.logical_id = f"fractal:bottom:{mid.bar_id}"
                candidates.append(fractal)
                fx_counter += 1

        # 后处理：合并连续同类型分型，取最极值
        fractals = self._merge_consecutive_same_type(candidates)

        # 重新编号
        for idx, f in enumerate(fractals):
            f.fractal_id = f"fx_{idx + 1:06d}"
            f.object_id = f.fractal_id

        # 尾部最后一个分型标记为 PROVISIONAL
        if fractals:
            last = fractals[-1]
            if last.status == StructureStatus.CANDIDATE:
                last.status = StructureStatus.PROVISIONAL
                last.repaint_risk = "HIGH"
                last.confirmation_requirements.append("waiting for next opposite fractal")

        return fractals, events

    def _check_top_fractal(
        self, left: MergedBar, mid: MergedBar, right: MergedBar
    ) -> bool:
        """检查是否构成顶分型。"""
        if self.allow_equal_high:
            return (
                mid.high >= left.high
                and mid.high > right.high
                and mid.low >= left.low
                and mid.low > right.low
            ) or (
                mid.high > left.high
                and mid.high >= right.high
                and mid.low > left.low
                and mid.low >= right.low
            )
        else:
            return (
                mid.high > left.high
                and mid.high > right.high
            )

    def _check_bottom_fractal(
        self, left: MergedBar, mid: MergedBar, right: MergedBar
    ) -> bool:
        """检查是否构成底分型。"""
        if self.allow_equal_low:
            return (
                mid.low <= left.low
                and mid.low < right.low
                and mid.high <= left.high
                and mid.high < right.high
            ) or (
                mid.low < left.low
                and mid.low <= right.low
                and mid.high < left.high
                and mid.high <= right.high
            )
        else:
            return (
                mid.low < left.low
                and mid.low < right.low
            )

    def _merge_consecutive_same_type(
        self, candidates: list[Fractal]
    ) -> list[Fractal]:
        """合并连续同类型分型，保留最极值。

        连续顶分型 → 保留高点最高的
        连续底分型 → 保留低点最低的
        """
        if not candidates:
            return []

        result: list[Fractal] = []
        i = 0

        while i < len(candidates):
            current = candidates[i]
            # 收集连续同类型分型
            j = i + 1
            group = [current]
            while j < len(candidates) and candidates[j].fractal_type == current.fractal_type:
                group.append(candidates[j])
                j += 1

            if len(group) == 1:
                result.append(current)
            else:
                # 多个同类型分型，取最极值
                if current.fractal_type == FractalType.TOP:
                    best = max(group, key=lambda f: f.price)
                else:
                    best = min(group, key=lambda f: f.price)
                # 将被合并的分型标记为 REPLACED
                for g in group:
                    if g is not best:
                        g.mark_replaced(best.object_id)
                result.append(best)

            i = j

        return result
