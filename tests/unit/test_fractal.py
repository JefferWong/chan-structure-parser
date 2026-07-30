"""单元测试：分型识别引擎。"""

import pytest
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from chan_parser.domain.merged_bar import MergedBar
from chan_parser.domain.lifecycle import FractalType, StructureStatus
from chan_parser.engine.fractal import FractalEngine


def make_mbar(index: int, o: float, h: float, l: float, c: float) -> MergedBar:
    return MergedBar(
        bar_id=f"mbar_{index:06d}",
        bar_index=index,
        timestamp=datetime(2024, 1, 1),
        open=o,
        high=h,
        low=l,
        close=c,
        source_raw_bar_ids=[f"bar_{index:06d}"],
    )


class TestFractalEngine:
    """分型识别测试。"""

    def setup_method(self):
        self.config = {
            "window_size": 3,
            "allow_equal_high": False,
            "allow_equal_low": False,
            "use_merged_bars": True,
            "minimum_distance": 1,
        }
        self.engine = FractalEngine(self.config)

    def test_top_fractal(self):
        """标准顶分型：中间高点严格最高。"""
        bars = [
            make_mbar(0, 100, 103, 98, 102),
            make_mbar(1, 102, 108, 101, 105),  # 高点最高
            make_mbar(2, 104, 106, 100, 103),
        ]
        fractals, _ = self.engine.process(bars, 3)
        assert len(fractals) == 1
        assert fractals[0].fractal_type == FractalType.TOP
        assert fractals[0].price == 108
        assert fractals[0].merged_bar_index == 1

    def test_bottom_fractal(self):
        """标准底分型：中间低点严格最低。"""
        bars = [
            make_mbar(0, 100, 105, 100, 104),
            make_mbar(1, 99, 102, 95, 98),     # 低点最低
            make_mbar(2, 98, 101, 97, 100),
        ]
        fractals, _ = self.engine.process(bars, 3)
        assert len(fractals) == 1
        assert fractals[0].fractal_type == FractalType.BOTTOM
        assert fractals[0].price == 95

    def test_no_fractal_with_less_than_3_bars(self):
        """少于3根K线不应产生分型。"""
        bars = [
            make_mbar(0, 100, 105, 98, 103),
            make_mbar(1, 102, 106, 100, 104),
        ]
        fractals, _ = self.engine.process(bars, 2)
        assert len(fractals) == 0

    def test_no_fractal_on_flat(self):
        """平坦走势不应产生分型。"""
        bars = [
            make_mbar(0, 100, 105, 98, 103),
            make_mbar(1, 101, 105, 99, 102),  # 高点不严格大于
            make_mbar(2, 100, 104, 97, 101),
        ]
        fractals, _ = self.engine.process(bars, 3)
        # 中间K线高点105不严格大于左右（左=105相等），不构成顶分型
        assert all(f.fractal_type != FractalType.TOP or f.price != 105 for f in fractals)

    def test_consecutive_same_type_merged(self):
        """连续同类型分型应合并，只保留最极值。

        构造两个相邻的顶分型（中间K线不构成底分型），验证合并逻辑。
        """
        bars = [
            make_mbar(0, 100, 103, 98, 102),
            make_mbar(1, 102, 108, 101, 105),  # 顶分型，高点108
            make_mbar(2, 104, 106, 103, 103),  # 低点103 > 左右低点(101, 102) → 不构成底分型
            make_mbar(3, 103, 111, 102, 108),  # 顶分型，高点111（连续同类型）
            make_mbar(4, 105, 107, 101, 104),
        ]
        fractals, _ = self.engine.process(bars, 5)
        # bar[1]和bar[3]两个连续顶分型应合并为1个，保留最高点111
        top_fractals = [f for f in fractals if f.fractal_type == FractalType.TOP]
        assert len(top_fractals) == 1, (
            f"Expected 1 top fractal, got {len(top_fractals)}: "
            f"{[(f.merged_bar_index, f.price) for f in top_fractals]}"
        )
        assert top_fractals[0].price == 111

    def test_last_fractal_provisional(self):
        """最后一个分型应为PROVISIONAL状态。"""
        bars = [
            make_mbar(0, 100, 103, 98, 102),
            make_mbar(1, 102, 108, 101, 105),
            make_mbar(2, 104, 106, 100, 103),
        ]
        fractals, _ = self.engine.process(bars, 3)
        assert fractals[-1].status == StructureStatus.PROVISIONAL
        assert fractals[-1].repaint_risk == "HIGH"
