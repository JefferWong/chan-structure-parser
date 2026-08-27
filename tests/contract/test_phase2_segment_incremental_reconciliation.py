from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from chan_parser.contracts.segment_incremental_reconciliation import (
    SegmentIncrementalReconciliationAction,
    SegmentIncrementalReconciliationDecision,
    SegmentIncrementalReconciliationError,
    SegmentIncrementalTransientPolicyAction,
    SegmentIncrementalTransientPolicyError,
    evaluate_incremental_segment_transient_policy,
    reconcile_incremental_segment,
)
from chan_parser.domain.lifecycle import StructureStatus, StrokeDirection
from chan_parser.domain.segment import Segment
from chan_parser.domain.stroke import Stroke
from chan_parser.engine.segment import SegmentEngineResult


def strokes(count: int = 5) -> tuple[Stroke, ...]:
    result = []
    for index in range(count):
        direction = StrokeDirection.UP if index % 2 == 0 else StrokeDirection.DOWN
        result.append(
            Stroke(
                object_id=f"stroke-object-{index}",
                logical_id=f"stroke:logical:{index}",
                revision=1,
                status=StructureStatus.CONFIRMED,
                stroke_id=f"stroke_{index + 1:06d}",
                direction=direction,
                start_fractal_id=f"fractal_{index:06d}",
                end_fractal_id=f"fractal_{index + 1:06d}",
                start_price=float(index),
                end_price=float(index + 1),
                start_bar_index=index,
                end_bar_index=index + 1,
            )
        )
    return tuple(result)


def segment(*, logical_id: str = "segment:logical:1", end_price: float = 3.0) -> Segment:
    return Segment(
        object_id="segment-object-r1",
        logical_id=logical_id,
        revision=1,
        status=StructureStatus.CONFIRMED,
        created_at_bar=4,
        confirmed_at_bar=4,
        rule_profile="minimal_segment_engine_core_v1",
        rule_version="0.1.0",
        segment_id="segment_000001_000004_U",
        direction=StrokeDirection.UP,
        start_stroke_id="stroke_000001",
        end_stroke_id="stroke_000003",
        stroke_ids=["stroke_000001", "stroke_000002", "stroke_000003"],
        feature_sequence_stroke_ids=["stroke_000002"],
        destruction_evidence_stroke_ids=["stroke_000002"],
        start_price=0.0,
        end_price=end_price,
        start_bar_index=0,
        end_bar_index=3,
        confirmation_requirements=[],
        repaint_risk="NONE",
    )


def first_case(candidate: Segment | None = None, **overrides) -> SegmentEngineResult:
    values = {
        "reason_code": "SEGMENT_FIRST_CASE_CONFIRMED",
        "candidate_direction": StrokeDirection.UP,
        "feature_elements": (),
        "segment": candidate or segment(),
        "completed": True,
    }
    values.update(overrides)
    return SegmentEngineResult(**values)


def nonmaterialized(reason_code: str) -> SegmentEngineResult:
    return SegmentEngineResult(
        reason_code=reason_code,
        candidate_direction=StrokeDirection.UP,
        feature_elements=(),
        pending_second_case=(object() if reason_code == "SEGMENT_SECOND_CASE_PENDING" else None),
    )


@pytest.mark.parametrize(
    "reason_code",
    [
        "SEGMENT_FEATURE_WINDOW_INCOMPLETE",
        "SEGMENT_PRIMARY_FRACTAL_NOT_FOUND",
    ],
)
def test_transient_policy_retains_previous_only_with_preserved_source(reason_code):
    previous = segment()
    source = strokes()
    decision = evaluate_incremental_segment_transient_policy(
        previous=previous,
        current=nonmaterialized(reason_code),
        previous_source_strokes=source,
        current_source_strokes=source,
    )
    assert decision.action is SegmentIncrementalTransientPolicyAction.RETAIN_PREVIOUS
    assert decision.current_outcome_code == reason_code
    assert decision.source_continuity_action.value == "PRESERVED"
    assert decision.bound_prefix_length == 3
    assert decision.previous_logical_id == previous.logical_id
    assert decision.previous_object_id == previous.object_id
    assert decision.previous_revision == previous.revision
    assert decision.previous_content_hash == previous.content_hash()


