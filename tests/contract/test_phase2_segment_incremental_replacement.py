from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from chan_parser.contracts.segment_incremental_reconciliation import (
    SegmentIncrementalReconciliationAction,
    SegmentIncrementalReconciliationDecision,
    SegmentIncrementalReconciliationError,
)
from chan_parser.contracts.segment_incremental_replacement import (
    SegmentIncrementalReplacementError,
    SegmentIncrementalReplacementResult,
    materialize_incremental_segment_replacement,
)
from chan_parser.domain.lifecycle import EventType, StructureStatus, StrokeDirection
from chan_parser.domain.segment import Segment
from chan_parser.domain.stroke import Stroke
from chan_parser.engine.segment import SegmentEngine, SegmentEngineResult


def source_strokes() -> tuple[Stroke, ...]:
    points = [0, 10, 4, 12, 6, 11, 5]
    return tuple(
        Stroke(
            object_id=f"stroke-object-{index}",
            logical_id=f"stroke:logical:{index}",
            revision=1,
            status=StructureStatus.CONFIRMED,
            stroke_id=f"stroke_{index + 1:06d}",
            direction=(
                StrokeDirection.UP if start < end else StrokeDirection.DOWN
            ),
            start_fractal_id=f"fractal_{index:06d}",
            end_fractal_id=f"fractal_{index + 1:06d}",
            start_price=start,
            end_price=end,
            start_bar_index=index,
            end_bar_index=index + 1,
            merged_bar_count=2,
            max_price=max(start, end),
            min_price=min(start, end),
            price_range=abs(end - start),
            created_at_bar=index + 1,
            confirmed_at_bar=index + 1,
        )
        for index, (start, end) in enumerate(zip(points, points[1:]))
    )


def first_case() -> tuple[SegmentEngineResult, tuple[Stroke, ...]]:
    source = source_strokes()
    result = SegmentEngine(SegmentEngine.reference_profile()).process_primary(
        source, sequence_id="replacement:test"
    )
    return result, source


def previous_for(
    candidate: Segment,
    *,
    logical_id: str = "segment:previous",
    revision: int = 2,
    object_id: str | None = None,
) -> Segment:
    return replace(
        candidate,
        logical_id=logical_id,
        revision=revision,
        object_id=object_id or f"{candidate.segment_id}_r{revision}",
    )


def replacement_result() -> SegmentIncrementalReplacementResult:
    current, source = first_case()
    assert current.segment is not None
    return materialize_incremental_segment_replacement(
        previous=previous_for(current.segment),
        current=current,
        source_strokes=source,
    )


def test_replace_required_materializes_independent_records_and_ordered_intents():
    current, source = first_case()
    assert current.segment is not None
    previous = previous_for(current.segment)
    before = deepcopy((previous, current, source))
    result = materialize_incremental_segment_replacement(
        previous=previous,
        current=current,
        source_strokes=source,
    )

    assert result.action is SegmentIncrementalReconciliationAction.REPLACE_REQUIRED
    assert result.canonical_reconciliation.reason_code == (
        "SEGMENT_RECONCILIATION_LOGICAL_ID_CHANGED"
    )
    assert result.previous_logical_id == previous.logical_id
    assert result.previous_object_id == previous.object_id
    assert result.previous_revision == previous.revision
    assert result.replaced_previous is not previous
    assert result.replacement_segment is not current.segment
    assert result.replaced_previous.status is StructureStatus.REPLACED
    assert result.replaced_previous.replaced_by == result.replacement_segment.object_id
    assert result.replaced_previous.invalidated_at_bar is None
    assert result.replaced_previous.logical_id == previous.logical_id
    assert result.replaced_previous.object_id == previous.object_id
    assert result.replaced_previous.revision == previous.revision
    assert result.replaced_previous.content_hash() == previous.content_hash()
    assert result.replacement_segment.status is StructureStatus.CONFIRMED
    assert result.replacement_segment.logical_id == current.segment.logical_id
    assert result.replacement_segment.object_id == current.segment.object_id
    assert result.replacement_segment.revision == 1
    assert result.replacement_segment.replaced_by is None
    assert result.replacement_segment.invalidated_at_bar is None
    assert result.replacement_segment.content_hash() == current.segment.content_hash()
    assert tuple(intent.event_type for intent in result.replacement_segment_lifecycle_intents) == (
        EventType.CREATED,
        EventType.CONFIRMED,
    )
    assert result.previous_replacement_intent.event_type == EventType.STRUCTURE_REPLACED
    assert result.previous_replacement_intent.object_id == previous.object_id
    assert result.previous_replacement_intent.logical_id == previous.logical_id
    assert result.previous_replacement_intent.replaced_by == current.segment.object_id
    assert result.previous_replacement_intent.reason_code == (
        "SEGMENT_RECONCILIATION_LOGICAL_ID_CHANGED"
    )
    assert result.lifecycle_order == (
        EventType.CREATED,
        EventType.CONFIRMED,
        EventType.STRUCTURE_REPLACED,
    )
    assert result.previous_replacement_intent.occurred_at_bar_id == (
        f"bar_{current.segment.confirmed_at_bar + 1:06d}"
    )
    assert (previous, current, source) == before

    for field in (
        "stroke_ids",
        "feature_sequence_stroke_ids",
        "destruction_evidence_stroke_ids",
        "confirmation_requirements",
    ):
        assert getattr(result.replaced_previous, field) is not getattr(previous, field)
        assert getattr(result.replacement_segment, field) is not getattr(current.segment, field)


