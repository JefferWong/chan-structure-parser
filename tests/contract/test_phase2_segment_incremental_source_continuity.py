from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from chan_parser.contracts.segment_incremental_source_continuity import (
    SegmentIncrementalSourceContinuityAction,
    SegmentIncrementalSourceContinuityDecision,
    SegmentIncrementalSourceContinuityError,
    SegmentIncrementalSourcePreviousBinding,
    SegmentIncrementalSourceStrokeBinding,
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


_UNSET = object()


def evaluate(previous=_UNSET, historical=_UNSET, current=_UNSET):
    return evaluate_incremental_segment_source_continuity(
        previous=previous_segment() if previous is _UNSET else previous,
        previous_source_strokes=strokes() if historical is _UNSET else historical,
        current_source_strokes=strokes() if current is _UNSET else current,
    )


def test_identical_prefix_and_appended_suffix_are_preserved():
    identical = evaluate()
    appended = evaluate(current=strokes(8))
    assert identical.action is SegmentIncrementalSourceContinuityAction.PRESERVED
    assert appended.action is SegmentIncrementalSourceContinuityAction.PRESERVED
    assert identical.reason_code == appended.reason_code == "SOURCE_CONTINUITY_PRESERVED"
    assert identical.bound_prefix_length == appended.bound_prefix_length == 3


def test_historical_suffix_and_one_or_multiple_current_suffixes_are_allowed():
    for current_count in (4, 8):
        decision = evaluate(historical=strokes(7), current=strokes(current_count))
        assert decision.action is SegmentIncrementalSourceContinuityAction.PRESERVED
        assert decision.bound_prefix_length == 3

    historical = list(strokes())
    historical[4] = replace(
        historical[4], object_id="historical-suffix-r7", revision=7, end_price=77.0
    )
    assert evaluate(historical=tuple(historical)).action is (
        SegmentIncrementalSourceContinuityAction.PRESERVED
    )


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


def test_malformed_short_current_source_fails_closed_before_length_decision():
    current = list(strokes(2))
    current[1] = replace(current[1], revision=True)
    with pytest.raises(SegmentIncrementalSourceContinuityError) as raised:
        evaluate(current=tuple(current))
    assert raised.value.reason_code == (
        "SEGMENT_SOURCE_CONTINUITY_CURRENT_SOURCE_REVISION_INVALID"
    )


def test_empty_sources_fail_closed_under_nonempty_phase1_source_contract():
    for source_name in ("historical", "current"):
        for empty in ((), []):
            with pytest.raises(SegmentIncrementalSourceContinuityError) as raised:
                evaluate(**{source_name: empty})
            assert raised.value.reason_code == (
                "SEGMENT_SOURCE_CONTINUITY_"
                f"{'PREVIOUS_SOURCE' if source_name == 'historical' else 'CURRENT_SOURCE'}"
                "_REQUIRED"
            )


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
    "change",
    [
        {"start_stroke_id": "stroke_999998"},
        {"end_stroke_id": "stroke_999999"},
        {"stroke_ids": ["stroke_000001", "stroke_000002", "stroke_999999"]},
        {
            "stroke_ids": [
                "stroke_000001",
                "stroke_000002",
                "stroke_000003",
                "stroke_999999",
                "stroke_999998",
                "stroke_999997",
            ],
            "end_stroke_id": "stroke_999997",
        },
    ],
)
def test_previous_endpoint_and_missing_bound_stroke_fail_closed(change):
    with pytest.raises(SegmentIncrementalSourceContinuityError) as raised:
        evaluate(previous=replace(previous_segment(), **change))
    assert raised.value.reason_code == "SEGMENT_SOURCE_CONTINUITY_PREVIOUS_BINDING_INVALID"


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

    monkeypatch.setattr(Segment, "content_hash", original_segment_hash)
    calls_by_instance = {}

    def unstable_stroke_hash(self):
        key = id(self)
        calls_by_instance[key] = calls_by_instance.get(key, 0) + 1
        return "first" if calls_by_instance[key] == 1 else "second"

    monkeypatch.setattr(Stroke, "content_hash", unstable_stroke_hash)
    with pytest.raises(SegmentIncrementalSourceContinuityError) as stroke_unstable:
        evaluate()
    assert stroke_unstable.value.reason_code.endswith("CONTENT_HASH_INVALID")
    assert any(call_count >= 2 for call_count in calls_by_instance.values())

    monkeypatch.setattr(Stroke, "content_hash", lambda self: None)
    with pytest.raises(SegmentIncrementalSourceContinuityError):
        evaluate()


