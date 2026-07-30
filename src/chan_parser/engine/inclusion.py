"""
K线包含处理引擎。

根据 profile 配置，将存在包含关系的相邻K线进行方向性合并，
输出无包含关系的标准化K线序列。
"""

from __future__ import annotations

from typing import Optional

from ..domain.lifecycle import EventType, LifecycleEvent, TrendDirection
from ..domain.merged_bar import MergedBar
from ..domain.raw_bar import RawBar


class InclusionEngine:
    """K线包含关系处理引擎。

    算法：
    1. 遍历原始K线序列
    2. 判断相邻K线是否存在包含关系
    3. 根据当前趋势方向决定合并方式
    4. 输出无包含关系的合并K线序列
    """

    def __init__(self, config: dict):
        """
        Args:
            config: inclusion 配置节（来自 profile）
        """
        self.mode = config.get("mode", "directional_merge")
        self.equal_policy = config.get("equal_high_low_policy", "explicit")
        self.preserve_source = config.get("preserve_source_bars", True)
        self.direction_init = config.get("direction_initialization", "first_bar_up")
        self.rule_profile = "minimal_strict_v1"
        self.rule_version = "1.0.0"

    def process(self, raw_bars: list[RawBar]) -> tuple[list[MergedBar], list[LifecycleEvent]]:
        """处理原始K线序列，输出合并K线序列和事件日志。

        Args:
            raw_bars: 按时间排序的原始K线列表（仅有效K线）

        Returns:
            (merged_bars, events): 合并K线列表, 生命周期事件列表
        """
        if not raw_bars:
            return [], []

        events: list[LifecycleEvent] = []
        valid_bars = [b for b in raw_bars if b.is_valid]

        if len(valid_bars) < 2:
            # 只有一根K线，直接输出
            mb = self._bar_to_merged(valid_bars[0], 0)
            return [mb], events

        # 初始趋势方向
        if self.direction_init == "first_bar_up":
            # 比较第一根和第二根K线来确定初始方向
            if valid_bars[0].high < valid_bars[1].high and valid_bars[0].low < valid_bars[1].low:
                direction = TrendDirection.UP
            elif valid_bars[0].high > valid_bars[1].high and valid_bars[0].low > valid_bars[1].low:
                direction = TrendDirection.DOWN
            else:
                # 包含关系，假设向上
                direction = TrendDirection.UP
        else:
            direction = TrendDirection.UP

        # 处理队列：当前待合并的K线（可能是原始K线或已部分合并的）
        pending: list[RawBar] = [valid_bars[0]]
        merged_bars: list[MergedBar] = []
        mbar_counter = 1

        # 为原始K线初始化 source_raw_bar_ids
        for bar in valid_bars:
            if not bar.source_raw_bar_ids:
                bar.source_raw_bar_ids = [bar.bar_id]

        for i in range(1, len(valid_bars)):
            current = valid_bars[i]
            last_pending = pending[-1]

            if self._has_inclusion(last_pending, current):
                # 存在包含关系，执行合并
                merged = self._merge(last_pending, current, direction)
                pending[-1] = merged  # 替换最后一项
            else:
                # 无包含关系
                # 先确定新趋势方向
                if current.high > last_pending.high and current.low > last_pending.low:
                    new_direction = TrendDirection.UP
                elif current.high < last_pending.high and current.low < last_pending.low:
                    new_direction = TrendDirection.DOWN
                else:
                    # 理论上不应出现（无包含却不符合方向），维持原方向
                    new_direction = direction

                # 如果方向改变，pending中的K线要确认输出
                if new_direction != direction and len(pending) > 1:
                    # 输出pending中除最后一个外的所有K线
                    for j in range(len(pending) - 1):
                        mb = self._raws_to_merged(
                            [pending[j]], mbar_counter, direction
                        )
                        merged_bars.append(mb)
                        mbar_counter += 1
                    pending = [pending[-1]]

                direction = new_direction
                pending.append(current)

        # 输出剩余的pending K线
        for bar in pending:
            mb = self._raws_to_merged([bar], mbar_counter, direction)
            merged_bars.append(mb)
            mbar_counter += 1

        # 为合并K线生成 logical_id
        for idx, mb in enumerate(merged_bars):
            mb.bar_id = f"mbar_{idx + 1:06d}"
            mb.bar_index = idx
            mb.logical_id = f"mbar:idx_{idx}"
            mb.status = __import__(
                "chan_parser.domain.lifecycle", fromlist=["StructureStatus"]
            ).StructureStatus.CONFIRMED
            mb.created_at_bar = idx

        return merged_bars, events

    def _has_inclusion(self, a: RawBar, b: RawBar) -> bool:
        """判断两根K线是否存在包含关系。

        包含关系：一根K线的高点 >= 另一根K线的高点 且 低点 <= 另一根K线的低点。
        根据 equal_high_low_policy：
        - explicit: 高点严格大于 且 低点严格小于才算包含
        """
        if self.equal_policy == "explicit":
            # 严格包含：高点严格大于且低点严格小于
            a_contains_b = a.high > b.high and a.low < b.low
            b_contains_a = b.high > a.high and b.low < a.low
            return a_contains_b or b_contains_a
        else:
            # 宽松包含：允许相等
            a_contains_b = a.high >= b.high and a.low <= b.low
            b_contains_a = b.high >= a.high and b.low <= a.low
            return a_contains_b or b_contains_a

    def _merge(self, a: RawBar, b: RawBar, direction: TrendDirection) -> RawBar:
        """根据趋势方向合并两根K线。

        向上趋势：高高、低高（取两K线中较高的高点和较高的低点）
        向下趋势：高低、低低（取两K线中较低的高点和较低的低点）
        """
        if direction == TrendDirection.UP:
            high = max(a.high, b.high)
            low = max(a.low, b.low)
        else:
            high = min(a.high, b.high)
            low = min(a.low, b.low)

        # 合并 source_raw_bar_ids
        merged_source_ids = list(dict.fromkeys(
            a.source_raw_bar_ids + b.source_raw_bar_ids
        ))

        # 合并后的K线：取最晚的时间戳，保留最新收盘价
        merged = RawBar(
            bar_id=f"_merged_{a.bar_id}_{b.bar_id}",
            bar_index=a.bar_index,  # 保留较前者的索引
            timestamp=max(a.timestamp, b.timestamp),
            open=a.open,
            high=high,
            low=low,
            close=b.close,  # 最新收盘价
            volume=a.volume + b.volume,
            source_raw_bar_ids=merged_source_ids,
        )
        return merged

    def _bar_to_merged(self, bar: RawBar, index: int) -> MergedBar:
        """将单根原始K线转换为合并K线。"""
        return self._raws_to_merged([bar], index, TrendDirection.UP)

    def _raws_to_merged(
        self, bars: list[RawBar], index: int, direction: TrendDirection
    ) -> MergedBar:
        """将一组RawBar打包为合并K线。"""
        if len(bars) == 1:
            b = bars[0]
            return MergedBar(
                bar_id=f"mbar_{index:06d}",
                bar_index=index,
                timestamp=b.timestamp,
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                volume=b.volume,
                source_raw_bar_ids=b.source_raw_bar_ids or [b.bar_id],
                merge_direction=direction.value,
                rule_profile=self.rule_profile,
                rule_version=self.rule_version,
            )
        else:
            highs = [b.high for b in bars]
            lows = [b.low for b in bars]
            all_source_ids = []
            for b in bars:
                all_source_ids.extend(b.source_raw_bar_ids or [b.bar_id])
            # 去重保序
            seen = set()
            unique_ids = []
            for sid in all_source_ids:
                if sid not in seen:
                    seen.add(sid)
                    unique_ids.append(sid)
            return MergedBar(
                bar_id=f"mbar_{index:06d}",
                bar_index=index,
                timestamp=max(b.timestamp for b in bars),
                open=bars[0].open,
                high=max(highs),
                low=min(lows),
                close=bars[-1].close,
                volume=sum(b.volume for b in bars),
                source_raw_bar_ids=unique_ids,
                merge_direction=direction.value,
                rule_profile=self.rule_profile,
                rule_version=self.rule_version,
            )
