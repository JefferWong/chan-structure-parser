"""分型识别引擎，支持全量和带全局索引的有界窗口。"""
from __future__ import annotations

from ..domain.fractal import Fractal
from ..domain.lifecycle import EventType, FractalType, LifecycleEvent, StructureStatus
from ..domain.merged_bar import MergedBar


class FractalEngine:
    def __init__(self, config: dict):
        self.window_size = config.get("window_size", 3)
        self.allow_equal_high = config.get("allow_equal_high", False)
        self.allow_equal_low = config.get("allow_equal_low", False)
        self.use_merged_bars = config.get("use_merged_bars", True)
        self.minimum_distance = config.get("minimum_distance", 1)
        self.rule_profile = "minimal_strict_v1"
        self.rule_version = "1.0.0"

    def process(
        self,
        merged_bars: list[MergedBar],
        raw_bar_count: int,
        *,
        id_offset: int = 0,
    ) -> tuple[list[Fractal], list[LifecycleEvent]]:
        if len(merged_bars) < self.window_size:
            return [], []
        candidates: list[Fractal] = []
        events: list[LifecycleEvent] = []
        for i in range(1, len(merged_bars) - 1):
            left, mid, right = merged_bars[i - 1], merged_bars[i], merged_bars[i + 1]
            types = []
            if self._check_top_fractal(left, mid, right):
                types.append((FractalType.TOP, mid.high))
            if self._check_bottom_fractal(left, mid, right):
                types.append((FractalType.BOTTOM, mid.low))
            for fx_type, price in types:
                type_code = "T" if fx_type == FractalType.TOP else "B"
                fractal_id = f"fx_{mid.bar_index + 1:06d}_{type_code}"
                fx = Fractal(
                    fractal_id=fractal_id,
                    fractal_type=fx_type,
                    merged_bar_id=mid.bar_id,
                    merged_bar_index=mid.bar_index,
                    price=price,
                    left_bar_id=left.bar_id,
                    right_bar_id=right.bar_id,
                    window_indices=[left.bar_index, mid.bar_index, right.bar_index],
                    object_id=f"{fractal_id}_r1",
                    logical_id=f"fractal:{fx_type.value.lower()}:{mid.logical_id or mid.bar_id}",
                    revision=1,
                    status=StructureStatus.CANDIDATE,
                    created_at_bar=right.bar_index,
                    repaint_risk="LOW",
                    confirmation_requirements=["selection against adjacent same-type fractals"],
                    rule_profile=self.rule_profile,
                    rule_version=self.rule_version,
                )
                candidates.append(fx)
                events.append(self._event(EventType.CREATED, fx, right.bar_id, "THREE_BAR_PATTERN_DETECTED"))

        selected: list[Fractal] = []
        for fx in candidates:
            if not selected or selected[-1].fractal_type != fx.fractal_type:
                selected.append(fx)
                continue
            previous = selected[-1]
            new_wins = (fx.price > previous.price if fx.fractal_type == FractalType.TOP
                        else fx.price < previous.price)
            loser, winner = (previous, fx) if new_wins else (fx, previous)
            loser.mark_replaced(winner.object_id)
            events.append(LifecycleEvent(
                event_type=EventType.STRUCTURE_REPLACED,
                object_type="fractal",
                object_id=loser.object_id,
                logical_id=loser.logical_id,
                occurred_at_bar_id=fx.right_bar_id,
                reason_code="SAME_TYPE_MORE_EXTREME",
                replaced_by=winner.object_id,
                rule_profile=self.rule_profile,
                rule_version=self.rule_version,
                detail={"winner_price": winner.price, "loser_price": loser.price},
            ))
            if new_wins:
                selected[-1] = fx

        for idx, fx in enumerate(selected):
            if idx < len(selected) - 1:
                fx.mark_confirmed(fx.window_indices[-1])
                fx.repaint_risk = "NONE"
                fx.confirmation_requirements = []
                events.append(self._event(EventType.CONFIRMED, fx, fx.right_bar_id, "NEXT_ACTIVE_FRACTAL_OBSERVED"))
            else:
                fx.status = StructureStatus.PROVISIONAL
                fx.repaint_risk = "HIGH"
                fx.confirmation_requirements = ["waiting for next active opposite fractal"]
                events.append(self._event(EventType.STATUS_CHANGED, fx, fx.right_bar_id, "TAIL_FRACTAL_PROVISIONAL"))
        return selected, events

    def _event(self, event_type: str, fx: Fractal, bar_id: str, reason: str) -> LifecycleEvent:
        return LifecycleEvent(
            event_type=event_type,
            object_type="fractal",
            object_id=fx.object_id,
            logical_id=fx.logical_id,
            occurred_at_bar_id=bar_id,
            reason_code=reason,
            rule_profile=self.rule_profile,
            rule_version=self.rule_version,
            detail={"status": fx.status.value, "merged_bar_index": fx.merged_bar_index,
                    "fractal_type": fx.fractal_type.value, "price": fx.price},
        )

    def _check_top_fractal(self, left: MergedBar, mid: MergedBar, right: MergedBar) -> bool:
        if self.allow_equal_high:
            return ((mid.high >= left.high and mid.high > right.high and mid.low >= left.low and mid.low > right.low)
                    or (mid.high > left.high and mid.high >= right.high and mid.low > left.low and mid.low >= right.low))
        return mid.high > left.high and mid.high > right.high

    def _check_bottom_fractal(self, left: MergedBar, mid: MergedBar, right: MergedBar) -> bool:
        if self.allow_equal_low:
            return ((mid.low <= left.low and mid.low < right.low and mid.high <= left.high and mid.high < right.high)
                    or (mid.low < left.low and mid.low <= right.low and mid.high < left.high and mid.high <= right.high))
        return mid.low < left.low and mid.low < right.low