@pytest.mark.parametrize("record_type", [Segment, Stroke])
@pytest.mark.parametrize("bad_hash", ["", None, 1])
def test_empty_or_non_string_content_hash_fails_closed(
    monkeypatch, record_type, bad_hash
):
    monkeypatch.setattr(record_type, "content_hash", lambda self: bad_hash)
    with pytest.raises(SegmentIncrementalSourceContinuityError) as raised:
        evaluate()
    assert raised.value.reason_code.endswith("CONTENT_HASH_INVALID")


@pytest.mark.parametrize("record_type", [Segment, Stroke])
def test_raising_content_hash_fails_closed(monkeypatch, record_type):
    def raise_hash(_self):
        raise RuntimeError("diagnostic hash failure")

    monkeypatch.setattr(record_type, "content_hash", raise_hash)
    with pytest.raises(SegmentIncrementalSourceContinuityError) as raised:
        evaluate()
    assert raised.value.reason_code.endswith("CONTENT_HASH_INVALID")


def test_decision_type_is_closed_and_action_invariants_are_exact():
    valid = evaluate()

    with pytest.raises(SegmentIncrementalSourceContinuityError):
        SegmentIncrementalSourceContinuityDecision(
            "PRESERVED",
            "SOURCE_CONTINUITY_PRESERVED",
            3,
            valid.previous_binding,
            valid.historical_bound_prefix_binding,
            valid.current_source_binding,
        )
    with pytest.raises(SegmentIncrementalSourceContinuityError):
        SegmentIncrementalSourceContinuityDecision(
            SegmentIncrementalSourceContinuityAction.PRESERVED,
            "SOURCE_CONTINUITY_BROKEN",
            3,
            valid.previous_binding,
            valid.historical_bound_prefix_binding,
            valid.current_source_binding,
        )
    with pytest.raises(SegmentIncrementalSourceContinuityError):
        SegmentIncrementalSourceContinuityDecision(
            SegmentIncrementalSourceContinuityAction.BROKEN,
            "SOURCE_CONTINUITY_BROKEN",
            True,
            valid.previous_binding,
            valid.historical_bound_prefix_binding,
            valid.current_source_binding,
        )

    class EqualToEverything:
        def __eq__(self, _other):
            return True

    with pytest.raises(SegmentIncrementalSourceContinuityError):
        SegmentIncrementalSourceContinuityDecision(
            SegmentIncrementalSourceContinuityAction.PRESERVED,
            EqualToEverything(),
            3,
            valid.previous_binding,
            valid.historical_bound_prefix_binding,
            valid.current_source_binding,
        )


def test_current_suffix_is_bound_but_does_not_change_preserved_prefix_action():
    current_a = evaluate(current=strokes(5))
    current_b = evaluate(current=strokes(8))

    assert current_a.action is current_b.action is (
        SegmentIncrementalSourceContinuityAction.PRESERVED
    )
    assert current_a.reason_code == current_b.reason_code == (
        "SOURCE_CONTINUITY_PRESERVED"
    )
    assert current_a.bound_prefix_length == current_b.bound_prefix_length
    assert current_a.previous_binding == current_b.previous_binding
    assert current_a.historical_bound_prefix_binding == (
        current_b.historical_bound_prefix_binding
    )
    assert current_a.current_source_binding != current_b.current_source_binding
    assert current_a != current_b


def test_historical_suffix_is_not_part_of_previous_supporting_binding():
    historical_a = evaluate(historical=strokes(5))
    historical_b = evaluate(historical=strokes(8))

    assert historical_a == historical_b
    assert historical_a.historical_bound_prefix_binding == (
        historical_b.historical_bound_prefix_binding
    )


def test_binding_construction_reuses_validation_hash_results(monkeypatch):
    segment_calls = 0
    stroke_calls = {}
    original_segment_hash = Segment.content_hash
    original_stroke_hash = Stroke.content_hash
    expected_segment_hash = original_segment_hash(previous_segment())
    expected_stroke_hashes = tuple(
        original_stroke_hash(stroke) for stroke in strokes(3)
    )

    def segment_hash(value):
        nonlocal segment_calls
        segment_calls += 1
        return original_segment_hash(value)

    def stroke_hash(value):
        stroke_calls[value.stroke_id] = stroke_calls.get(value.stroke_id, 0) + 1
        return original_stroke_hash(value)

    monkeypatch.setattr(Segment, "content_hash", segment_hash)
    monkeypatch.setattr(Stroke, "content_hash", stroke_hash)
    decision = evaluate()

    assert segment_calls == 2
    assert stroke_calls == {f"stroke_{index + 1:06d}": 4 for index in range(5)}
    assert decision.previous_binding.content_hash == expected_segment_hash
    assert tuple(
        binding.content_hash for binding in decision.historical_bound_prefix_binding
    ) == expected_stroke_hashes