@pytest.mark.parametrize(
    "reason_code",
    [
        "SEGMENT_FEATURE_WINDOW_INCOMPLETE",
        "SEGMENT_PRIMARY_FRACTAL_NOT_FOUND",
        "SEGMENT_SECOND_CASE_PENDING",
    ],
)
def test_transient_policy_fails_closed_for_broken_source_or_second_case(reason_code):
    previous = segment()
    historical = strokes()
    current = nonmaterialized(reason_code)
    changed = replace(historical[0], end_price=99.0)
    current_source = (changed, *historical[1:])
    decision = evaluate_incremental_segment_transient_policy(
        previous=previous,
        current=current,
        previous_source_strokes=historical,
        current_source_strokes=(historical if reason_code == "SEGMENT_SECOND_CASE_PENDING" else current_source),
    )
    assert decision.action is SegmentIncrementalTransientPolicyAction.FAIL_CLOSED
    if reason_code == "SEGMENT_SECOND_CASE_PENDING":
        assert decision.source_continuity_action.value == "PRESERVED"
    else:
        assert decision.source_continuity_action.value == "BROKEN"


def test_transient_policy_rejects_malformed_inputs_and_is_deterministic_and_pure():
    previous = segment()
    source = strokes()
    current = nonmaterialized("SEGMENT_FEATURE_WINDOW_INCOMPLETE")
    before = deepcopy((previous, current, source))
    first = evaluate_incremental_segment_transient_policy(
        previous=previous,
        current=current,
        previous_source_strokes=source,
        current_source_strokes=source,
    )
    second = evaluate_incremental_segment_transient_policy(
        previous=deepcopy(previous),
        current=deepcopy(current),
        previous_source_strokes=deepcopy(source),
        current_source_strokes=deepcopy(source),
    )
    assert first == second
    assert (previous, current, source) == before

    with pytest.raises(SegmentIncrementalTransientPolicyError):
        evaluate_incremental_segment_transient_policy(
            previous=replace(previous, status=StructureStatus.INVALIDATED),
            current=current,
            previous_source_strokes=source,
            current_source_strokes=source,
        )
    with pytest.raises(SegmentIncrementalTransientPolicyError):
        evaluate_incremental_segment_transient_policy(
            previous=previous,
            current=replace(current, segment=segment()),
            previous_source_strokes=source,
            current_source_strokes=source,
        )
    with pytest.raises(SegmentIncrementalTransientPolicyError):
        evaluate_incremental_segment_transient_policy(
            previous=previous,
            current=replace(
                current,
                reason_code="SEGMENT_SECOND_CASE_PENDING",
                pending_second_case=None,
            ),
            previous_source_strokes=source,
            current_source_strokes=source,
        )
    with pytest.raises(SegmentIncrementalTransientPolicyError):
        evaluate_incremental_segment_transient_policy(
            previous=previous,
            current=first_case(),
            previous_source_strokes=source,
            current_source_strokes=source,
        )


@pytest.mark.parametrize(
    "reason_code",
    [
        "SEGMENT_FEATURE_WINDOW_INCOMPLETE",
        "SEGMENT_PRIMARY_FRACTAL_NOT_FOUND",
        "SEGMENT_SECOND_CASE_PENDING",
    ],
)
def test_no_previous_accepts_nonmaterialized_without_creating_segment(reason_code):
    decision = reconcile_incremental_segment(
        previous=None,
        current=nonmaterialized(reason_code),
        source_strokes=strokes(),
    )
    assert decision.action is SegmentIncrementalReconciliationAction.NO_PREVIOUS
    assert decision.candidate_logical_id is None
    assert decision.candidate_content_hash is None
    assert decision.next_revision is None


def test_no_previous_first_case_only_reports_absent_previous_identity():
    candidate = segment()
    decision = reconcile_incremental_segment(
        previous=None, current=first_case(candidate), source_strokes=strokes()
    )
    assert decision.action is SegmentIncrementalReconciliationAction.NO_PREVIOUS
    assert decision.candidate_logical_id == candidate.logical_id
    assert decision.candidate_content_hash == candidate.content_hash()
    assert decision.next_revision is None


