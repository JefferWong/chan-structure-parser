from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from chan_parser.contracts.segment_incremental_materialization import (
    SegmentIncrementalMaterializationError,
    SegmentIncrementalMaterializationResult,
    materialize_incremental_segment,
)
from chan_parser.contracts.segment_incremental_reconciliation import (
    SegmentIncrementalReconciliationAction,
)
from chan_parser.domain.lifecycle import StructureStatus, StrokeDirection
from chan_parser.domain.segment import Segment
from chan_parser.domain.stroke import Stroke
from chan_parser.engine.segment import SegmentEngineResult


def source_strokes(count: int = 5) -> tuple[Stroke, ...]:
    return tuple(
        Stroke(
            object_id=f"stroke-object-{index}",
            logical_id=f"stroke:logical:{index}",
            revision=1,
            status=StructureStatus.CONFIRMED,
            stroke_id=f"stroke_{index + 1:06d}",
            direction=(
                StrokeDirection.UP if index % 2 == 0 else StrokeDirection.DOWN
            ),
            start_fractal_id=f"fractal_{index:06d}",
            end_fractal_id=f"fractal_{index + 1:06d}",
            start_price=float(index),
            end_price=float(index + 1),
            start_bar_index=index,
            end_bar_index=index + 1,
        )
        for index in range(count)
    )