def test_previous_binding_changes_with_previous_identity_and_revision():
    original = evaluate()
    changed = evaluate(
        previous=replace(
            previous_segment(),
            logical_id="segment:other",
            object_id="segment_000001_000003_U_r4",
            segment_id="segment_000001_000003_U_other",
            revision=4,
        )
    )

    assert original.action is changed.action is (
        SegmentIncrementalSourceContinuityAction.PRESERVED
    )
    assert original.previous_binding != changed.previous_binding
    assert original.historical_bound_prefix_binding == (
        changed.historical_bound_prefix_binding
    )

    changed_content = evaluate(
        previous=replace(previous_segment(), rule_version="other-rule-version")
    )
    assert original.previous_binding.content_hash != (
        changed_content.previous_binding.content_hash
    )
    assert original.previous_binding != changed_content.previous_binding


@pytest.mark.parametrize(
    "factory",
    [
        lambda valid: replace(valid, previous_binding=object()),
        lambda valid: replace(valid, historical_bound_prefix_binding=[]),
        lambda valid: replace(valid, current_source_binding=[]),
        lambda valid: replace(
            valid,
            historical_bound_prefix_binding=(
                valid.historical_bound_prefix_binding[0],
            ),
        ),
        lambda valid: replace(
            valid,
            previous_binding=replace(
                valid.previous_binding,
                stroke_ids=("wrong-stroke", "stroke_000002", "stroke_000003"),
            ),
        ),
        lambda valid: replace(
            valid,
            action=SegmentIncrementalSourceContinuityAction.BROKEN,
        ),
    ],
)
def test_direct_decision_construction_fail_closes_adversarial_evidence(factory):
    with pytest.raises(SegmentIncrementalSourceContinuityError):
        factory(evaluate())


def test_direct_decision_rejects_empty_current_binding():
    with pytest.raises(SegmentIncrementalSourceContinuityError):
        replace(evaluate(), current_source_binding=())


@pytest.mark.parametrize(
    "binding_name,field",
    [
        ("historical_bound_prefix_binding", "logical_id"),
        ("historical_bound_prefix_binding", "object_id"),
        ("historical_bound_prefix_binding", "stroke_id"),
        ("current_source_binding", "logical_id"),
        ("current_source_binding", "object_id"),
        ("current_source_binding", "stroke_id"),
    ],
)
def test_direct_decision_rejects_global_binding_identity_duplicates(
    binding_name, field
):
    valid = evaluate()
    bindings = list(getattr(valid, binding_name))
    bindings[-1] = replace(bindings[-1], **{field: getattr(bindings[0], field)})

    with pytest.raises(SegmentIncrementalSourceContinuityError):
        replace(valid, **{binding_name: tuple(bindings)})


@pytest.mark.parametrize(
    "factory",
    [
        lambda: SegmentIncrementalSourcePreviousBinding(
            "", "object", "segment", 1, "hash", ("stroke",)
        ),
        lambda: SegmentIncrementalSourcePreviousBinding(
            "logical", "object", "segment", True, "hash", ("stroke",)
        ),
        lambda: SegmentIncrementalSourcePreviousBinding(
            "logical", "object", "segment", 1, "", ("stroke",)
        ),
        lambda: SegmentIncrementalSourcePreviousBinding(
            "logical", "object", "segment", 1, "hash", ["stroke"]
        ),
        lambda: SegmentIncrementalSourcePreviousBinding(
            "logical", "object", "segment", 1, "hash", ("stroke", "stroke")
        ),
        lambda: SegmentIncrementalSourceStrokeBinding(
            "logical", "object", "stroke", True, "hash"
        ),
        lambda: SegmentIncrementalSourceStrokeBinding(
            "logical", "object", "stroke", 1, None
        ),
    ],
)
def test_binding_types_fail_closed_on_exact_type_and_value_errors(factory):
    with pytest.raises(SegmentIncrementalSourceContinuityError):
        factory()


def test_preserved_decision_rejects_mismatching_or_short_current_prefix():
    valid = evaluate()
    changed = list(strokes())
    changed[1] = replace(changed[1], revision=2)
    changed_decision = evaluate(current=tuple(changed))
    short_decision = evaluate(current=strokes(2))

    with pytest.raises(SegmentIncrementalSourceContinuityError):
        replace(
            valid,
            current_source_binding=changed_decision.current_source_binding,
        )
    with pytest.raises(SegmentIncrementalSourceContinuityError):
        replace(valid, current_source_binding=short_decision.current_source_binding)