def test_same_logical_and_content_reuses_previous_identity():
    previous = replace(segment(), object_id="durable-object-r7", revision=7)
    candidate = segment()
    decision = reconcile_incremental_segment(
        previous=previous, current=first_case(candidate), source_strokes=strokes()
    )
    assert decision.action is SegmentIncrementalReconciliationAction.REUSE
    assert decision.previous_object_id == "durable-object-r7"
    assert decision.previous_revision == 7
    assert decision.next_revision == 7
    assert decision.candidate_logical_id == previous.logical_id


def test_same_logical_changed_content_requires_next_revision_only():
    previous = replace(segment(), object_id="durable-object-r4", revision=4)
    candidate = segment(end_price=9.0)
    decision = reconcile_incremental_segment(
        previous=previous, current=first_case(candidate), source_strokes=strokes()
    )
    assert decision.action is SegmentIncrementalReconciliationAction.REVISE
    assert decision.previous_object_id == "durable-object-r4"
    assert decision.next_revision == 5
    assert candidate.object_id == "segment-object-r1"
    assert candidate.revision == 1


def test_different_logical_identity_requires_future_replacement_semantics():
    previous = segment()
    candidate = segment(logical_id="segment:logical:other")
    decision = reconcile_incremental_segment(
        previous=previous, current=first_case(candidate), source_strokes=strokes()
    )
    assert decision.action is SegmentIncrementalReconciliationAction.REPLACE_REQUIRED
    assert decision.next_revision is None
    assert previous.replaced_by is None


@pytest.mark.parametrize(
    "reason_code",
    [
        "SEGMENT_FEATURE_WINDOW_INCOMPLETE",
        "SEGMENT_PRIMARY_FRACTAL_NOT_FOUND",
        "SEGMENT_SECOND_CASE_PENDING",
    ],
)
def test_previous_with_nonmaterialized_current_fails_closed(reason_code):
    with pytest.raises(SegmentIncrementalReconciliationError) as raised:
        reconcile_incremental_segment(
            previous=segment(),
            current=nonmaterialized(reason_code),
            source_strokes=strokes(),
        )
    assert raised.value.reason_code == (
        "SEGMENT_RECONCILIATION_PREVIOUS_WITH_NONMATERIALIZED_CURRENT_UNSUPPORTED"
    )


@pytest.mark.parametrize(
    "previous",
    [
        replace(segment(), status=StructureStatus.PROVISIONAL),
        replace(segment(), status=StructureStatus.INVALIDATED),
        replace(segment(), status=StructureStatus.REPLACED),
        replace(segment(), logical_id=""),
        replace(segment(), object_id=""),
        replace(segment(), revision=0),
        replace(segment(), revision=True),
    ],
)
def test_malformed_previous_fails_closed(previous):
    with pytest.raises(SegmentIncrementalReconciliationError):
        reconcile_incremental_segment(
            previous=previous, current=first_case(), source_strokes=strokes()
        )


@pytest.mark.parametrize(
    "previous",
    [
        replace(segment(), revision=-1),
        replace(segment(), revision=1.0),
        replace(segment(), segment_id=""),
        replace(segment(), replaced_by="replacement-object"),
        replace(segment(), invalidated_at_bar=10),
    ],
)
def test_additional_malformed_previous_states_fail_closed(previous):
    with pytest.raises(SegmentIncrementalReconciliationError) as raised:
        reconcile_incremental_segment(
            previous=previous, current=first_case(), source_strokes=strokes()
        )
    assert raised.value.reason_code.startswith("SEGMENT_RECONCILIATION_PREVIOUS_")


