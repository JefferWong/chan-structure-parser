"""Unit tests for the isolated Phase 2 SegmentEngine core R1."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from chan_parser.contracts.segment_rules import (
    DestructionCase,
    SegmentDirection,
)
from chan_parser.domain.lifecycle import StructureStatus, StrokeDirection
from chan_parser.domain.stroke import Stroke
from chan_parser.engine.segment import (
    SegmentEngine,
    SegmentEngineCoreError,
)


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "configs/profiles/minimal_segment_engine_core_v1.yaml"


def profile() -> dict:
    return yaml.safe_load(PROFILE.read_text(encoding="utf-8"))


def make_strokes(
    points: list[float],
    *,
    visibility_overrides: dict[int, int] | None = None,
    raw_visibility_overrides: dict[int, tuple[int, int]] | None = None,
) -> list[Stroke]:
    visibility_overrides = visibility_overrides or {}
    raw_visibility_overrides = raw_visibility_overrides or {}
    strokes: list[Stroke] = []
    for index, (start, end) in enumerate(zip(points, points[1:])):
        direction = StrokeDirection.UP if start < end else StrokeDirection.DOWN
        confirmed_at = visibility_overrides.get(index, index + 1)
        raw_created, raw_confirmed = raw_visibility_overrides.get(index, (None, None))
        strokes.append(Stroke(
            object_id=f"stroke_{index:06d}_r1",
            logical_id=f"stroke:{index}",
            revision=1,
            status=StructureStatus.CONFIRMED,
            created_at_bar=index + 1,
            confirmed_at_bar=confirmed_at,
            rule_profile="minimal_strict_v1",
            rule_version="1.0.0",
            stroke_id=f"stroke_{index:06d}",
            direction=direction,
            start_fractal_id=f"fx:{index}",
            end_fractal_id=f"fx:{index + 1}",
            start_price=start,
            end_price=end,
            start_bar_index=index,
            end_bar_index=index + 1,
            merged_bar_count=2,
            max_price=max(start, end),
            min_price=min(start, end),
            price_range=abs(end - start),
            confirmation_requirements=[],
            repaint_risk="NONE",
            created_at_raw_bar_index=raw_created,
            confirmed_at_raw_bar_index=raw_confirmed,
        ))
    return strokes


def engine() -> SegmentEngine:
    return SegmentEngine(profile())


def test_engine_profile_loads_core_only():
    instance = engine()
    assert instance.profile_id == "minimal_segment_engine_core_v1"
    assert instance.profile_version == "0.1.0"


def test_engine_profile_fails_closed_on_parser_enable():
    loaded = deepcopy(profile())
    loaded["implementation"]["parser_integration_enabled"] = True
    with pytest.raises(SegmentEngineCoreError, match="parser_integration_enabled"):
        SegmentEngine(loaded)


def test_primary_adapter_selects_opposite_strokes_and_preserves_endpoint_evidence():
    result = engine().process_primary(
        make_strokes([0, 10, 4, 12, 6]),
        sequence_id="primary:test",
    )
    assert result.reason_code == "SEGMENT_FEATURE_WINDOW_INCOMPLETE"
    assert result.candidate_direction == StrokeDirection.UP
    assert len(result.feature_elements) == 2
    first, second = result.feature_elements
    assert first.direction == SegmentDirection.DOWN
    assert first.interval.source_stroke_logical_ids == ("stroke:1",)
    assert first.start_endpoint.endpoint_id == "fx:1"
    assert first.end_endpoint.endpoint_id == "fx:2"
    assert first.high_endpoint is first.start_endpoint
    assert first.low_endpoint is first.end_endpoint
    assert second.interval.source_stroke_logical_ids == ("stroke:3",)


def test_seeded_inclusion_merges_with_stable_provenance():
    result = engine().process_primary(
        make_strokes([0, 9, 4, 10, 5, 10, 6]),
        sequence_id="primary:inclusion",
    )
    assert result.reason_code == "SEGMENT_FEATURE_WINDOW_INCOMPLETE"
    assert len(result.feature_elements) == 2
    merged = result.feature_elements[1]
    assert (merged.interval.low, merged.interval.high) == (6, 10)
    assert merged.interval.source_stroke_logical_ids == ("stroke:3", "stroke:5")
    assert merged.start_endpoint.endpoint_id == "fx:3"
    assert merged.end_endpoint.endpoint_id == "fx:6"


def test_normalized_inclusion_raw_visibility_uses_all_source_strokes():
    strokes = make_strokes(
        [0, 9, 4, 10, 5, 10, 6],
        raw_visibility_overrides={
            0: (10, 10),
            1: (11, 11),
            2: (12, 12),
            3: (13, 13),
            4: (14, 14),
            5: (25, 25),
        },
    )
    result = engine().process_primary(strokes, sequence_id="primary:raw-inclusion")
    merged = result.feature_elements[1]
    assert merged.interval.source_stroke_logical_ids == ("stroke:3", "stroke:5")
    source_by_id = {stroke.logical_id: stroke for stroke in strokes}
    assert engine()._raw_feature_visibility(merged, source_by_id) == 25


def test_equal_extremum_tie_uses_earliest_source_bar():
    result = engine().process_primary(
        make_strokes([0, 9, 4, 10, 5, 10, 6]),
        sequence_id="primary:tie",
    )
    merged = result.feature_elements[1]
    assert merged.high_endpoint.price == 10
    assert merged.high_endpoint.bar_index == 3
    assert merged.high_endpoint.endpoint_id == "fx:3"
    assert merged.high_endpoint.defining_stroke_logical_ids == ("stroke:3",)


def test_unseeded_inclusion_fails_closed():
    with pytest.raises(
        SegmentEngineCoreError,
        match="SEGMENT_FEATURE_INCLUSION_UNSEEDED",
    ):
        engine().process_primary(
            make_strokes([0, 10, 4, 9, 5, 11, 6]),
            sequence_id="primary:unseeded",
        )


def test_up_first_case_materializes_confirmed_segment():
    result = engine().process_primary(
        make_strokes([0, 10, 4, 12, 6, 11, 5]),
        sequence_id="primary:up",
    )
    assert result.reason_code == "SEGMENT_FIRST_CASE_CONFIRMED"
    assert result.primary_evidence is not None
    assert result.primary_evidence.destruction_case == DestructionCase.FIRST_CASE
    segment = result.segment
    assert segment is not None
    assert segment.status == StructureStatus.CONFIRMED
    assert segment.direction == StrokeDirection.UP
    assert segment.start_bar_index == 0
    assert segment.end_bar_index == 3
    assert segment.end_price == 12
    assert segment.stroke_ids == ["stroke_000000", "stroke_000001", "stroke_000002"]
    assert segment.feature_sequence_stroke_ids == [
        "stroke_000001", "stroke_000003", "stroke_000005"
    ]
    assert segment.destruction_evidence_stroke_ids == [
        "stroke_000003", "stroke_000005"
    ]
    assert segment.confirmed_at_bar == 6
    assert segment.created_at_bar == 6


def test_first_case_confirmation_waits_for_all_feature_elements():
    result = engine().process_primary(
        make_strokes(
            [0, 10, 4, 12, 6, 11, 5],
            visibility_overrides={3: 9},
        ),
        sequence_id="primary:up-early-feature-late-visibility",
    )
    assert result.segment is not None
    assert result.segment.confirmed_at_bar == 9


def test_raw_visibility_preserves_structural_lifecycle_axis():
    strokes = make_strokes(
        [0, 10, 4, 12, 6, 11, 5],
        visibility_overrides={3: 8},
        raw_visibility_overrides={
            index: (index + 10, index + 10)
            for index in range(6)
        },
    )
    result = engine().process_primary(
        strokes,
        sequence_id="primary:raw-axis",
    )
    assert result.reason_code == "SEGMENT_FIRST_CASE_CONFIRMED"
    assert result.segment is not None
    assert result.segment.confirmed_at_bar == 8
    assert result.segment.created_at_bar == 8
    assert result.segment.created_at_raw_bar_index == 15
    assert result.segment.confirmed_at_raw_bar_index == 15
    assert [item.visible_at_bar_index for item in result.feature_elements] == [2, 8, 6]
    source_by_id = {stroke.logical_id: stroke for stroke in strokes}
    assert max(
        engine()._raw_feature_visibility(item, source_by_id)
        for item in result.feature_elements
    ) == 15
    assert result.segment.start_bar_index == 0
    assert result.segment.end_bar_index == 3


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda values: values.__setitem__(0, replace(values[0], created_at_raw_bar_index=1,
                                                       confirmed_at_raw_bar_index=None)),
         "SEGMENT_SOURCE_RAW_VISIBILITY_PARTIAL"),
        (lambda values: values.__setitem__(0, replace(values[0], confirmed_at_raw_bar_index=-1)),
         "SEGMENT_SOURCE_RAW_VISIBILITY_INVALID"),
        (lambda values: values.__setitem__(0, replace(values[0], created_at_raw_bar_index=True,
                                                       confirmed_at_raw_bar_index=2)),
         "SEGMENT_SOURCE_RAW_VISIBILITY_INVALID"),
        (lambda values: values.__setitem__(0, replace(values[0], created_at_raw_bar_index=3,
                                                       confirmed_at_raw_bar_index=2)),
         "SEGMENT_SOURCE_RAW_VISIBILITY_INVALID"),
    ],
)
def test_raw_visibility_source_contract_fails_closed(mutation, message):
    strokes = make_strokes(
        [0, 10, 4, 12, 6, 11, 5],
        raw_visibility_overrides={index: (index + 10, index + 10) for index in range(6)},
    )
    mutation(strokes)
    with pytest.raises(SegmentEngineCoreError, match=message):
        engine().process_primary(strokes, sequence_id="primary:raw-invalid")


def test_single_raw_lifecycle_field_fails_closed():
    strokes = make_strokes([0, 10, 4, 12, 6, 11, 5])
    strokes[0] = replace(strokes[0], confirmed_at_raw_bar_index=10)
    with pytest.raises(
        SegmentEngineCoreError,
        match="SEGMENT_SOURCE_RAW_VISIBILITY_PARTIAL",
    ):
        engine().process_primary(strokes, sequence_id="primary:raw-single-field")


def test_legacy_segment_raw_lifecycle_is_none_and_serialization_hash_boundary_is_unchanged():
    result = engine().process_primary(
        make_strokes([0, 10, 4, 12, 6, 11, 5]),
        sequence_id="primary:legacy-axis",
    )
    assert result.segment is not None
    assert result.segment.created_at_raw_bar_index is None
    assert result.segment.confirmed_at_raw_bar_index is None
    payload = result.segment.to_dict()
    assert "created_at_raw_bar_index" not in payload
    assert "confirmed_at_raw_bar_index" not in payload
    assert result.segment.content_hash() == replace(
        result.segment,
        created_at_raw_bar_index=100,
        confirmed_at_raw_bar_index=101,
    ).content_hash()


def test_down_first_case_materializes_confirmed_segment():
    result = engine().process_primary(
        make_strokes([12, 2, 8, 0, 6, 1, 7]),
        sequence_id="primary:down",
    )
    assert result.reason_code == "SEGMENT_FIRST_CASE_CONFIRMED"
    segment = result.segment
    assert segment is not None
    assert segment.direction == StrokeDirection.DOWN
    assert segment.start_bar_index == 0
    assert segment.end_bar_index == 3
    assert segment.end_price == 0
    assert segment.confirmed_at_bar == 6


def test_second_case_remains_pending_without_materialization():
    result = engine().process_primary(
        make_strokes([0, 3, 1, 8, 5, 7, 4]),
        sequence_id="primary:case2",
    )
    assert result.reason_code == "SEGMENT_SECOND_CASE_PENDING"
    assert result.primary_evidence is not None
    assert (
        result.primary_evidence.destruction_case
        == DestructionCase.SECOND_CASE_PENDING
    )
    assert result.pending_second_case is not None
    assert result.segment is None


def test_raw_pending_outcomes_do_not_materialize_segment_lifecycle():
    for points in ([0, 3, 1, 8, 5, 7, 4], [0, 10, 4, 12, 6]):
        result = engine().process_primary(
            make_strokes(
                list(points),
                raw_visibility_overrides={
                    index: (index + 10, index + 10)
                    for index in range(len(points) - 1)
                },
            ),
            sequence_id="primary:raw-pending",
        )
        assert result.reason_code in {
            "SEGMENT_FEATURE_WINDOW_INCOMPLETE",
            "SEGMENT_SECOND_CASE_PENDING",
            "SEGMENT_PRIMARY_FRACTAL_NOT_FOUND",
        }
        assert result.segment is None


def test_source_sequence_must_be_alternating_and_contiguous():
    discontinuous = make_strokes([0, 10, 4, 12, 6, 11, 5])
    discontinuous[2] = replace(discontinuous[2], start_fractal_id="fx:other")
    with pytest.raises(
        SegmentEngineCoreError,
        match="SEGMENT_SOURCE_ENDPOINT_NOT_CONTIGUOUS",
    ):
        engine().process_primary(discontinuous, sequence_id="primary:bad-gap")

    nonalternating = make_strokes([0, 10, 4, 12, 6, 11, 5])
    nonalternating[2] = replace(
        nonalternating[2],
        direction=StrokeDirection.DOWN,
        start_price=12,
        end_price=4,
        max_price=12,
        min_price=4,
    )
    with pytest.raises(
        SegmentEngineCoreError,
        match="SEGMENT_SOURCE_DIRECTION_NOT_ALTERNATING",
    ):
        engine().process_primary(nonalternating, sequence_id="primary:bad-direction")


def test_feature_sources_must_be_confirmed_with_visibility():
    provisional = make_strokes([0, 10, 4, 12, 6, 11, 5])
    provisional[5] = replace(
        provisional[5],
        status=StructureStatus.PROVISIONAL,
        confirmed_at_bar=None,
    )
    with pytest.raises(
        SegmentEngineCoreError,
        match="SEGMENT_SOURCE_STROKE_NOT_CONFIRMED",
    ):
        engine().process_primary(provisional, sequence_id="primary:provisional")

    bad_visibility = make_strokes([0, 10, 4, 12, 6, 11, 5])
    bad_visibility[5] = replace(bad_visibility[5], confirmed_at_bar=5)
    with pytest.raises(
        SegmentEngineCoreError,
        match="SEGMENT_SOURCE_CONFIRMATION_VISIBILITY_INVALID",
    ):
        engine().process_primary(bad_visibility, sequence_id="primary:visibility")


def test_first_case_identity_is_deterministic():
    strokes = make_strokes(
        [0, 10, 4, 12, 6, 11, 5],
        visibility_overrides={5: 9},
    )
    first = engine().process_primary(strokes, sequence_id="primary:stable")
    second = engine().process_primary(strokes, sequence_id="primary:stable")
    assert first.segment is not None and second.segment is not None
    assert first.segment.logical_id == second.segment.logical_id
    assert first.segment.segment_id == second.segment.segment_id
    assert first.segment.content_hash() == second.segment.content_hash()
    assert first.primary_evidence is not None and second.primary_evidence is not None
    assert first.primary_evidence.evidence_key == second.primary_evidence.evidence_key
    assert tuple(item.logical_id for item in first.feature_elements) == tuple(
        item.logical_id for item in second.feature_elements
    )
    assert first.segment.confirmed_at_bar == 9


def test_segment_engine_r1_exposes_no_parser_event_or_checkpoint_api():
    instance = engine()
    assert not hasattr(instance, "process")
    assert not hasattr(instance, "append")
    assert not hasattr(instance, "append_batch")
    assert not hasattr(instance, "restore_checkpoint")
    assert not hasattr(instance, "emit_events")