def test_replacement_copies_are_mutable_independently_after_materialization():
    result = replacement_result()
    current, source = first_case()
    assert current.segment is not None
    previous = previous_for(current.segment)
    result.replaced_previous.stroke_ids.append("old-only")
    result.replacement_segment.stroke_ids.append("new-only")
    assert "old-only" not in previous.stroke_ids
    assert "new-only" not in current.segment.stroke_ids
    assert result.replaced_previous is not result.replacement_segment
    assert source[0].stroke_id == "stroke_000001"


def test_replacement_is_deterministic_and_uses_nonhashed_intent_key():
    first = replacement_result()
    second = replacement_result()
    assert first == second
    assert first.previous_replacement_intent.intent_key == (
        f"segment_replacement:{first.previous_object_id}:"
        f"{first.replacement_object_id}:{first.previous_replacement_intent.occurred_at_bar_id}"
    )
    assert not first.previous_replacement_intent.intent_key.startswith("sha256:")


@pytest.mark.parametrize(
    "tamper",
    [
        "previous_identity",
        "replacement_identity",
        "replacement_revision",
        "previous_hash",
        "replacement_hash",
        "replaced_by",
        "previous_status",
        "replacement_status",
        "reconciliation_action",
        "reconciliation_reason",
        "candidate_event_type",
        "candidate_event_object_id",
        "candidate_created_intent_key",
        "candidate_confirmed_intent_key",
        "previous_event_logical_id",
        "previous_event_replaced_by",
        "previous_event_timestamp",
        "previous_event_reason",
        "lifecycle_order",
    ],
)
def test_replacement_result_rejects_direct_envelope_tampering(tamper):
    result = replacement_result()
    replacement = result.replacement_segment
    previous = result.replaced_previous
    expected_errors = (
        SegmentIncrementalReplacementError,
        SegmentIncrementalReconciliationError,
    )
    with pytest.raises(expected_errors):
        if tamper == "previous_identity":
            replace(result, previous_object_id="forged")
        elif tamper == "replacement_identity":
            replace(result, replacement_object_id="forged")
        elif tamper == "replacement_revision":
            replace(result, replacement_revision=2)
        elif tamper == "previous_hash":
            replace(result, previous_content_hash="forged")
        elif tamper == "replacement_hash":
            replace(result, replacement_content_hash="forged")
        elif tamper == "replaced_by":
            replace(
                result,
                replaced_previous=replace(previous, replaced_by="wrong"),
            )
        elif tamper == "previous_status":
            replace(
                result,
                replaced_previous=replace(previous, status=StructureStatus.CONFIRMED),
            )
        elif tamper == "replacement_status":
            replace(
                result,
                replacement_segment=replace(
                    replacement, status=StructureStatus.REPLACED
                ),
            )
        elif tamper == "reconciliation_action":
            replace(
                result,
                action=SegmentIncrementalReconciliationAction.REVISE,
            )
        elif tamper == "reconciliation_reason":
            replace(
                result,
                canonical_reconciliation=replace(
                    result.canonical_reconciliation,
                    reason_code="SEGMENT_RECONCILIATION_CONTENT_CHANGED",
                ),
            )
        elif tamper == "candidate_event_type":
            intents = (
                replace(
                    result.replacement_segment_lifecycle_intents[0],
                    event_type=EventType.CONFIRMED,
                ),
                result.replacement_segment_lifecycle_intents[1],
            )
            replace(result, replacement_segment_lifecycle_intents=intents)
        elif tamper == "candidate_event_object_id":
            intent = replace(
                result.replacement_segment_lifecycle_intents[0],
                object_id="forged",
            )
            replace(
                result,
                replacement_segment_lifecycle_intents=(
                    intent,
                    result.replacement_segment_lifecycle_intents[1],
                ),
            )
        elif tamper == "candidate_created_intent_key":
            intent = replace(
                result.replacement_segment_lifecycle_intents[0],
                intent_key="forged",
            )
            replace(
                result,
                replacement_segment_lifecycle_intents=(
                    intent,
                    result.replacement_segment_lifecycle_intents[1],
                ),
            )
        elif tamper == "candidate_confirmed_intent_key":
            intent = replace(
                result.replacement_segment_lifecycle_intents[1],
                intent_key="forged",
            )
            replace(
                result,
                replacement_segment_lifecycle_intents=(
                    result.replacement_segment_lifecycle_intents[0],
                    intent,
                ),
            )
        elif tamper == "previous_event_logical_id":
            replace(
                result,
                previous_replacement_intent=replace(
                    result.previous_replacement_intent,
                    logical_id="forged",
                ),
            )
        elif tamper == "previous_event_replaced_by":
            replace(
                result,
                previous_replacement_intent=replace(
                    result.previous_replacement_intent,
                    replaced_by="forged",
                ),
            )
        elif tamper == "previous_event_timestamp":
            replace(
                result,
                previous_replacement_intent=replace(
                    result.previous_replacement_intent,
                    occurred_at_bar_id="bar_999999",
                ),
            )
        elif tamper == "previous_event_reason":
            replace(
                result,
                previous_replacement_intent=replace(
                    result.previous_replacement_intent,
                    reason_code="wrong",
                ),
            )
        else:
            replace(
                result,
                lifecycle_order=(
                    EventType.CONFIRMED,
                    EventType.CREATED,
                    EventType.STRUCTURE_REPLACED,
                ),
            )