@pytest.mark.parametrize(
    "current",
    [
        first_case(segment=None, completed=False),
        first_case(pending_second_case=object()),
        replace(nonmaterialized("SEGMENT_FEATURE_WINDOW_INCOMPLETE"), segment=segment()),
        replace(nonmaterialized("SEGMENT_PRIMARY_FRACTAL_NOT_FOUND"), completed=True),
        SegmentEngineResult(
            reason_code="SEGMENT_SECOND_CASE_PENDING",
            candidate_direction=StrokeDirection.UP,
            feature_elements=(),
        ),
        replace(nonmaterialized("SEGMENT_FEATURE_WINDOW_INCOMPLETE"), pending_second_case=object()),
    ],
)
def test_contradictory_current_result_fails_closed(current):
    with pytest.raises(SegmentIncrementalReconciliationError):
        reconcile_incremental_segment(
            previous=None, current=current, source_strokes=strokes()
        )


def test_current_requires_exact_result_type_and_direction_type():
    with pytest.raises(SegmentIncrementalReconciliationError) as fake_error:
        reconcile_incremental_segment(
            previous=None,
            current=object(),
            source_strokes=strokes(),
        )
    assert fake_error.value.reason_code == "SEGMENT_RECONCILIATION_CURRENT_TYPE_INVALID"

    wrong_direction = replace(first_case(), candidate_direction="UP")
    with pytest.raises(SegmentIncrementalReconciliationError) as direction_error:
        reconcile_incremental_segment(
            previous=None,
            current=wrong_direction,
            source_strokes=strokes(),
        )
    assert direction_error.value.reason_code == (
        "SEGMENT_RECONCILIATION_CANDIDATE_DIRECTION_INVALID"
    )


def test_first_case_candidate_direction_must_match_result():
    current = replace(first_case(), candidate_direction=StrokeDirection.DOWN)
    with pytest.raises(SegmentIncrementalReconciliationError) as raised:
        reconcile_incremental_segment(
            previous=None, current=current, source_strokes=strokes()
        )
    assert raised.value.reason_code == (
        "SEGMENT_RECONCILIATION_CANDIDATE_DIRECTION_MISMATCH"
    )


@pytest.mark.parametrize(
    "candidate,reason_code",
    [
        (
            replace(segment(), invalidated_at_bar=10),
            "SEGMENT_RECONCILIATION_CANDIDATE_LIFECYCLE_STATE_INVALID",
        ),
        (
            replace(segment(), replaced_by="replacement-object"),
            "SEGMENT_RECONCILIATION_CANDIDATE_LIFECYCLE_STATE_INVALID",
        ),
        (
            replace(segment(), start_stroke_id="stroke_000002"),
            "SEGMENT_RECONCILIATION_CANDIDATE_SOURCE_BINDING_INVALID",
        ),
        (
            replace(segment(), end_stroke_id="stroke_000002"),
            "SEGMENT_RECONCILIATION_CANDIDATE_SOURCE_BINDING_INVALID",
        ),
    ],
)
def test_first_case_candidate_lifecycle_and_boundaries_fail_closed(
    candidate, reason_code
):
    with pytest.raises(SegmentIncrementalReconciliationError) as raised:
        reconcile_incremental_segment(
            previous=None, current=first_case(candidate), source_strokes=strokes()
        )
    assert raised.value.reason_code == reason_code


def test_new_candidate_failure_paths_are_input_pure():
    previous = replace(segment(), object_id="previous-object-r1")
    source = strokes()
    for candidate in (
        replace(segment(), invalidated_at_bar=10),
        replace(segment(), replaced_by="replacement-object"),
        replace(segment(), start_stroke_id="stroke_000002"),
        replace(segment(), end_stroke_id="stroke_000002"),
    ):
        current = first_case(candidate)
        before = deepcopy((previous, current, source))
        with pytest.raises(SegmentIncrementalReconciliationError):
            reconcile_incremental_segment(
                previous=previous, current=current, source_strokes=source
            )
        assert (previous, current, source) == before


def test_candidate_source_must_be_exact_nonempty_ordered_prefix():
    candidate = segment()
    candidate.stroke_ids = ["stroke_000002", "stroke_000001"]
    with pytest.raises(SegmentIncrementalReconciliationError) as raised:
        reconcile_incremental_segment(
            previous=None, current=first_case(candidate), source_strokes=strokes()
        )
    assert raised.value.reason_code == (
        "SEGMENT_RECONCILIATION_CANDIDATE_SOURCE_BINDING_INVALID"
    )


