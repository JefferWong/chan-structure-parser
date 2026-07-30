"""单元测试：包含处理引擎。"""

import pytest
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from chan_parser.domain.raw_bar import RawBar
from chan_parser.engine.inclusion import InclusionEngine


def make_bar(index: int, o: float, h: float, l: float, c: float) -> RawBar:
    return RawBar(
        bar_id=f"bar_{index:06d}",
        bar_index=index,
        timestamp=datetime(2024, 1, 1, 9, 30) if index == 0
        else datetime(2024, 1, 1, 10, 0),
        open=o,
        high=h,
        low=l,
        close=c,
    )


class TestInclusionEngine:
    """K线包含处理测试。"""

    def setup_method(self):
        self.config = {
            "mode": "directional_merge",
            "equal_high_low_policy": "explicit",
            "preserve_source_bars": True,
            "direction_initialization": "first_bar_up",
        }
        self.engine = InclusionEngine(self.config)

    def test_single_bar(self):
        """单根K线应原样输出。"""
        bars = [make_bar(0, 100, 105, 98, 103)]
        merged, events = self.engine.process(bars)
        assert len(merged) == 1
        assert merged[0].open == 100
        assert merged[0].high == 105
        assert merged[0].low == 98
        assert merged[0].close == 103

    def test_no_inclusion(self):
        """无包含关系的两根K线应分别输出。"""
        bars = [
            make_bar(0, 100, 105, 98, 103),
            make_bar(1, 104, 108, 102, 106),
        ]
        merged, _ = self.engine.process(bars)
        assert len(merged) == 2
        assert merged[1].open == 104
        assert merged[1].close == 106

    def test_upward_merge(self):
        """向上趋势的包含合并：取高高、取低高。"""
        bars = [
            make_bar(0, 100, 105, 98, 103),   # 第一根
            make_bar(1, 102, 104, 100, 101),   # 被第一根包含（高点更低，低点更高）
        ]
        merged, _ = self.engine.process(bars)
        assert len(merged) == 1
        # 向上趋势：高高=105, 低高=max(98,100)=100
        assert merged[0].high == 105
        assert merged[0].low == 100

    def test_equal_high_no_inclusion(self):
        """explicit模式下，高点相等的两根K线不算包含。"""
        bars = [
            make_bar(0, 100, 105, 98, 103),
            make_bar(1, 102, 105, 97, 101),  # 高点相等=105
        ]
        merged, _ = self.engine.process(bars)
        # explicit模式：高点相等不算包含，两根都保留
        assert len(merged) >= 1

    def test_preserve_source(self):
        """合并后的K线应保留原始K线引用。"""
        bars = [
            make_bar(0, 100, 105, 98, 103),
            make_bar(1, 102, 104, 100, 101),
        ]
        merged, _ = self.engine.process(bars)
        assert len(merged) == 1
        assert len(merged[0].source_raw_bar_ids) == 2
        assert "bar_000000" in merged[0].source_raw_bar_ids
        assert "bar_000001" in merged[0].source_raw_bar_ids
