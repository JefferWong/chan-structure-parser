"""
笔构建引擎。

基于分型序列构建笔，满足严格笔的所有条件。
"""

from __future__ import annotations

from typing import Optional

from ..domain.fractal import Fractal
from ..domain.lifecycle import (
    EventType,
    FractalType,
    LifecycleEvent,
    StructureStatus,
    StrokeDirection,
)
from ..domain.merged_bar import MergedBar
from ..domain.stroke import Stroke


class StrokeEngine:
    """笔构建引擎。

    算法：
    1. 从分型序列中选取交替的顶底分型
    2. 验证笔的条件（最少K线数、端点极值等）
    3. 输出笔序列，未完成尾部标记为 PROVISIONAL
    """

    def __init__(self, config: dict):
        """
        Args:
            config: stroke 配置节（来自 profile）
        """
        self.mode = config.get("mode", "strict")
        self.alternating_required = config.get("alternating_fractals_required", True)
        self.min_merged_bars = config.get("minimum_merged_bar_count", 5)
        self.endpoint_extreme_required = config.get("endpoint_extreme_required", True)
        self.allow_unconfirmed_tail = config.get("allow_unconfirmed_tail", True)
        self.rule_profile = "minimal_strict_v1"
        self.rule_version = "1.0.0"

    def process(
        self,
        fractals: list[Fractal],
        merged_bars: list[MergedBar],
        raw_bar_count: int,
    ) -> tuple[list[Stroke], list[LifecycleEvent]]:
        """从分型序列构建笔。

        Args:
            fractals: 分型列表（已处理连续同类型合并）
            merged_bars: 合并K线列表
            raw_bar_count: 原始K线总数

        Returns:
            (strokes, events): 笔列表, 事件列表
        """
        if len(fractals) < 2:
            return [], []

        events: list[LifecycleEvent] = []
        strokes: list[Stroke] = []
        stroke_counter = 1

        # 找到第一个有效的分型对
        i = 0
        while i < len(fractals) - 1:
            f1 = fractals[i]
            f2 = self._find_next_opposite(fractals, i)

            if f2 is None:
                break

            # 验证笔的条件
            can_form, reason = self._can_form_stroke(f1, f2, merged_bars)

            if can_form:
                stroke = self._create_stroke(f1, f2, merged_bars, stroke_counter)
                stroke_counter += 1

                # 检查端点极值条件
                if self.endpoint_extreme_required:
                    extreme_ok, extreme_reason = self._check_endpoint_extreme(
                        stroke, merged_bars
                    )
                    if not extreme_ok:
                        stroke.confirmation_requirements.append(extreme_reason)
                        stroke.repaint_risk = "MEDIUM"

                strokes.append(stroke)

                # 标记已确认
                if len(strokes) >= 2:
                    strokes[-2].mark_confirmed(strokes[-2].end_bar_index)
                    strokes[-2].repaint_risk = "NONE"
                    strokes[-2].confirmation_requirements = []

                # 跳到f2的索引继续
                i = fractals.index(f2)
            else:
                # 不能形成笔，跳过当前分型
                i += 1

        # 最后一笔标记为 PROVISIONAL
        if strokes and self.allow_unconfirmed_tail:
            last = strokes[-1]
            if last.status == StructureStatus.CANDIDATE:
                last.status = StructureStatus.PROVISIONAL
                last.repaint_risk = "HIGH"
                last.confirmation_requirements.append(
                    "waiting for next opposite fractal to confirm"
                )

        return strokes, events

    def _find_next_opposite(
        self, fractals: list[Fractal], start_idx: int
    ) -> Optional[Fractal]:
        """找到start_idx之后第一个相反类型的分型。"""
        if start_idx >= len(fractals):
            return None
        target_type = fractals[start_idx].fractal_type
        for j in range(start_idx + 1, len(fractals)):
            if fractals[j].fractal_type != target_type:
                return fractals[j]
        return None

    def _can_form_stroke(
        self,
        f1: Fractal,
        f2: Fractal,
        merged_bars: list[MergedBar],
    ) -> tuple[bool, str]:
        """检查两个分型是否能构成有效笔。

        Returns:
            (can_form, reason)
        """
        # 检查类型是否交替
        if self.alternating_required and f1.fractal_type == f2.fractal_type:
            return False, "fractal types not alternating"

        # 检查K线数
        start_idx = f1.merged_bar_index
        end_idx = f2.merged_bar_index
        bar_count = end_idx - start_idx + 1

        if bar_count < self.min_merged_bars:
            return False, (
                f"insufficient merged bars: {bar_count} < {self.min_merged_bars}"
            )

        # 严格模式下：顶分型必须高于底分型，底分型必须低于顶分型
        if self.mode == "strict":
            if f1.fractal_type == FractalType.BOTTOM and f2.fractal_type == FractalType.TOP:
                if f1.price >= f2.price:
                    return False, "bottom fractal price >= top fractal price in UP stroke"
            elif f1.fractal_type == FractalType.TOP and f2.fractal_type == FractalType.BOTTOM:
                if f1.price <= f2.price:
                    return False, "top fractal price <= bottom fractal price in DOWN stroke"

        return True, ""

    def _check_endpoint_extreme(
        self, stroke: Stroke, merged_bars: list[MergedBar]
    ) -> tuple[bool, str]:
        """检查笔的端点是否为笔内极值。

        向上笔：终点（顶分型）高点 >= 笔内所有K线高点
        向下笔：终点（底分型）低点 <= 笔内所有K线低点
        """
        start = stroke.start_bar_index
        end = stroke.end_bar_index

        if start < 0 or end >= len(merged_bars) or end <= start:
            return True, ""

        bars_in_stroke = merged_bars[start : end + 1]

        if stroke.direction == StrokeDirection.UP:
            max_high = max(b.high for b in bars_in_stroke)
            if stroke.end_price < max_high:
                return False, (
                    f"end price {stroke.end_price} < max high {max_high} in stroke"
                )
        else:
            min_low = min(b.low for b in bars_in_stroke)
            if stroke.end_price > min_low:
                return False, (
                    f"end price {stroke.end_price} > min low {min_low} in stroke"
                )

        return True, ""

    def _create_stroke(
        self,
        f1: Fractal,
        f2: Fractal,
        merged_bars: list[MergedBar],
        counter: int,
    ) -> Stroke:
        """创建笔对象。"""
        if f1.fractal_type == FractalType.BOTTOM:
            direction = StrokeDirection.UP
        else:
            direction = StrokeDirection.DOWN

        start_idx = f1.merged_bar_index
        end_idx = f2.merged_bar_index
        bar_count = end_idx - start_idx + 1

        bars_in_stroke = merged_bars[start_idx : end_idx + 1]
        max_price = max(b.high for b in bars_in_stroke)
        min_price = min(b.low for b in bars_in_stroke)

        if direction == StrokeDirection.UP:
            price_range = f2.price - f1.price
        else:
            price_range = f1.price - f2.price

        stroke = Stroke(
            stroke_id=f"stroke_{counter:06d}",
            direction=direction,
            start_fractal_id=f1.fractal_id,
            end_fractal_id=f2.fractal_id,
            start_price=f1.price,
            end_price=f2.price,
            start_bar_index=start_idx,
            end_bar_index=end_idx,
            merged_bar_count=bar_count,
            max_price=max_price,
            min_price=min_price,
            price_range=price_range,
            status=StructureStatus.CANDIDATE,
            created_at_bar=end_idx,
            repaint_risk="LOW",
            confirmation_requirements=[
                "next opposite fractal must confirm this stroke"
            ],
            rule_profile=self.rule_profile,
            rule_version=self.rule_version,
        )
        stroke.logical_id = f"stroke:{f1.fractal_id}->{f2.fractal_id}"
        stroke.object_id = stroke.stroke_id
        return stroke