def test_source_binding_accepts_prefix_with_unrelated_confirmed_tail():
    decision = reconcile_incremental_segment(
        previous=None, current=first_case(), source_strokes=strokes(5)
    )
    assert decision.action is SegmentIncrementalReconciliationAction.NO_PREVIOUS


@pytest.mark.parametrize(
    "source_factory",
    [
        lambda: (),
        lambda: (object(),),
        lambda: (replace(strokes()[0], status=StructureStatus.PROVISIONAL),),
        lambda: strokes()[:1] + (replace(strokes()[1], stroke_id="stroke_000001"),) + strokes()[2:],
        lambda: strokes()[:1] + (replace(strokes()[1], logical_id="stroke:logical:0"),) + strokes()[2:],
        lambda: strokes()[:1] + (replace(strokes()[1], object_id="stroke-object-0"),) + strokes()[2:],
    ],
)
def test_malformed_source_evidence_fails_closed(source_factory):
    with pytest.raises(SegmentIncrementalReconciliationError):
        reconcile_incremental_segment(
            previous=None, current=first_case(), source_strokes=source_factory()
        )


@pytest.mark.parametrize(
    "candidate_ids",
    [
        ["stroke_000001", "stroke_000003"],
        ["stroke_000002", "stroke_000001", "stroke_000003"],
        ["stroke_000003", "stroke_000004", "stroke_000005"],
        ["stroke_999999"],
        [f"stroke_{index + 1:06d}" for index in range(6)],
        [],
    ],
)
def test_candidate_source_binding_rejects_nonprefix_shapes(candidate_ids):
    candidate = segment()
    candidate.stroke_ids = candidate_ids
    with pytest.raises(SegmentIncrementalReconciliationError) as raised:
        reconcile_incremental_segment(
            previous=None, current=first_case(candidate), source_strokes=strokes()
        )
    assert raised.value.reason_code == (
        "SEGMENT_RECONCILIATION_CANDIDATE_SOURCE_BINDING_INVALID"
    )


def test_segment_content_hash_method_is_authoritative(monkeypatch):
    previous = replace(segment(), object_id="previous-r8", revision=8, end_price=1.0)
    candidate = replace(segment(), object_id="fresh-r1", end_price=99.0)
    calls = []

    def controlled_hash(value):
        calls.append(value.object_id)
        return "controlled-equal-hash"

    monkeypatch.setattr(Segment, "content_hash", controlled_hash)
    reuse = reconcile_incremental_segment(
        previous=previous, current=first_case(candidate), source_strokes=strokes()
    )
    assert reuse.action is SegmentIncrementalReconciliationAction.REUSE
    assert calls.count("previous-r8") == 2
    assert calls.count("fresh-r1") == 2


def decision_fields(**overrides):
    values = {
        "action": SegmentIncrementalReconciliationAction.REUSE,
        "reason_code": "SEGMENT_RECONCILIATION_IDENTITY_REUSED",
        "previous_logical_id": "logical",
        "previous_object_id": "object-r3",
        "previous_revision": 3,
        "previous_content_hash": "same-hash",
        "candidate_logical_id": "logical",
        "candidate_content_hash": "same-hash",
        "next_revision": 3,
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    "overrides",
    [
        {"action": "REUSE"},
        {"action": "anything"},
        {"candidate_logical_id": "other"},
        {"candidate_content_hash": "different"},
        {"next_revision": 4},
        {"previous_revision": True},
    ],
)
def test_decision_rejects_unknown_action_type_and_reuse_contradictions(overrides):
    with pytest.raises(SegmentIncrementalReconciliationError):
        SegmentIncrementalReconciliationDecision(**decision_fields(**overrides))