def candidate_segment(*, end_price: float = 3.0) -> Segment:
    return Segment(
        object_id="segment_000001_000004_U_r1",
        logical_id="segment:logical:1",
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


def first_case(candidate: Segment) -> SegmentEngineResult:
    return SegmentEngineResult(
        reason_code="SEGMENT_FIRST_CASE_CONFIRMED",
        candidate_direction=StrokeDirection.UP,
        feature_elements=(),
        segment=candidate,
        completed=True,
    )


def materialize(previous: Segment, candidate: Segment) -> SegmentIncrementalMaterializationResult:
    return materialize_incremental_segment(
        previous=previous,
        current=first_case(candidate),
        source_strokes=source_strokes(),
    )


def test_reuse_preserves_authenticated_identity_and_owns_mutable_storage():
    previous = candidate_segment()
    candidate = deepcopy(previous)
    before = deepcopy((previous, candidate))
    result = materialize(previous, candidate)

    assert result.action is SegmentIncrementalReconciliationAction.REUSE
    assert result.materialized_segment is not previous
    assert result.materialized_segment == previous
    assert result.previous_logical_id == previous.logical_id
    assert result.previous_object_id == previous.object_id
    assert result.previous_revision == previous.revision
    assert result.previous_content_hash == previous.content_hash()
    assert result.materialized_content_hash == previous.content_hash()
    assert result.materialized_segment.status is StructureStatus.CONFIRMED
    assert result.materialized_segment.invalidated_at_bar is None
    assert result.materialized_segment.replaced_by is None
    for field in (
        "stroke_ids",
        "feature_sequence_stroke_ids",
        "destruction_evidence_stroke_ids",
        "confirmation_requirements",
    ):
        assert getattr(result.materialized_segment, field) is not getattr(previous, field)
        assert getattr(result.materialized_segment, field) is not getattr(candidate, field)
    assert (previous, candidate) == before


def test_revise_derives_next_canonical_identity_and_preserves_candidate_semantics():
    previous = replace(candidate_segment(end_price=3.0), revision=2, object_id="segment_000001_000004_U_r2")
    candidate = candidate_segment(end_price=8.0)
    before = deepcopy((previous, candidate))
    result = materialize(previous, candidate)

    assert result.action is SegmentIncrementalReconciliationAction.REVISE
    materialized = result.materialized_segment
    assert materialized is not candidate
    assert materialized.logical_id == previous.logical_id
    assert materialized.revision == 3
    assert materialized.revision == result.canonical_reconciliation.next_revision
    assert materialized.object_id == f"{candidate.segment_id}_r3"
    assert materialized.content_hash() == candidate.content_hash()
    assert materialized.status is StructureStatus.CONFIRMED
    assert materialized.invalidated_at_bar is None
    assert materialized.replaced_by is None
    for field in (
        "segment_id",
        "direction",
        "start_stroke_id",
        "end_stroke_id",
        "stroke_ids",
        "feature_sequence_stroke_ids",
        "destruction_evidence_stroke_ids",
        "start_price",
        "end_price",
        "start_bar_index",
        "end_bar_index",
        "confirmation_requirements",
        "repaint_risk",
        "created_at_bar",
        "confirmed_at_bar",
        "created_at_raw_bar_index",
        "confirmed_at_raw_bar_index",
        "rule_profile",
        "rule_version",
    ):
        assert getattr(materialized, field) == getattr(candidate, field)
    for field in (
        "stroke_ids",
        "feature_sequence_stroke_ids",
        "destruction_evidence_stroke_ids",
        "confirmation_requirements",
    ):
        assert getattr(materialized, field) is not getattr(candidate, field)
    assert (previous, candidate) == before


def test_repeated_materialization_is_deterministic_and_does_not_publish_candidate_r1():
    previous = replace(candidate_segment(), revision=2, object_id="segment_000001_000004_U_r2")
    candidate = candidate_segment(end_price=8.0)
    first = materialize(previous, candidate)
    second = materialize(deepcopy(previous), deepcopy(candidate))

    assert first == second
    assert first.materialized_object_id == "segment_000001_000004_U_r3"
    assert first.materialized_object_id != candidate.object_id


@pytest.mark.parametrize("case", [
    "previous_type",
    "previous_status",
    "previous_invalidated",
    "previous_replaced",
    "current_type",
    "nonmaterialized",
    "second_case",
    "source_type",
    "malformed_stroke",
    "source_binding",
    "candidate_lifecycle",
    "candidate_invalidated",
    "candidate_replaced",
    "candidate_revision",
    "candidate_object_id",
    "no_previous",
    "replace_required",
])
def test_materializer_rejects_unsupported_or_unauthenticated_inputs(case):
    previous = candidate_segment()
    candidate = candidate_segment()
    current = first_case(candidate)
    source = source_strokes()
    if case == "previous_type":
        previous = object()
    elif case == "previous_status":
        previous = replace(previous, status=StructureStatus.PROVISIONAL)
    elif case == "previous_invalidated":
        previous = replace(previous, invalidated_at_bar=9)
    elif case == "previous_replaced":
        previous = replace(previous, replaced_by="segment_2_r1")
    elif case == "current_type":
        current = object()
    elif case == "nonmaterialized":
        current = SegmentEngineResult(
            reason_code="SEGMENT_FEATURE_WINDOW_INCOMPLETE",
            candidate_direction=StrokeDirection.UP,
            feature_elements=(),
        )
    elif case == "second_case":
        current = SegmentEngineResult(
            reason_code="SEGMENT_SECOND_CASE_PENDING",
            candidate_direction=StrokeDirection.UP,
            feature_elements=(),
            pending_second_case=object(),
        )
    elif case == "source_type":
        source = "not-a-source"
    elif case == "malformed_stroke":
        source = (object(), *source[1:])
    elif case == "source_binding":
        current = first_case(
            replace(
                candidate,
                stroke_ids=["stroke_000005", "stroke_000002", "stroke_000003"],
            )
        )
    elif case == "candidate_lifecycle":
        current = first_case(replace(candidate, status=StructureStatus.PROVISIONAL))
    elif case == "candidate_invalidated":
        current = first_case(replace(candidate, invalidated_at_bar=9))
    elif case == "candidate_replaced":
        current = first_case(replace(candidate, replaced_by="segment_2_r1"))
    elif case == "candidate_revision":
        current = first_case(replace(candidate, revision=2, object_id="segment_000001_000004_U_r2"))
    elif case == "candidate_object_id":
        current = first_case(replace(candidate, object_id="random-id"))
    elif case == "no_previous":
        previous = None
    else:
        current = first_case(replace(candidate, logical_id="segment:logical:other"))
    with pytest.raises(SegmentIncrementalMaterializationError):
        materialize_incremental_segment(
            previous=previous,
            current=current,
            source_strokes=source,
        )
