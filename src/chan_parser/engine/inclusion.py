"""K线包含处理引擎，支持全量与带方向种子的有界尾部处理。"""
from __future__ import annotations

from ..domain.lifecycle import EventType, LifecycleEvent, StructureStatus, TrendDirection
from ..domain.merged_bar import MergedBar
from ..domain.raw_bar import RawBar


class InclusionEngine:
    def __init__(self, config: dict):
        self.mode = config.get("mode", "directional_merge")
        self.equal_policy = config.get("equal_high_low_policy", "explicit")
        self.preserve_source = config.get("preserve_source_bars", True)
        self.direction_init = config.get("direction_initialization", "first_bar_up")
        self.rule_profile = "minimal_strict_v1"
        self.rule_version = "1.0.0"

    def process(
        self,
        raw_bars: list[RawBar],
        *,
        initial_direction: TrendDirection | str | None = None,
        index_offset: int = 0,
    ) -> tuple[list[MergedBar], list[LifecycleEvent]]:
        """处理输入窗口。

        ``initial_direction`` 和 ``index_offset`` 仅用于增量尾部重算；全量调用
        不传这两个参数时保持原有语义。
        """
        valid = [b for b in raw_bars if b.is_valid]
        if not valid:
            return [], []
        if isinstance(initial_direction, str):
            initial_direction = TrendDirection(initial_direction)
        direction = initial_direction or self._initial_direction(valid)
        work: list[RawBar] = []
        work_directions: list[TrendDirection] = []
        for source in valid:
            if not source.source_raw_bar_ids:
                source.source_raw_bar_ids = [source.bar_id]
            if work and self._has_inclusion(work[-1], source):
                work[-1] = self._merge(work[-1], source, direction)
                work_directions[-1] = direction
                continue
            if work:
                direction = self._direction_between(work[-1], source, direction)
            work.append(source)
            work_directions.append(direction)

        merged: list[MergedBar] = []
        events: list[LifecycleEvent] = []
        for local_idx, bar in enumerate(work):
            global_idx = index_offset + local_idx
            merge_direction = work_directions[local_idx]
            mb = MergedBar(
                bar_id=f"mbar_{global_idx + 1:06d}",
                bar_index=global_idx,
                timestamp=bar.timestamp,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                source_raw_bar_ids=list(bar.source_raw_bar_ids or [bar.bar_id]),
                merge_direction=merge_direction.value,
                object_id=f"mbar_{global_idx + 1:06d}_r1",
                logical_id=f"mbar:idx_{global_idx}",
                revision=1,
                status=StructureStatus.CONFIRMED,
                created_at_bar=bar.bar_index,
                confirmed_at_bar=bar.bar_index,
                rule_profile=self.rule_profile,
                rule_version=self.rule_version,
            )
            merged.append(mb)
            occurred_at = mb.source_raw_bar_ids[-1]
            events.append(LifecycleEvent(
                event_type=EventType.CREATED,
                object_type="merged_bar",
                object_id=mb.object_id,
                logical_id=mb.logical_id,
                occurred_at_bar_id=occurred_at,
                reason_code="INCLUSION_NORMALIZED",
                rule_profile=self.rule_profile,
                rule_version=self.rule_version,
                detail={"source_raw_bar_ids": mb.source_raw_bar_ids,
                        "merge_direction": mb.merge_direction},
            ))
            events.append(LifecycleEvent(
                event_type=EventType.CONFIRMED,
                object_type="merged_bar",
                object_id=mb.object_id,
                logical_id=mb.logical_id,
                occurred_at_bar_id=occurred_at,
                reason_code="MERGED_BAR_FINALIZED",
                rule_profile=self.rule_profile,
                rule_version=self.rule_version,
            ))
        return merged, events

    def _initial_direction(self, bars: list[RawBar]) -> TrendDirection:
        if len(bars) > 1:
            return self._direction_between(bars[0], bars[1], TrendDirection.UP)
        return TrendDirection.UP

    def _direction_for_bar(
        self,
        bars: list[RawBar],
        idx: int,
        seeded_direction: TrendDirection | None = None,
    ) -> TrendDirection:
        if idx == 0:
            return seeded_direction or self._initial_direction(bars)
        return self._direction_between(bars[idx - 1], bars[idx], TrendDirection.UP)

    @staticmethod
    def _direction_between(a: RawBar, b: RawBar, fallback: TrendDirection) -> TrendDirection:
        if b.high > a.high and b.low > a.low:
            return TrendDirection.UP
        if b.high < a.high and b.low < a.low:
            return TrendDirection.DOWN
        return fallback

    def _has_inclusion(self, a: RawBar, b: RawBar) -> bool:
        if self.equal_policy == "explicit":
            return (a.high > b.high and a.low < b.low) or (b.high > a.high and b.low < a.low)
        return (a.high >= b.high and a.low <= b.low) or (b.high >= a.high and b.low <= a.low)

    @staticmethod
    def _merge(a: RawBar, b: RawBar, direction: TrendDirection) -> RawBar:
        high, low = ((max(a.high, b.high), max(a.low, b.low))
                     if direction == TrendDirection.UP
                     else (min(a.high, b.high), min(a.low, b.low)))
        source_ids = list(dict.fromkeys((a.source_raw_bar_ids or [a.bar_id]) +
                                        (b.source_raw_bar_ids or [b.bar_id])))
        return RawBar(
            bar_id=f"_merged_{source_ids[0]}_{source_ids[-1]}",
            bar_index=a.bar_index,
            timestamp=max(a.timestamp, b.timestamp),
            open=a.open,
            high=high,
            low=low,
            close=b.close,
            volume=a.volume + b.volume,
            source_raw_bar_ids=source_ids,
        )