def test_decision_enforces_revise_replace_and_no_previous_invariants():
    with pytest.raises(SegmentIncrementalReconciliationError):
        SegmentIncrementalReconciliationDecision(
            **decision_fields(
                action=SegmentIncrementalReconciliationAction.REVISE,
                reason_code="SEGMENT_RECONCILIATION_CONTENT_CHANGED",
                candidate_content_hash="different",
                next_revision=3,
            )
        )
    with pytest.raises(SegmentIncrementalReconciliationError):
        SegmentIncrementalReconciliationDecision(
            **decision_fields(
                action=SegmentIncrementalReconciliationAction.REPLACE_REQUIRED,
                reason_code="SEGMENT_RECONCILIATION_LOGICAL_ID_CHANGED",
                candidate_logical_id="logical",
                next_revision=None,
            )
        )
    with pytest.raises(SegmentIncrementalReconciliationError):
        SegmentIncrementalReconciliationDecision(
            **decision_fields(
                action=SegmentIncrementalReconciliationAction.NO_PREVIOUS,
                reason_code="SEGMENT_RECONCILIATION_NO_PREVIOUS_NONMATERIALIZED",
                next_revision=None,
            )
        )


@pytest.mark.parametrize(
    "action,reason_code,candidate_content_hash",
    [
        (
            SegmentIncrementalReconciliationAction.REUSE,
            "SEGMENT_RECONCILIATION_IDENTITY_REUSED",
            "same-hash",
        ),
        (
            SegmentIncrementalReconciliationAction.REVISE,
            "SEGMENT_RECONCILIATION_CONTENT_CHANGED",
            "different",
        ),
    ],
)
def test_decision_rejects_bool_next_revision_for_reuse_and_revise(
    action, reason_code, candidate_content_hash
):
    with pytest.raises(SegmentIncrementalReconciliationError) as raised:
        SegmentIncrementalReconciliationDecision(
            **decision_fields(
                action=action,
                reason_code=reason_code,
                candidate_content_hash=candidate_content_hash,
                next_revision=True,
            )
        )
    assert raised.value.reason_code == "SEGMENT_RECONCILIATION_NEXT_REVISION_TYPE_INVALID"


def test_repeated_deepcopied_semantically_equal_inputs_are_deterministic_and_pure():
    previous = replace(segment(), object_id="durable-object-r3", revision=3)
    current = first_case(segment(end_price=8.0))
    source = strokes()
    before = deepcopy((previous, current, source))

    first = reconcile_incremental_segment(
        previous=previous, current=current, source_strokes=source
    )
    second = reconcile_incremental_segment(
        previous=deepcopy(previous),
        current=deepcopy(current),
        source_strokes=deepcopy(source),
    )

    assert first == second
    assert (previous, current, source) == before


@pytest.mark.parametrize(
    "previous,current",
    [
        (segment(), first_case()),
        (segment(), first_case(segment(end_price=8.0))),
        (segment(), first_case(segment(logical_id="segment:logical:other"))),
        (None, nonmaterialized("SEGMENT_FEATURE_WINDOW_INCOMPLETE")),
    ],
)
def test_every_success_action_is_pure(previous, current):
    source = strokes()
    before = deepcopy((previous, current, source))
    reconcile_incremental_segment(
        previous=previous, current=current, source_strokes=source
    )
    assert (previous, current, source) == before


def test_fail_closed_path_is_pure():
    previous = segment()
    current = nonmaterialized("SEGMENT_SECOND_CASE_PENDING")
    source = strokes()
    previous_before = deepcopy(previous)
    source_before = deepcopy(source)
    current_before = (
        current.reason_code,
        current.candidate_direction,
        current.feature_elements,
        current.primary_evidence,
        current.pending_second_case,
        current.segment,
        current.completed,
    )
    with pytest.raises(SegmentIncrementalReconciliationError) as raised:
        reconcile_incremental_segment(
            previous=previous, current=current, source_strokes=source
        )
    assert raised.value.reason_code == (
        "SEGMENT_RECONCILIATION_PREVIOUS_WITH_NONMATERIALIZED_CURRENT_UNSUPPORTED"
    )
    assert previous == previous_before
    assert source == source_before
    assert (
        current.reason_code,
        current.candidate_direction,
        current.feature_elements,
        current.primary_evidence,
        current.pending_second_case,
        current.segment,
        current.completed,
    ) == current_before
