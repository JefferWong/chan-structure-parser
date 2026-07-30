"""回放测试：逐K线输入，验证无未来函数。"""

import pytest
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import yaml

from chan_parser.domain.raw_bar import RawBar
from chan_parser.engine.full_rebuild import FullRebuildEngine
from chan_parser.engine.incremental import IncrementalEngine
from chan_parser.audit.consistency import ConsistencyChecker


def make_bars(count: int, seed: int = 42) -> list[RawBar]:
    """生成测试用K线序列。"""
    import random
    random.seed(seed)

    bars = []
    base = datetime(2024, 1, 2, 9, 30)
    price = 100.0

    for i in range(count):
        change = random.gauss(0, 1.5)
        o = price
        c = price + change
        h = max(o, c) + abs(random.gauss(0, 0.5))
        l = min(o, c) - abs(random.gauss(0, 0.5))
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


class TestReplayNoFutureLeak:
    """逐K回放测试：防止未来函数。"""

    @pytest.fixture
    def profile(self):
        config_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "configs", "profiles", "minimal_strict_v1.yaml"
        )
        with open(config_path) as f:
            return yaml.safe_load(f)

    def test_incremental_full_consistency(self, profile):
        """逐K增量追加与全量重建的已确认结构必须一致。"""
        bars = make_bars(100)

        # 全量重建
        full_engine = FullRebuildEngine(profile)
        full_result = full_engine.process(bars)

        # 逐K增量追加
        incr_engine = IncrementalEngine(profile)
        incr_result = None
        for bar in bars:
            incr_result = incr_engine.append_one(bar)

        # 一致性检查
        checker = ConsistencyChecker()
        result = checker.check(full_result, incr_result)
        assert result["pass"], f"Consistency failed: {result['differences']}"

    def test_no_future_reference(self, profile):
        """验证任意历史时点的输出不引用未来K线。

        方法：用前N根K线全量重建，检查所有结构的 bar_index 不超过 N-1。
        """
        bars = make_bars(100)

        for n in range(10, len(bars) + 1, 10):
            subset = bars[:n]
            engine = FullRebuildEngine(profile)
            result = engine.process(subset)

            # 检查所有合并K线的 source_raw_bar_ids
            for mb in result["structures"]["merged_bars"]:
                for rid in mb.get("source_raw_bar_ids", []):
                    # 提取索引
                    idx = int(rid.split("_")[-1]) - 1
                    assert idx < n, (
                        f"MergedBar references future raw bar: "
                        f"{rid} (idx={idx}) >= {n}"
                    )

            # 检查所有分型
            for f in result["structures"]["fractals"]:
                assert f["merged_bar_index"] < len(result["structures"]["merged_bars"]), (
                    f"Fractal references out-of-range merged bar: "
                    f"{f['merged_bar_index']}"
                )

            # 检查所有笔
            for s in result["structures"]["strokes"]:
                assert s["end_bar_index"] < len(result["structures"]["merged_bars"]), (
                    f"Stroke references out-of-range bar: "
                    f"end={s['end_bar_index']}"
                )

    def test_prefix_immutability(self, profile):
        """前缀不变性：前N根K线的已确认结构不因后续K线加入而改变。

        比较：全量(前N根) vs 全量(前M根，M>N)中前N根产生的已确认结构。
        """
        bars = make_bars(120)

        # 前100根全量
        engine_n = FullRebuildEngine(profile)
        result_n = engine_n.process(bars[:100])

        # 全部120根全量
        engine_m = FullRebuildEngine(profile)
        result_m = engine_m.process(bars)

        # 比较已确认的笔：前100根产生的已确认笔，在120根中应该相同
        confirmed_n = [
            s for s in result_n["structures"]["strokes"]
            if s["status"] == "CONFIRMED"
        ]
        confirmed_m = [
            s for s in result_m["structures"]["strokes"]
            if s["status"] == "CONFIRMED"
        ]

        # 前100根的已确认笔应该全部出现在120根的已确认笔中
        for sn in confirmed_n:
            found = any(
                sm["start_bar_index"] == sn["start_bar_index"]
                and sm["end_bar_index"] == sn["end_bar_index"]
                and sm["direction"] == sn["direction"]
                and sm["start_price"] == sn["start_price"]
                and sm["end_price"] == sn["end_price"]
                for sm in confirmed_m
            )
            assert found, (
                f"Confirmed stroke from first 100 bars not preserved: "
                f"{sn['stroke_id']} {sn['direction']} "
                f"bar[{sn['start_bar_index']}->{sn['end_bar_index']}]"
            )
