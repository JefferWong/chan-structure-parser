"""单元测试：笔构建引擎。"""

import pytest
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from chan_parser.domain.merged_bar import MergedBar
from chan_parser.domain.fractal import Fractal
from chan_parser.domain.lifecycle import (
    FractalType,
    StrokeDirection,
    StructureStatus,
)
from chan_parser.engine.stroke import StrokeEngine


def make_mbar(index: int, o: float, h: float, l: float, c: float) -> MergedBar:
    return MergedBar(
        bar_id=f"mbar_{index:06d}",
        bar_index=index,
        timestamp=datetime(2024, 1, 1),
        open=o, high=h, low=l, close=c,
        source_raw_bar_ids=[f"bar_{index:06d}"],
    )


def make_fractal(fx_id: str, fx_type: FractalType, bar_idx: int, price: float) -> Fractal:
    return Fractal(
        fractal_id=fx_id,
        fractal_type=fx_type,
        merged_bar_id=f"mbar_{bar_idx:06d}",
        merged_bar_index=bar_idx,
        price=price,
        left_bar_id="",
        right_bar_id="",
        window_indices=[bar_idx - 1, bar_idx, bar_idx + 1],
        status=StructureStatus.CANDIDATE,
        created_at_bar=bar_idx,
    )


class TestStrokeEngine:
    """笔构建测试。"""

    def setup_method(self):
        self.config = {
            "mode": "strict",
            "alternating_fractals_required": True,
            "minimum_merged_bar_count": 5,
            "endpoint_extreme_required": True,
            "allow_unconfirmed_tail": True,
        }
        self.engine = StrokeEngine(self.config)

    def test_up_stroke(self):
        """底分型→顶分型构成向上笔。"""
        merged_bars = [make_mbar(i, 100, 105, 98, 103) for i in range(10)]
        fractals = [
            make_fractal("fx_01", FractalType.BOTTOM, 1, 98.0),
            make_fractal("fx_02", FractalType.TOP, 8, 108.0),
        ]
        strokes, _ = self.engine.process(fractals, merged_bars, 10)
        assert len(strokes) == 1
        assert strokes[0].direction == StrokeDirection.UP
        assert strokes[0].start_price == 98.0
        assert strokes[0].end_price == 108.0

    def test_down_stroke(self):
        """顶分型→底分型构成向下笔。"""
        merged_bars = [make_mbar(i, 100, 105, 98, 103) for i in range(10)]
        fractals = [
            make_fractal("fx_01", FractalType.TOP, 1, 108.0),
            make_fractal("fx_02", FractalType.BOTTOM, 8, 95.0),
        ]
        strokes, _ = self.engine.process(fractals, merged_bars, 10)
        assert len(strokes) == 1
        assert strokes[0].direction == StrokeDirection.DOWN
        assert strokes[0].start_price == 108.0
        assert strokes[0].end_price == 95.0

    def test_insufficient_bars(self):
        """K线数不足5根时不应构成笔。"""
        merged_bars = [make_mbar(i, 100, 105, 98, 103) for i in range(5)]
        fractals = [
            make_fractal("fx_01", FractalType.BOTTOM, 0, 98.0),
            make_fractal("fx_02", FractalType.TOP, 3, 108.0),  # 只有4根
        ]
        strokes, _ = self.engine.process(fractals, merged_bars, 5)
        assert len(strokes) == 0  # bar_count=4 < 5

    def test_last_stroke_provisional(self):
        """最后一笔应为PROVISIONAL状态。"""
        merged_bars = [make_mbar(i, 100, 105, 98, 103) for i in range(10)]
        fractals = [
            make_fractal("fx_01", FractalType.BOTTOM, 1, 98.0),
            make_fractal("fx_02", FractalType.TOP, 8, 108.0),
        ]
        strokes, _ = self.engine.process(fractals, merged_bars, 10)
        assert strokes[-1].status == StructureStatus.PROVISIONAL
        assert strokes[-1].repaint_risk == "HIGH"

    def test_second_stroke_confirms_first(self):
        """第二笔确认后，第一笔应变为CONFIRMED。"""
        merged_bars = [make_mbar(i, 100, 105, 98, 103) for i in range(20)]
        fractals = [
            make_fractal("fx_01", FractalType.BOTTOM, 1, 98.0),
            make_fractal("fx_02", FractalType.TOP, 8, 108.0),
            make_fractal("fx_03", FractalType.BOTTOM, 15, 95.0),
        ]
        strokes, _ = self.engine.process(fractals, merged_bars, 20)
        assert len(strokes) >= 2
        assert strokes[0].status == StructureStatus.CONFIRMED

    def test_non_alternating_skipped(self):
        """连续两个底分型：跳过不构成笔，直接找下一个顶分型。"""
        merged_bars = [make_mbar(i, 100, 105, 98, 103) for i in range(20)]
        fractals = [
            make_fractal("fx_01", FractalType.BOTTOM, 1, 98.0),
            make_fractal("fx_02", FractalType.BOTTOM, 5, 95.0),  # 连续底分型
            make_fractal("fx_03", FractalType.TOP, 12, 110.0),
        ]
        strokes, _ = self.engine.process(fractals, merged_bars, 20)
        # 应该用 fx_01(98.0) 到 fx_03(110.0)，跳过 fx_02
        assert len(strokes) >= 1
        assert strokes[0].start_price == 98.0
        assert strokes[0].end_price == 110.0
