from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from chan_parser.contracts.segment_incremental_reconciliation import (
    SegmentIncrementalReconciliationAction,
    SegmentIncrementalReconciliationError,
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
