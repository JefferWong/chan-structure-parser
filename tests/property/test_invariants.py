"""性质测试：随机OHLC序列验证结构不变量。"""

import pytest
import sys
import os
import random
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import yaml

from chan_parser.domain.raw_bar import RawBar
from chan_parser.domain.lifecycle import FractalType, StructureStatus
from chan_parser.engine.full_rebuild import FullRebuildEngine


def random_bars(count: int, seed: int) -> list[RawBar]:
    """生成随机OHLC序列。"""
    random.seed(seed)
    bars = []
    base = datetime(2024, 1, 2, 9, 30)
    price = 100.0

    for i in range(count):
        change = random.gauss(0, 2.0)
        o = price
        c = price + change
        h = max(o, c) + abs(random.gauss(0, 1.0))
        l = min(o, c) - abs(random.gauss(0, 1.0))
        l = max(l, 0.1)

        bars.append(RawBar(
            bar_id=f"bar_{i + 1:06d}",
            bar_index=i,
            timestamp=base + timedelta(minutes=30 * i),
            open=round(o, 2),
            high=round(h, 2),
            low=round(l, 2),
            close=round(c, 2),
        ))
        price = c

    return bars


class TestInvariants:
    """结构不变量测试。"""

    @pytest.fixture
    def profile(self):
        config_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "configs", "profiles", "minimal_strict_v1.yaml"
        )
        with open(config_path) as f:
            return yaml.safe_load(f)

    @pytest.mark.parametrize("seed", [42, 123, 456, 789])
    def test_fractal_on_merged_bars_only(self, profile, seed):
        """不变量：分型只能建立在合并K线上。"""
        bars = random_bars(50, seed)
        engine = FullRebuildEngine(profile)
        result = engine.process(bars)

        merged_bar_ids = {mb["bar_id"] for mb in result["structures"]["merged_bars"]}
        for f in result["structures"]["fractals"]:
            assert f["merged_bar_id"] in merged_bar_ids, (
                f"Fractal {f['fractal_id']} references non-existent merged bar "
                f"{f['merged_bar_id']}"
            )

    @pytest.mark.parametrize("seed", [42, 123, 456, 789])
    def test_stroke_endpoints_opposite_types(self, profile, seed):
        """不变量：笔的两个端点分型类型必须相反。"""
        bars = random_bars(100, seed)
        engine = FullRebuildEngine(profile)
        result = engine.process(bars)

        fractals_by_id = {f["fractal_id"]: f for f in result["structures"]["fractals"]}
        for s in result["structures"]["strokes"]:
            start = fractals_by_id.get(s["start_fractal_id"])
            end = fractals_by_id.get(s["end_fractal_id"])
            if start and end:
                assert start["fractal_type"] != end["fractal_type"], (
                    f"Stroke {s['stroke_id']} has same-type endpoints: "
                    f"{start['fractal_type']} -> {end['fractal_type']}"
                )

    @pytest.mark.parametrize("seed", [42, 123, 456, 789])
    def test_confirmed_stroke_no_future_ref(self, profile, seed):
        """不变量：已确认笔不引用未来K线。"""
        bars = random_bars(100, seed)
        engine = FullRebuildEngine(profile)
        result = engine.process(bars)

        max_bar = len(result["structures"]["merged_bars"]) - 1
        for s in result["structures"]["strokes"]:
            if s["status"] == "CONFIRMED":
                assert s["end_bar_index"] <= max_bar, (
                    f"Confirmed stroke {s['stroke_id']} end_bar_index "
                    f"{s['end_bar_index']} > max {max_bar}"
                )

    @pytest.mark.parametrize("seed", [42, 123, 456, 789])
    def test_deterministic_output(self, profile, seed):
        """不变量：同输入多次运行输出完全一致。"""
        bars = random_bars(50, seed)

        engine1 = FullRebuildEngine(profile)
        result1 = engine1.process(bars)

        engine2 = FullRebuildEngine(profile)
        result2 = engine2.process(bars)

        # 比较已确认笔的数量和内容
        strokes1 = [s for s in result1["structures"]["strokes"] if s["status"] == "CONFIRMED"]
        strokes2 = [s for s in result2["structures"]["strokes"] if s["status"] == "CONFIRMED"]

        assert len(strokes1) == len(strokes2)
        for s1, s2 in zip(strokes1, strokes2):
            assert s1["direction"] == s2["direction"]
            assert s1["start_bar_index"] == s2["start_bar_index"]
            assert s1["end_bar_index"] == s2["end_bar_index"]
            assert s1["start_price"] == s2["start_price"]
            assert s1["end_price"] == s2["end_price"]

    @pytest.mark.parametrize("seed", [42, 123, 456, 789])
    def test_merged_bars_cover_all_raw(self, profile, seed):
        """不变量：所有有效原始K线必须被合并K线覆盖。"""
        bars = random_bars(50, seed)
        engine = FullRebuildEngine(profile)
        result = engine.process(bars)

        raw_ids = {b.bar_id for b in bars if b.is_valid}
        covered_ids = set()
        for mb in result["structures"]["merged_bars"]:
            covered_ids.update(mb.get("source_raw_bar_ids", []))

        missing = raw_ids - covered_ids
        assert len(missing) == 0, (
            f"Raw bars not covered by any merged bar: {missing}"
        )