@pytest.mark.parametrize("scenario", ["no_previous", "reuse", "revise", "nonmaterialized", "second_case", "malformed_source", "candidate_r1", "previous_rn", "collision"])
def test_replacement_materializer_fails_closed_for_unsupported_actions_and_inputs(scenario):
    current, source = first_case()
    assert current.segment is not None
    candidate = current.segment
    previous = previous_for(candidate)
    if scenario == "no_previous":
        previous = None
    elif scenario == "reuse":
        previous = previous_for(candidate, logical_id=candidate.logical_id)
    elif scenario == "revise":
        previous = previous_for(replace(candidate, end_price=11), logical_id=candidate.logical_id)
    elif scenario == "nonmaterialized":
        current = SegmentEngineResult("SEGMENT_FEATURE_WINDOW_INCOMPLETE", candidate.direction, ())
    elif scenario == "second_case":
        current = SegmentEngineResult("SEGMENT_SECOND_CASE_PENDING", candidate.direction, (), pending_second_case=object())
    elif scenario == "malformed_source":
        source = "bad-source"
    elif scenario == "candidate_r1":
        current = replace(current, segment=replace(candidate, revision=2, object_id=f"{candidate.segment_id}_r2"))
    elif scenario == "previous_rn":
        previous = replace(previous, object_id="not-canonical")
    else:
        previous = replace(previous, object_id=candidate.object_id, revision=1)
    with pytest.raises(SegmentIncrementalReplacementError):
        materialize_incremental_segment_replacement(
            previous=previous,
            current=current,
            source_strokes=source,
        )


def test_object_id_collision_has_stable_fail_closed_reason_code():
    current, source = first_case()
    assert current.segment is not None
    previous = previous_for(
        current.segment,
        revision=1,
        object_id=current.segment.object_id,
    )
    with pytest.raises(SegmentIncrementalReplacementError) as raised:
        materialize_incremental_segment_replacement(
            previous=previous,
            current=current,
            source_strokes=source,
        )
    assert raised.value.reason_code == "SEGMENT_REPLACEMENT_OBJECT_ID_COLLISION"


def test_direct_result_rejects_coherent_object_id_collision_envelope():
    result = replacement_result()
    candidate = result.replacement_segment
    collision_previous = replace(
        result.replaced_previous,
        object_id=candidate.object_id,
        revision=1,
    )
    collision_decision = SegmentIncrementalReconciliationDecision(
        action=SegmentIncrementalReconciliationAction.REPLACE_REQUIRED,
        reason_code="SEGMENT_RECONCILIATION_LOGICAL_ID_CHANGED",
        previous_logical_id=collision_previous.logical_id,
        previous_object_id=collision_previous.object_id,
        previous_revision=collision_previous.revision,
        previous_content_hash=collision_previous.content_hash(),
        candidate_logical_id=candidate.logical_id,
        candidate_content_hash=candidate.content_hash(),
        next_revision=None,
    )
    intent = result.previous_replacement_intent
    collision_intent = replace(
        intent,
        intent_key=(
            f"segment_replacement:{collision_previous.object_id}:"
            f"{candidate.object_id}:{intent.occurred_at_bar_id}"
        ),
        object_id=collision_previous.object_id,
    )
    with pytest.raises(SegmentIncrementalReplacementError) as raised:
        replace(
            result,
            canonical_reconciliation=collision_decision,
            previous_object_id=collision_previous.object_id,
            previous_revision=collision_previous.revision,
            previous_content_hash=collision_previous.content_hash(),
            replaced_previous=collision_previous,
            previous_replacement_intent=collision_intent,
        )
    assert raised.value.reason_code == "SEGMENT_REPLACEMENT_OBJECT_ID_COLLISION"
