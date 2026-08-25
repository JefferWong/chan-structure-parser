from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from chan_parser.contracts.segment_incremental_source_continuity import (
    SegmentIncrementalSourceContinuityAction,
    SegmentIncrementalSourceContinuityDecision,
    SegmentIncrementalSourceContinuityError,
    evaluate_incremental_segment_source_continuity,
)
from chan_parser.domain.lifecycle import StructureStatus, StrokeDirection
from chan_parser.domain.segment import Segment
from chan_parser.domain.stroke import Stroke


def strokes(count: int = 5) -> tuple[Stroke, ...]:
    result = []
    for index in range(count):
        direction = StrokeDirection.UP if index % 2 == 0 else StrokeDirection.DOWN
        result.append(Stroke(
            object_id=f"stroke_{index + 1:06d}_r1",
            logical_id=f"stroke:logical:{index + 1}",
            revision=1,
            status=StructureStatus.CONFIRMED,
            stroke_id=f"stroke_{index + 1:06d}",
            direction=direction,
            start_fractal_id=f"fx:{index}",
            end_fractal_id=f"fx:{index + 1}",
            start_price=float(index),
            end_price=float(index + 1),
            start_bar_index=index,
            end_bar_index=index + 1,
        ))
    return tuple(result)


def previous_segment() -> Segment:
    return Segment(
        object_id="segment_000001_000003_U_r3",
        logical_id="segment:stroke:logical:1->stroke:logical:3",
        revision=3,
        status=StructureStatus.CONFIRMED,
        created_at_bar=4,
        confirmed_at_bar=4,
        segment_id="segment_000001_000003_U",
        direction=StrokeDirection.UP,
        start_stroke_id="stroke_000001",
        end_stroke_id="stroke_000003",
        stroke_ids=["stroke_000001", "stroke_000002", "stroke_000003"],
        start_price=0.0,
        end_price=3.0,
        start_bar_index=0,
        end_bar_index=3,
    )


def evaluate(previous=None, historical=None, current=None):
    return evaluate_incremental_segment_source_continuity(
        previous=previous or previous_segment(),
        previous_source_strokes=historical or strokes(),
        current_source_strokes=current or strokes(),
    )


def test_identical_prefix_and_appended_suffix_are_preserved():
    identical = evaluate()
    appended = evaluate(current=strokes(8))
    assert identical.action is SegmentIncrementalSourceContinuityAction.PRESERVED
    assert appended.action is SegmentIncrementalSourceContinuityAction.PRESERVED
    assert identical.reason_code == appended.reason_code == "SOURCE_CONTINUITY_PRESERVED"
    assert identical.bound_prefix_length == appended.bound_prefix_length == 3


def test_suffix_changes_do_not_affect_bound_prefix_continuity():
    current = list(strokes())
    current[3] = replace(
        current[3], object_id="changed-suffix-r9", revision=9, end_price=99.0
    )
    assert evaluate(current=tuple(current)).action is (
        SegmentIncrementalSourceContinuityAction.PRESERVED
    )


def test_current_source_too_short_is_broken_not_malformed():
    decision = evaluate(current=strokes(2))
    assert decision.action is SegmentIncrementalSourceContinuityAction.BROKEN
    assert decision.reason_code == "SOURCE_CONTINUITY_BROKEN"
    assert decision.bound_prefix_length == 3


@pytest.mark.parametrize(
    "field,value",
    [
        ("logical_id", "changed-logical"),
        ("stroke_id", "changed-stroke"),
        ("object_id", "changed-object-r1"),
        ("revision", 2),
        ("end_price", 99.0),
    ],
)
def test_each_bound_prefix_identity_or_content_change_is_broken(field, value):
    current = list(strokes())
    current[1] = replace(current[1], **{field: value})
    assert evaluate(current=tuple(current)).action is (
        SegmentIncrementalSourceContinuityAction.BROKEN
    )


@pytest.mark.parametrize("source_name", ["historical", "current"])
@pytest.mark.parametrize("identity", ["logical_id", "object_id", "stroke_id"])
def test_duplicate_source_identity_fails_closed(source_name, identity):
    source = list(strokes())
    source[1] = replace(source[1], **{identity: getattr(source[0], identity)})
    kwargs = {source_name: tuple(source)}
    with pytest.raises(SegmentIncrementalSourceContinuityError) as raised:
        evaluate(**kwargs)
    assert "DUPLICATE" in raised.value.reason_code


@pytest.mark.parametrize("source_name", ["historical", "current"])
def test_bool_revision_fails_closed(source_name):
    source = list(strokes())
    source[1] = replace(source[1], revision=True)
    with pytest.raises(SegmentIncrementalSourceContinuityError) as raised:
        evaluate(**{source_name: tuple(source)})
    assert raised.value.reason_code.endswith("REVISION_INVALID")


