"""单元测试：严格笔构建引擎。"""
from datetime import datetime

import pytest

from chan_parser.domain.merged_bar import MergedBar
from chan_parser.domain.fractal import Fractal
from chan_parser.domain.lifecycle import FractalType, StrokeDirection, StructureStatus
from chan_parser.domain.stroke import Stroke
from chan_parser.engine.stroke import StrokeEngine


def make_mbar(index, o, h, low, c):
    return MergedBar(bar_id=f"mbar_{index:06d}", bar_index=index, timestamp=datetime(2024,1,1),
                     open=o, high=h, low=low, close=c, source_raw_bar_ids=[f"bar_{index:06d}"],
                     logical_id=f"mbar:idx_{index}", status=StructureStatus.CONFIRMED)


def make_fractal(fid, typ, idx, price):
    return Fractal(fractal_id=fid, fractal_type=typ, merged_bar_id=f"mbar_{idx:06d}",
                   merged_bar_index=idx, price=price, left_bar_id="", right_bar_id="",
                   window_indices=[idx-1,idx,idx+1], logical_id=f"fractal:{typ.value}:{idx}")


def superseded_tail_fixture(intermediate_high=100):
    bars = []
    for index in range(27):
        high, low = 100, 95
        if index == 0:
            high = 110
        if index == 5:
            low = 90
        if index == 10:
            high = 120
        if index == 12:
            high = intermediate_high
        if index == 15:
            high = 130
        if index == 20:
            low = 70
        if index == 26:
            high = 140
        bars.append(make_mbar(index, low + 1, high, low, high - 1))
    fractals = [
        make_fractal("t0", FractalType.TOP, 0, 110),
        make_fractal("b5", FractalType.BOTTOM, 5, 90),
        make_fractal("t10", FractalType.TOP, 10, 120),
        make_fractal("b12", FractalType.BOTTOM, 12, 80),
        make_fractal("t15", FractalType.TOP, 15, 130),
        make_fractal("b20", FractalType.BOTTOM, 20, 70),
        make_fractal("t26", FractalType.TOP, 26, 140),
    ]
    return bars, fractals


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



def test_same_type_anchor_collapse_emits_terminal_event():
    engine = StrokeEngine({
        "mode": "strict",
        "alternating_fractals_required": True,
        "minimum_merged_bar_count": 5,
        "endpoint_extreme_required": True,
        "allow_unconfirmed_tail": True,
    })
    bars = [make_mbar(i, 100, 105, 95, 103) for i in range(20)]
    fractals = [
        make_fractal("fx1", FractalType.BOTTOM, 1, 98),
        make_fractal("fx2", FractalType.BOTTOM, 5, 95),
        make_fractal("fx3", FractalType.TOP, 12, 110),
    ]
    _, events = engine.process(fractals, bars, 20)
    collapsed = [e for e in events if e.reason_code == "SAME_TYPE_ANCHOR_COLLAPSED"]
    assert len(collapsed) == 1
    assert collapsed[0].event_type == "CANDIDATE_REJECTED"
    assert collapsed[0].replaced_by == fractals[1].object_id


def test_disallowed_unconfirmed_tail_emits_invalidation_event():
    engine = StrokeEngine({
        "mode": "strict",
        "alternating_fractals_required": True,
        "minimum_merged_bar_count": 5,
        "endpoint_extreme_required": True,
        "allow_unconfirmed_tail": False,
    })
    bars = [make_mbar(i, 100, 105, 98, 103) for i in range(10)]
    strokes, events = engine.process([
        make_fractal("fx1", FractalType.BOTTOM, 1, 98),
        make_fractal("fx2", FractalType.TOP, 8, 108),
    ], bars, 10)
    assert strokes == []
    created = [e for e in events if e.event_type == "OBJECT_CREATED"]
    invalidated = [e for e in events if e.event_type == "OBJECT_INVALIDATED"]
    assert len(created) == 1
    assert len(invalidated) == 1
    assert invalidated[0].object_id == created[0].object_id
    assert invalidated[0].reason_code == "UNCONFIRMED_TAIL_NOT_ALLOWED"


