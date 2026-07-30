"""单元测试：严格笔构建引擎。"""
from datetime import datetime

from chan_parser.domain.merged_bar import MergedBar
from chan_parser.domain.fractal import Fractal
from chan_parser.domain.lifecycle import FractalType, StrokeDirection, StructureStatus
from chan_parser.engine.stroke import StrokeEngine


def make_mbar(index, o, h, l, c):
    return MergedBar(bar_id=f"mbar_{index:06d}", bar_index=index, timestamp=datetime(2024,1,1),
                     open=o, high=h, low=l, close=c, source_raw_bar_ids=[f"bar_{index:06d}"],
                     logical_id=f"mbar:idx_{index}", status=StructureStatus.CONFIRMED)


def make_fractal(fid, typ, idx, price):
    return Fractal(fractal_id=fid, fractal_type=typ, merged_bar_id=f"mbar_{idx:06d}",
                   merged_bar_index=idx, price=price, left_bar_id="", right_bar_id="",
                   window_indices=[idx-1,idx,idx+1], logical_id=f"fractal:{typ.value}:{idx}")


class TestStrokeEngine:
    def setup_method(self):
        self.engine=StrokeEngine({"mode":"strict","alternating_fractals_required":True,
                                  "minimum_merged_bar_count":5,"endpoint_extreme_required":True,
                                  "allow_unconfirmed_tail":True})

    def test_up_stroke(self):
        bars=[make_mbar(i,100,105,98,103) for i in range(10)]
        strokes,_=self.engine.process([make_fractal("fx1",FractalType.BOTTOM,1,98),
                                       make_fractal("fx2",FractalType.TOP,8,108)],bars,10)
        assert len(strokes)==1 and strokes[0].direction==StrokeDirection.UP

    def test_down_stroke(self):
        bars=[make_mbar(i,100,105,98,103) for i in range(10)]
        strokes,_=self.engine.process([make_fractal("fx1",FractalType.TOP,1,108),
                                       make_fractal("fx2",FractalType.BOTTOM,8,95)],bars,10)
        assert len(strokes)==1 and strokes[0].direction==StrokeDirection.DOWN

    def test_insufficient_bars(self):
        bars=[make_mbar(i,100,105,98,103) for i in range(5)]
        strokes,events=self.engine.process([make_fractal("fx1",FractalType.BOTTOM,0,98),
                                             make_fractal("fx2",FractalType.TOP,3,108)],bars,5)
        assert not strokes and events[-1].reason_code=="INSUFFICIENT_MERGED_BARS"

    def test_second_valid_stroke_confirms_first(self):
        bars=[make_mbar(i,100,105,98,103) for i in range(20)]
        strokes,_=self.engine.process([make_fractal("fx1",FractalType.BOTTOM,1,98),
                                       make_fractal("fx2",FractalType.TOP,8,108),
                                       make_fractal("fx3",FractalType.BOTTOM,15,95)],bars,20)
        assert len(strokes)==2 and strokes[0].status==StructureStatus.CONFIRMED
        assert strokes[-1].status==StructureStatus.PROVISIONAL

    def test_same_type_anchor_uses_more_extreme_fractal(self):
        bars=[make_mbar(i,100,105,95,103) for i in range(20)]
        strokes,_=self.engine.process([make_fractal("fx1",FractalType.BOTTOM,1,98),
                                       make_fractal("fx2",FractalType.BOTTOM,5,95),
                                       make_fractal("fx3",FractalType.TOP,12,110)],bars,20)
        assert strokes[0].start_price==95