def test_broken_decision_rejects_identical_adequate_prefix_evidence():
    valid = evaluate()
    with pytest.raises(SegmentIncrementalSourceContinuityError):
        replace(valid, action=SegmentIncrementalSourceContinuityAction.BROKEN)


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


def test_broken_decision_is_deterministic_for_copied_and_reconstructed_inputs():
    changed = list(strokes())
    changed[1] = replace(changed[1], revision=2)
    changed = tuple(changed)
    original = evaluate(current=changed)
    copied = evaluate(
        previous=deepcopy(previous_segment()),
        historical=deepcopy(strokes()),
        current=deepcopy(changed),
    )
    reconstructed = evaluate(
        previous=replace(previous_segment()),
        historical=tuple(replace(stroke) for stroke in strokes()),
        current=tuple(replace(stroke) for stroke in changed),
    )
    assert original == copied == reconstructed
    assert original.action is SegmentIncrementalSourceContinuityAction.BROKEN


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


@pytest.mark.parametrize("source_name", ["historical", "current"])
@pytest.mark.parametrize("field", ["logical_id", "object_id", "stroke_id"])
@pytest.mark.parametrize("value", [None, ""])
def test_source_identity_fields_are_exact_nonempty_strings(
    source_name, field, value
):
    source = list(strokes())
    source[1] = replace(source[1], **{field: value})
    with pytest.raises(SegmentIncrementalSourceContinuityError) as raised:
        evaluate(**{source_name: tuple(source)})
    assert raised.value.reason_code.endswith(f"{field.upper()}_INVALID")


@pytest.mark.parametrize("source_name", ["historical", "current"])
def test_nonconfirmed_source_stroke_fails_closed(source_name):
    source = list(strokes())
    source[4] = replace(source[4], status=StructureStatus.PROVISIONAL)
    with pytest.raises(SegmentIncrementalSourceContinuityError) as raised:
        evaluate(**{source_name: tuple(source)})
    assert raised.value.reason_code.endswith("STATUS_INVALID")


@pytest.mark.parametrize(
    "source_name,reason_prefix",
    [
        ("historical", "SEGMENT_SOURCE_CONTINUITY_PREVIOUS_SOURCE"),
        ("current", "SEGMENT_SOURCE_CONTINUITY_CURRENT_SOURCE"),
    ],
)
@pytest.mark.parametrize(
    "lifecycle_change",
    [
        {"invalidated_at_bar": 9},
        {"replaced_by": "stroke-replacement"},
    ],
)
def test_confirmed_source_lifecycle_evidence_fails_closed_without_mutation(
    source_name, reason_prefix, lifecycle_change
):
    previous = previous_segment()
    historical = strokes()
    current = strokes()
    changed = list(historical if source_name == "historical" else current)
    changed[1] = replace(changed[1], **lifecycle_change)
    if source_name == "historical":
        historical = tuple(changed)
    else:
        current = tuple(changed)
    before = deepcopy((previous, historical, current))

    with pytest.raises(SegmentIncrementalSourceContinuityError) as raised:
        evaluate(previous=previous, historical=historical, current=current)

    assert raised.value.reason_code == f"{reason_prefix}_LIFECYCLE_INVALID"
    assert (previous, historical, current) == before


@pytest.mark.parametrize(
    "case",
    ["previous", "historical", "current", "unstable_hash", "boundary"],
)
def test_representative_failure_paths_are_input_pure(monkeypatch, case):
    previous = previous_segment()
    historical = strokes()
    current = strokes()
    if case == "previous":
        previous = replace(previous, revision=True)
    elif case == "historical":
        mutable = list(historical)
        mutable[1] = replace(mutable[1], revision=True)
        historical = tuple(mutable)
    elif case == "current":
        mutable = list(current)
        mutable[1] = replace(mutable[1], revision=True)
        current = tuple(mutable)
    elif case == "unstable_hash":
        values = iter(("first", "second"))
        monkeypatch.setattr(Segment, "content_hash", lambda self: next(values))
    else:
        previous = replace(previous, end_price=999.0)
    before = deepcopy((previous, historical, current))
    with pytest.raises(SegmentIncrementalSourceContinuityError):
        evaluate(previous=previous, historical=historical, current=current)
    assert (previous, historical, current) == before


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
