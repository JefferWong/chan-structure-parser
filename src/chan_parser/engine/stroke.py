"""严格笔状态机，支持全量和带合并K线全局偏移的有界窗口。"""
from __future__ import annotations

from ..domain.fractal import Fractal
from ..domain.lifecycle import EventType, FractalType, LifecycleEvent, StructureStatus, StrokeDirection
from ..domain.merged_bar import MergedBar
from ..domain.stroke import Stroke


class StrokeEngine:
    def __init__(self, config: dict):
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
        *,
        bar_index_offset: int = 0,
        id_offset: int = 0,
    ) -> tuple[list[Stroke], list[LifecycleEvent]]:
        if len(fractals) < 2:
            return [], []
        strokes: list[Stroke] = []
        events: list[LifecycleEvent] = []
        anchor = fractals[0]
        counter = id_offset + 1
        for candidate in fractals[1:]:
            if candidate.fractal_type == anchor.fractal_type:
                anchor = self._more_extreme(anchor, candidate)
                continue
            valid, reason, detail = self._validate(anchor, candidate, merged_bars, bar_index_offset)
            if not valid:
                events.append(LifecycleEvent(
                    event_type=EventType.CANDIDATE_REJECTED,
                    object_type="stroke_candidate",
                    object_id=f"stroke_candidate:{anchor.object_id}->{candidate.object_id}",
                    logical_id=f"stroke:{anchor.logical_id}->{candidate.logical_id}",
                    occurred_at_bar_id=candidate.right_bar_id or candidate.merged_bar_id,
                    reason_code=reason,
                    rule_profile=self.rule_profile,
                    rule_version=self.rule_version,
                    detail=detail,
                ))
                continue
            stroke = self._create_stroke(anchor, candidate, merged_bars, counter, bar_index_offset)
            counter += 1
            events.append(LifecycleEvent(
                event_type=EventType.CREATED,
                object_type="stroke",
                object_id=stroke.object_id,
                logical_id=stroke.logical_id,
                occurred_at_bar_id=candidate.right_bar_id or candidate.merged_bar_id,
                reason_code="STRICT_STROKE_VALID",
                rule_profile=self.rule_profile,
                rule_version=self.rule_version,
                detail={"start_bar_index": stroke.start_bar_index,
                        "end_bar_index": stroke.end_bar_index,
                        "direction": stroke.direction.value},
            ))
            if strokes:
                previous = strokes[-1]
                if previous.end_fractal_id == stroke.start_fractal_id and previous.status == StructureStatus.PROVISIONAL:
                    previous.mark_confirmed(stroke.end_bar_index)
                    previous.repaint_risk = "NONE"
                    previous.confirmation_requirements = []
                    events.append(LifecycleEvent(
                        event_type=EventType.CONFIRMED,
                        object_type="stroke",
                        object_id=previous.object_id,
                        logical_id=previous.logical_id,
                        occurred_at_bar_id=candidate.right_bar_id or candidate.merged_bar_id,
                        reason_code="NEXT_STRICT_STROKE_CONFIRMED",
                        rule_profile=self.rule_profile,
                        rule_version=self.rule_version,
                    ))
            strokes.append(stroke)
            anchor = candidate
        if strokes and not self.allow_unconfirmed_tail:
            strokes = [s for s in strokes if s.status == StructureStatus.CONFIRMED]
        return strokes, events

    @staticmethod
    def _more_extreme(a: Fractal, b: Fractal) -> Fractal:
        if a.fractal_type == FractalType.TOP:
            return b if b.price > a.price else a
        return b if b.price < a.price else a

    def _validate(
        self,
        f1: Fractal,
        f2: Fractal,
        bars: list[MergedBar],
        bar_index_offset: int,
    ) -> tuple[bool, str, dict]:
        if self.alternating_required and f1.fractal_type == f2.fractal_type:
            return False, "SAME_TYPE_ENDPOINTS", {}
        start, end = f1.merged_bar_index, f2.merged_bar_index
        count = end - start + 1
        if count < self.min_merged_bars:
            return False, "INSUFFICIENT_MERGED_BARS", {"actual": count, "required": self.min_merged_bars}
        local_start, local_end = start - bar_index_offset, end - bar_index_offset
        if local_start < 0 or local_end >= len(bars) or local_end <= local_start:
            return False, "INVALID_BAR_RANGE", {
                "start": start, "end": end, "bar_index_offset": bar_index_offset,
                "available": len(bars),
            }
        direction = StrokeDirection.UP if f1.fractal_type == FractalType.BOTTOM else StrokeDirection.DOWN
        if direction == StrokeDirection.UP and f1.price >= f2.price:
            return False, "NON_POSITIVE_UP_RANGE", {"start_price": f1.price, "end_price": f2.price}
        if direction == StrokeDirection.DOWN and f1.price <= f2.price:
            return False, "NON_POSITIVE_DOWN_RANGE", {"start_price": f1.price, "end_price": f2.price}
        interval = bars[local_start:local_end + 1]
        if self.endpoint_extreme_required:
            max_high, min_low = max(b.high for b in interval), min(b.low for b in interval)
            if direction == StrokeDirection.UP and (f1.price > min_low or f2.price < max_high):
                return False, "ENDPOINT_NOT_INTERVAL_EXTREME", {"min_low": min_low, "max_high": max_high}
            if direction == StrokeDirection.DOWN and (f1.price < max_high or f2.price > min_low):
                return False, "ENDPOINT_NOT_INTERVAL_EXTREME", {"min_low": min_low, "max_high": max_high}
        return True, "", {}

    def _create_stroke(
        self,
        f1: Fractal,
        f2: Fractal,
        bars: list[MergedBar],
        counter: int,
        bar_index_offset: int,
    ) -> Stroke:
        direction = StrokeDirection.UP if f1.fractal_type == FractalType.BOTTOM else StrokeDirection.DOWN
        local_start = f1.merged_bar_index - bar_index_offset
        local_end = f2.merged_bar_index - bar_index_offset
        interval = bars[local_start:local_end + 1]
        return Stroke(
            stroke_id=f"stroke_{counter:06d}",
            direction=direction,
            start_fractal_id=f1.fractal_id,
            end_fractal_id=f2.fractal_id,
            start_price=f1.price,
            end_price=f2.price,
            start_bar_index=f1.merged_bar_index,
            end_bar_index=f2.merged_bar_index,
            merged_bar_count=len(interval),
            max_price=max(b.high for b in interval),
            min_price=min(b.low for b in interval),
            price_range=abs(f2.price - f1.price),
            object_id=f"stroke_{counter:06d}_r1",
            logical_id=f"stroke:{f1.logical_id}->{f2.logical_id}",
            revision=1,
            status=StructureStatus.PROVISIONAL,
            created_at_bar=f2.window_indices[-1],
            repaint_risk="HIGH",
            confirmation_requirements=["next strict stroke must confirm"],
            rule_profile=self.rule_profile,
            rule_version=self.rule_version,
        )