def test_previous_segment_order_and_endpoint_binding_fail_closed():
    with pytest.raises(SegmentIncrementalSourceContinuityError) as order_error:
        evaluate(previous=replace(
            previous_segment(),
            stroke_ids=["stroke_000002", "stroke_000001", "stroke_000003"],
        ))
    assert order_error.value.reason_code.endswith("BINDING_INVALID")

    for change in (
        {"start_bar_index": 1},
        {"start_price": -1.0},
        {"end_bar_index": 4},
        {"end_price": 4.0},
        {"direction": StrokeDirection.DOWN},
    ):
        with pytest.raises(SegmentIncrementalSourceContinuityError) as boundary_error:
            evaluate(previous=replace(previous_segment(), **change))
        assert boundary_error.value.reason_code.endswith("BOUNDARY_INVALID")


@pytest.mark.parametrize(
    "previous",
    [
        replace(previous_segment(), status=StructureStatus.PROVISIONAL),
        replace(previous_segment(), status=StructureStatus.INVALIDATED),
        replace(previous_segment(), status=StructureStatus.REPLACED),
        replace(previous_segment(), invalidated_at_bar=9),
        replace(previous_segment(), replaced_by="replacement"),
        replace(previous_segment(), revision=True),
        replace(previous_segment(), revision=0),
    ],
)
def test_invalid_previous_lifecycle_or_revision_fails_closed(previous):
    with pytest.raises(SegmentIncrementalSourceContinuityError):
        evaluate(previous=previous)


def test_unstable_non_string_and_raising_content_hash_fail_closed(monkeypatch):
    original_segment_hash = Segment.content_hash
    values = iter(("first", "second"))
    monkeypatch.setattr(Segment, "content_hash", lambda self: next(values))
    with pytest.raises(SegmentIncrementalSourceContinuityError) as unstable:
        evaluate()
    assert unstable.value.reason_code.endswith("CONTENT_HASH_INVALID")

    monkeypatch.setattr(Segment, "content_hash", lambda self: 123)
    with pytest.raises(SegmentIncrementalSourceContinuityError):
        evaluate()

    calls = iter(("stable", "stable", "changed"))
    monkeypatch.setattr(Stroke, "content_hash", lambda self: next(calls))
    with pytest.raises(SegmentIncrementalSourceContinuityError):
        evaluate()

    monkeypatch.setattr(Segment, "content_hash", original_segment_hash)
    monkeypatch.setattr(Stroke, "content_hash", lambda self: None)
    with pytest.raises(SegmentIncrementalSourceContinuityError):
        evaluate()


def test_decision_type_is_closed_and_action_invariants_are_exact():
    with pytest.raises(SegmentIncrementalSourceContinuityError):
        SegmentIncrementalSourceContinuityDecision("PRESERVED", "SOURCE_CONTINUITY_PRESERVED", 3)
    with pytest.raises(SegmentIncrementalSourceContinuityError):
        SegmentIncrementalSourceContinuityDecision(
            SegmentIncrementalSourceContinuityAction.PRESERVED,
            "SOURCE_CONTINUITY_BROKEN",
            3,
        )
    with pytest.raises(SegmentIncrementalSourceContinuityError):
        SegmentIncrementalSourceContinuityDecision(
            SegmentIncrementalSourceContinuityAction.BROKEN,
            "SOURCE_CONTINUITY_BROKEN",
            True,
        )


def test_success_and_failure_paths_are_input_pure():
    for current in (strokes(), strokes(2)):
        previous = previous_segment()
        historical = strokes()
        before = deepcopy((previous, historical, current))
        evaluate(previous=previous, historical=historical, current=current)
        assert (previous, historical, current) == before

    previous = previous_segment()
    historical = list(strokes())
    historical[1] = replace(historical[1], revision=True)
    current = strokes()
    before = deepcopy((previous, historical, current))
    with pytest.raises(SegmentIncrementalSourceContinuityError):
        evaluate(previous=previous, historical=tuple(historical), current=current)
    assert (previous, historical, current) == before


def test_deepcopy_and_reconstructed_records_are_deterministic():
    original = evaluate()
    copied = evaluate(
        previous=deepcopy(previous_segment()),
        historical=deepcopy(strokes()),
        current=deepcopy(strokes()),
    )
    reconstructed = evaluate(
        previous=replace(previous_segment()),
        historical=tuple(replace(stroke) for stroke in strokes()),
        current=tuple(replace(stroke) for stroke in strokes()),
    )
    assert original == copied == reconstructed


@pytest.mark.parametrize(
    "field,value",
    [
        ("logical_id", ""),
        ("object_id", ""),
        ("segment_id", ""),
    ],
)
def test_previous_identity_fields_are_exact_validated(field, value):
    with pytest.raises(SegmentIncrementalSourceContinuityError):
        evaluate(previous=replace(previous_segment(), **{field: value}))


@pytest.mark.parametrize("value", [None, "strokes", b"strokes", object()])
def test_historical_and_current_source_are_required_sequences(value):
    with pytest.raises(SegmentIncrementalSourceContinuityError):
        evaluate_incremental_segment_source_continuity(
            previous=previous_segment(),
            previous_source_strokes=value,
            current_source_strokes=strokes(),
        )
    with pytest.raises(SegmentIncrementalSourceContinuityError):
        evaluate_incremental_segment_source_continuity(
            previous=previous_segment(),
            previous_source_strokes=strokes(),
            current_source_strokes=value,
        )