def test_same_type_anchor_replacement_cannot_confirm_a_stale_tail_chain():
    """A later extreme anchor must carry the active provisional tail with it."""
    bars = []
    for index in range(27):
        high, low = 100, 95
        if index == 0:
            high = 110
        if index == 5:
            low = 90
        if index == 10:
            high = 120
        if index == 15:
            high = 130
        if index == 20:
            low = 70
        if index == 26:
            high = 140
        bars.append(make_mbar(index, low + 1, high, low, high - 1))

    fractals = [
        make_fractal("t0", FractalType.TOP, 0, 110),
        make_fractal("b5", FractalType.BOTTOM, 5, 90),
        make_fractal("t10", FractalType.TOP, 10, 120),
        # Too close to t10, so the anchor remains t10.
        make_fractal("b12", FractalType.BOTTOM, 12, 80),
        # Supersedes t10 while b5 -> t10 is still provisional.
        make_fractal("t15", FractalType.TOP, 15, 130),
        make_fractal("b20", FractalType.BOTTOM, 20, 70),
        make_fractal("t26", FractalType.TOP, 26, 140),
    ]

    strokes, _ = StrokeEngine({
        "mode": "strict",
        "alternating_fractals_required": True,
        "minimum_merged_bar_count": 5,
        "endpoint_extreme_required": True,
        "allow_unconfirmed_tail": True,
    }).process(fractals, bars, len(bars))

    confirmed = [stroke for stroke in strokes if stroke.status == StructureStatus.CONFIRMED]
    assert len(confirmed) == 3
    for previous, current in zip(confirmed, confirmed[1:]):
        assert previous.direction != current.direction
        assert previous.end_fractal_id == current.start_fractal_id
        assert previous.end_bar_index == current.start_bar_index
        assert previous.end_price == current.start_price
    assert confirmed[1].end_fractal_id == "t15"


def test_valid_provisional_tail_replacement_links_audit_event():
    bars, fractals = superseded_tail_fixture()
    strokes, events = StrokeEngine({
        "mode": "strict",
        "alternating_fractals_required": True,
        "minimum_merged_bar_count": 5,
        "endpoint_extreme_required": True,
        "allow_unconfirmed_tail": True,
    }).process(fractals, bars, len(bars))

    replacement = next(
        event for event in events
        if event.reason_code == "SAME_TYPE_PROVISIONAL_TAIL_REVISED"
    )
    invalidated = next(
        event for event in events
        if event.reason_code == "SAME_TYPE_PROVISIONAL_TAIL_REPLACED"
    )
    assert replacement.object_id in {stroke.object_id for stroke in strokes}
    assert invalidated.replaced_by == replacement.object_id
    assert invalidated.object_id != replacement.object_id


def test_invalid_provisional_tail_replacement_has_no_replacement_link():
    bars, fractals = superseded_tail_fixture(intermediate_high=140)
    strokes, events = StrokeEngine({
        "mode": "strict",
        "alternating_fractals_required": True,
        "minimum_merged_bar_count": 5,
        "endpoint_extreme_required": True,
        "allow_unconfirmed_tail": True,
    }).process(fractals[:5], bars, len(bars))

    invalidated = next(
        event for event in events
        if event.reason_code == "SAME_TYPE_PROVISIONAL_TAIL_REPLACED"
    )
    assert invalidated.replaced_by is None
    assert all(stroke.object_id != invalidated.object_id for stroke in strokes)


@pytest.mark.parametrize(
    "current_kwargs",
    [
        {"direction": StrokeDirection.DOWN},
        {"start_fractal_id": "fx_other"},
    ],
)
def test_confirmed_chain_postcondition_fails_closed(current_kwargs):
    previous = Stroke(
        object_id="stroke_a",
        logical_id="stroke:a",
        stroke_id="stroke_a",
        direction=StrokeDirection.DOWN,
        start_fractal_id="fx_start",
        end_fractal_id="fx_end",
        start_price=110,
        end_price=90,
        start_bar_index=1,
        end_bar_index=6,
        status=StructureStatus.CONFIRMED,
    )
    current = Stroke(
        object_id="stroke_b",
        logical_id="stroke:b",
        stroke_id="stroke_b",
        direction=StrokeDirection.UP,
        start_fractal_id="fx_end",
        end_fractal_id="fx_next",
        start_price=90,
        end_price=120,
        start_bar_index=6,
        end_bar_index=12,
        status=StructureStatus.CONFIRMED,
    )
    for key, value in current_kwargs.items():
        setattr(current, key, value)
    with pytest.raises(ValueError, match="PHASE1_CONFIRMED_STROKE_CHAIN_INVALID"):
        StrokeEngine._validate_confirmed_chain([previous, current])
