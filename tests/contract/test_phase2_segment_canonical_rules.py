"""Executable gates for the stateless Phase 2 segment-rule oracle."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml

from chan_parser.contracts.segment_rules import (
    CandidateChoice,
    DestructionCase,
    FeatureFractalType,
    InclusionContext,
    InclusionSeed,
    IntervalRelation,
    LifecycleResolution,
    PendingSecondCaseContext,
    PriceInterval,
    SecondarySequenceContext,
    SegmentBoundaryInput,
    SegmentDirection,
    SegmentRuleContractError,
    StrokeRuleInput,
    SequenceBoundaryNature,
    build_feature_sequence,
    choose_deterministic_candidate,
    classify_interval_relation,
    classify_primary_destruction_case,
    classify_pending_second_case_invalidation,
    classify_secondary_confirmation,
    classify_failed_pen_break,
    classify_strict_feature_fractal,
    confirmation_bar,
    derive_inclusion_seed,
    has_feature_gap,
    merge_included_intervals,
    resolve_lifecycle,
    validate_frozen_prefix_transition,
    validate_segment_boundaries,
    validate_segment_canonical_rules_profile,
)


ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "configs/profiles/minimal_segment_canonical_rules_v1.yaml"
FIXTURE_DIR = ROOT / "tests/fixtures/segment_rules"
REQUIRED_FIXTURE_IDS = {
    "FS-UP-001", "FS-DOWN-001", "FS-CROSS-SEQUENCE-REJECT-001",
    "INCLUSION-UP-001", "INCLUSION-DOWN-001", "INCLUSION-EQUAL-001",
    "INCLUSION-UNSEEDED-001", "INCLUSION-PROVENANCE-001",
    "INCLUSION-FIRST-BOUNDARY-NOMERGE-001",
    "INCLUSION-SECOND-SEQUENCE-MERGE-001", "FRACTAL-TOP-001",
    "FRACTAL-BOTTOM-001", "FRACTAL-EQUAL-HIGH-REJECT-001",
    "FRACTAL-EQUAL-LOW-REJECT-001",
    "FRACTAL-WRONG-DIRECTION-REJECT-001", "GAP-STRICT-UP-001",
    "GAP-STRICT-DOWN-001", "GAP-TOUCHING-NOGAP-001",
    "GAP-OVERLAP-NOGAP-001", "CASE1-UP-001", "CASE1-DOWN-001",
    "CASE1-PEN-ONLY-PENDING-001", "CASE1-FAILED-CONTINUATION-001",
    "CASE2-UP-PENDING-001", "CASE2-DOWN-PENDING-001",
    "CASE2-SECOND-FRACTAL-CONFIRM-001",
    "CASE2-GAP-NOT-CLOSED-CONFIRM-001",
    "CASE2-NEW-EXTREME-INVALIDATE-001",
    "CASE2-SECOND-SEQUENCE-INCLUSION-001", "WINNER-LEFTMOST-001",
    "WINNER-SAME-ENDPOINT-001", "LIFECYCLE-INVALIDATED-001",
    "LIFECYCLE-REPLACED-001", "TIMING-NO-BACKFILL-001",
    "SG-CONNECTION-001", "FREEZE-APPEND-001",
}
RULE_CLASSIFICATION = {
    "FS-001": "ORIGINAL_CANONICAL_CORE",
    "FS-002": "ORIGINAL_CANONICAL_CORE",
    "FS-003": "ORIGINAL_CANONICAL_CORE",
    "FR-001": "ORIGINAL_CANONICAL_CORE",
    "DS-CASE1": "ORIGINAL_CANONICAL_CORE",
    "DS-CASE2": "ORIGINAL_CANONICAL_CORE",
    "DS-PEN-001": "ORIGINAL_CANONICAL_CORE",
    "DS-CASE2-FAIL": "ORIGINAL_CANONICAL_CORE",
    "DS-CASE1-FAIL": "ORIGINAL_CANONICAL_CORE",
    "SG-001": "ORIGINAL_CANONICAL_CORE",
    "EQ-INTERVAL-001": "ENGINEERING_DETERMINISM_V1",
    "EQ-INCLUSION-001": "ENGINEERING_DETERMINISM_V1",
    "EQ-GAP-001": "ENGINEERING_DETERMINISM_V1",
    "EQ-SEED-001": "ENGINEERING_DETERMINISM_V1",
    "EQ-MERGE-001": "ENGINEERING_DETERMINISM_V1",
    "EQ-BOUNDARY-001": "ENGINEERING_DETERMINISM_V1",
    "EQ-FRACTAL-001": "ENGINEERING_DETERMINISM_V1",
    "EQ-TIME-001": "ENGINEERING_DETERMINISM_V1",
    "EQ-WINNER-001": "ENGINEERING_DETERMINISM_V1",
    "EQ-LIFECYCLE-001": "ENGINEERING_DETERMINISM_V1",
    "EQ-FREEZE-001": "ENGINEERING_DETERMINISM_V1",
}


def profile() -> dict:
    return yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))


def interval(low: float, high: float, *sources: str) -> PriceInterval:
    return PriceInterval(low, high, tuple(sources))


def inclusion_context(
    boundary: SequenceBoundaryNature = SequenceBoundaryNature.NORMAL,
) -> InclusionContext:
    return InclusionContext(
        "sequence:a",
        "sequence:a",
        SegmentDirection.UP,
        SegmentDirection.UP,
        boundary,
    )


def secondary_context(**overrides) -> SecondarySequenceContext:
    values = {
        "pending": PendingSecondCaseContext(
            DestructionCase.SECOND_CASE_PENDING,
            SegmentDirection.UP,
            "second:a",
            "endpoint:pending",
            ("stroke:pending",),
        ),
        "normalized": True,
        "adjacent_to_pending_endpoint": True,
        "element_sequence_ids": ("second:a", "second:a", "second:a"),
        "normalized_source_logical_ids": ("stroke:a", "stroke:b", "stroke:c"),
        "left_start_endpoint_id": "endpoint:pending",
    }
    values.update(overrides)
    return SecondarySequenceContext(**values)


def pending_context(
    direction: SegmentDirection = SegmentDirection.UP,
) -> PendingSecondCaseContext:
    return PendingSecondCaseContext(
        DestructionCase.SECOND_CASE_PENDING,
        direction,
        "second:a",
        "endpoint:pending",
        ("stroke:pending",),
    )


def all_fixture_cases() -> list[dict]:
    cases = []
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        cases.extend(json.loads(path.read_text(encoding="utf-8")))
    return cases


def test_profile_loads_and_is_rules_only():
    loaded = profile()
    validate_segment_canonical_rules_profile(loaded)
    assert loaded["status"] == "CANONICAL_RULES_ONLY"
    assert loaded["implementation_enabled"] is False
    assert loaded["parser_integration_enabled"] is False


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda value: value.update({"unknown": True}), "unknown"),
        (lambda value: value.pop("status"), "missing"),
        (lambda value: value.__setitem__("rules", None), "must be a mapping"),
        (
            lambda value: value["rules"]["inclusion"].__setitem__(
                "seed_policy", "guess_from_segment_direction"
            ),
            "unsupported",
        ),
        (
            lambda value: value.__setitem__("implementation_enabled", True),
            "unsupported",
        ),
        (
            lambda value: value["prohibited"].__setitem__("segment_engine", False),
            "unsupported",
        ),
        (
            lambda value: value["rules"]["fractal"].__setitem__("window_size", "3"),
            "unsupported",
        ),
    ],
)
def test_profile_fails_closed(mutation, match):
    loaded = profile()
    mutation(loaded)
    with pytest.raises(SegmentRuleContractError, match=match):
        validate_segment_canonical_rules_profile(loaded)


@pytest.mark.parametrize("invalid", [None, [], "profile"])
def test_profile_rejects_non_mapping(invalid):
    with pytest.raises(SegmentRuleContractError, match="mapping"):
        validate_segment_canonical_rules_profile(invalid)


def test_all_required_fixtures_have_contract_metadata():
    cases = all_fixture_cases()
    assert {case["fixture_id"] for case in cases} == REQUIRED_FIXTURE_IDS
    assert len(cases) == 36
    assert set(RULE_CLASSIFICATION) == {
        rule_id for case in cases for rule_id in case["rule_ids"]
    }
    for case in cases:
        assert set(case) == {
            "fixture_id", "rule_ids", "classification", "input", "expected",
            "reason_code",
        }
        assert case["rule_ids"] and case["reason_code"]
        assert case["classification"] in {
            "ORIGINAL_CANONICAL_CORE", "ENGINEERING_DETERMINISM_V1",
        }
        assert all(
            RULE_CLASSIFICATION[rule_id] == case["classification"]
            for rule_id in case["rule_ids"]
        )


@pytest.mark.parametrize(
    "case", all_fixture_cases(), ids=lambda case: case["fixture_id"]
)
def test_every_fixture_executes_against_reference_oracle(case):
    """Every versioned decision-table row must drive a pure oracle function."""
    fixture_id = case["fixture_id"]
    data = case["input"]
    expected = case["expected"]

    if fixture_id in {"FS-UP-001", "FS-DOWN-001"}:
        direction = SegmentDirection(data["candidate_direction"])
        strokes = [
            StrokeRuleInput(
                f"stroke:{index}",
                f"runtime:{index}",
                SegmentDirection(value),
                index,
                index + 2,
                index,
                index + 1,
                "fixture-sequence",
            )
            for index, value in enumerate(data["stroke_directions"])
        ]
        result = build_feature_sequence(
            direction, strokes, sequence_id="fixture-sequence"
        )
        selected = [
            stroke.direction.value
            for stroke in strokes
            if stroke.logical_id in result.source_stroke_logical_ids
        ]
        assert selected == expected["selected_directions"]
        return
    if fixture_id == "FS-CROSS-SEQUENCE-REJECT-001":
        strokes = [
            StrokeRuleInput("a", "ra", SegmentDirection.DOWN, 1, 2, 1, 2, "a"),
            StrokeRuleInput("b", "rb", SegmentDirection.DOWN, 2, 3, 2, 3, "b"),
        ]
        with pytest.raises(SegmentRuleContractError, match=case["reason_code"]):
            build_feature_sequence(
                SegmentDirection.UP, strokes, sequence_id="a"
            )
        return
    if fixture_id.startswith("INCLUSION-"):
        if fixture_id == "INCLUSION-UNSEEDED-001":
            assert derive_inclusion_seed(
                interval(*data["previous"]), interval(*data["current"])
            ).value == expected["seed"]
            return
        if fixture_id == "INCLUSION-EQUAL-001":
            assert classify_interval_relation(
                interval(*data["a"]), interval(*data["b"])
            ).value == expected["relation"]
            return
        first = interval(
            *(data.get("a") or [1, 5]),
            *(data.get("a_sources") or ["stroke:a"]),
        )
        second = interval(
            *(data.get("b") or [2, 4]),
            *(data.get("b_sources") or ["stroke:b"]),
        )
        if fixture_id == "INCLUSION-FIRST-BOUNDARY-NOMERGE-001":
            with pytest.raises(SegmentRuleContractError, match=case["reason_code"]):
                merge_included_intervals(
                    first,
                    second,
                    InclusionSeed.UP,
                    context=inclusion_context(
                        SequenceBoundaryNature.FIRST_CASE_CROSS_BOUNDARY
                    ),
                )
            return
        boundary = (
            SequenceBoundaryNature.SECOND_FEATURE_SEQUENCE
            if fixture_id == "INCLUSION-SECOND-SEQUENCE-MERGE-001"
            else SequenceBoundaryNature.NORMAL
        )
        merged = merge_included_intervals(
            first,
            second,
            InclusionSeed(data.get("seed", "UP")),
            context=inclusion_context(boundary),
        )
        if "merged" in expected and isinstance(expected["merged"], list):
            assert [merged.low, merged.high] == expected["merged"]
        if "sources" in expected:
            assert list(merged.source_stroke_logical_ids) == expected["sources"]
        return
    if fixture_id.startswith("FRACTAL-"):
        if fixture_id == "FRACTAL-WRONG-DIRECTION-REJECT-001":
            result = classify_primary_destruction_case(
                SegmentDirection.UP,
                interval(3, 7),
                interval(1, 4),
                interval(2, 6),
                pen_break_observed=False,
            )
            assert result.destruction_case == DestructionCase.NONE
            assert result.reason_code == case["reason_code"]
            return
        result = classify_strict_feature_fractal(
            interval(*data["left"]),
            interval(*data["center"]),
            interval(*data["right"]),
        )
        assert result.value == expected["fractal"]
        return
    if fixture_id.startswith("GAP-"):
        first, second = interval(*data["a"]), interval(*data["b"])
        assert has_feature_gap(first, second) is expected["gap"]
        if "relation" in expected:
            assert classify_interval_relation(first, second).value == expected["relation"]
        return
    if fixture_id in {"CASE1-UP-001", "CASE1-DOWN-001"}:
        result = classify_primary_destruction_case(
            SegmentDirection(data["direction"]),
            interval(*data["left"]),
            interval(*data["center"]),
            interval(*data["right"]),
            pen_break_observed=True,
        )
        assert result.destruction_case.value == expected["case"]
        assert result.endpoint_price == expected["endpoint"]
        assert result.reason_code == case["reason_code"]
        return
    if fixture_id == "CASE1-PEN-ONLY-PENDING-001":
        result = classify_failed_pen_break(
            pen_break_observed=data["pen_break"],
            required_fractal_formed=data["complete_fractal"],
            countermove_invalidated=False,
        )
        assert result.destruction_case.value == expected["case"]
        assert result.reason_code == case["reason_code"]
        return
    if fixture_id == "CASE1-FAILED-CONTINUATION-001":
        result = classify_failed_pen_break(
            pen_break_observed=data["pen_break"],
            required_fractal_formed=data["complete_fractal"],
            countermove_invalidated=data["countermove_invalidated"],
        )
        assert result.destruction_case.value == expected["case"]
        assert result.reason_code == case["reason_code"]
        return
    if fixture_id in {"CASE2-UP-PENDING-001", "CASE2-DOWN-PENDING-001"}:
        direction = SegmentDirection(data["direction"])
        result = classify_primary_destruction_case(
            direction,
            interval(*data["left"]),
            interval(*data["center"]),
            interval(*data["right"]),
            pen_break_observed=data["pen_break"],
        )
        assert result.destruction_case.value == expected["case"]
        assert result.reason_code == case["reason_code"]
        return
    if fixture_id in {
        "CASE2-SECOND-FRACTAL-CONFIRM-001",
        "CASE2-GAP-NOT-CLOSED-CONFIRM-001",
    }:
        result = classify_secondary_confirmation(
            SegmentDirection(data["original_direction"]),
            interval(*data["left"], "stroke:a"),
            interval(*data["center"], "stroke:b"),
            interval(*data["right"], "stroke:c"),
            context=secondary_context(),
        )
        assert result.destruction_case.value == expected["case"]
        assert result.feature_fractal_type.value == data["second_sequence_fractal"]
        if "original_gap_closed" in data:
            assert data["original_gap_closed"] is False
        return
    if fixture_id == "CASE2-NEW-EXTREME-INVALIDATE-001":
        result = classify_pending_second_case_invalidation(
            pending_context(SegmentDirection(data["original_direction"])),
            strict_original_direction_new_extreme=data[
                "original_direction_new_extreme"
            ],
            new_extreme_observed_at_bar=data["new_extreme_observed_at_bar"],
            secondary_confirmed_at_bar=data["secondary_confirmed_at_bar"],
        )
        assert result.destruction_case.value == expected["case"]
        assert result.reason_code == case["reason_code"]
        return
    if fixture_id == "CASE2-SECOND-SEQUENCE-INCLUSION-001":
        merged = merge_included_intervals(
            interval(*data["a"], "stroke:a"),
            interval(*data["b"], "stroke:b"),
            InclusionSeed(data["seed"]),
            context=inclusion_context(
                SequenceBoundaryNature.SECOND_FEATURE_SEQUENCE
            ),
        )
        assert merged == interval(2, 5, "stroke:a", "stroke:b")
        assert data["normal_inclusion_required"] is True
        return
    if fixture_id.startswith("WINNER-"):
        candidates = [
            CandidateChoice(logical_id, endpoint, start, "mutex")
            for logical_id, endpoint, start in data["candidates"]
        ]
        assert choose_deterministic_candidate(
            candidates
        ).winner_logical_id == expected["winner"]
        return
    if fixture_id == "LIFECYCLE-INVALIDATED-001":
        status, _ = resolve_lifecycle(
            minimum_candidate_window_present=True,
            previously_confirmed=False,
            evidence_complete=False,
            evidence_invalidated=True,
        )
        assert status.value == expected["status"]
        return
    if fixture_id == "LIFECYCLE-REPLACED-001":
        status, replaced_by = resolve_lifecycle(
            minimum_candidate_window_present=True,
            previously_confirmed=True,
            evidence_complete=True,
            evidence_invalidated=False,
            reverse_segment_logical_id=data["reverse_segment_logical_id"],
            reverse_segment_confirmed=True,
        )
        assert status.value == expected["status"]
        assert replaced_by == expected["replaced_by"]
        return
    if fixture_id == "TIMING-NO-BACKFILL-001":
        assert confirmation_bar(
            data["endpoint_bar"], data["right_element_visible_bars"]
        ) == expected["confirmed_at_bar"]
        return
    if fixture_id == "SG-CONNECTION-001":
        first = SegmentBoundaryInput(
            "segment:previous",
            SegmentDirection(data["previous_direction"]),
            SegmentDirection(data["previous_direction"]),
            SegmentDirection(data["previous_direction"]),
            "endpoint:0",
            data["shared_endpoint"],
        )
        current = SegmentBoundaryInput(
            "segment:current",
            SegmentDirection(data["current_direction"]),
            SegmentDirection(data["current_direction"]),
            SegmentDirection(data["current_direction"]),
            data["shared_endpoint"],
            "endpoint:2",
        )
        assert validate_segment_boundaries(current, previous_confirmed=first)
        return
    if fixture_id == "FREEZE-APPEND-001":
        assert validate_frozen_prefix_transition(
            before_prefix_hash=data["prefix_hash"],
            after_prefix_hash=data["prefix_hash"],
            before_event_count=data["before_event_count"],
            after_event_count=data["after_event_count"],
            original_confirmed_at_bar=data["confirmed_at_bar"],
            revised_confirmed_at_bar=data["confirmed_at_bar"],
            correction_occurred=data["correction_occurred"],
        )
        return
    raise AssertionError(f"fixture has no oracle execution: {fixture_id}")


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ((1, 5), (1, 5), IntervalRelation.EQUAL),
        ((1, 5), (2, 4), IntervalRelation.CONTAINS),
        ((2, 4), (1, 5), IntervalRelation.CONTAINED_BY),
        ((1, 2), (2, 4), IntervalRelation.TOUCHING),
        ((1, 2), (3, 4), IntervalRelation.DISJOINT),
        ((1, 3), (2, 4), IntervalRelation.OVERLAP),
    ],
)
def test_closed_interval_relations(first, second, expected):
    assert classify_interval_relation(interval(*first), interval(*second)) == expected


def test_gap_requires_strict_separation_and_touching_is_not_gap():
    assert has_feature_gap(interval(1, 2), interval(3, 4))
    assert has_feature_gap(interval(4, 5), interval(1, 3))
    assert not has_feature_gap(interval(1, 2), interval(2, 4))
    assert not has_feature_gap(interval(1, 3), interval(2, 4))


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        ((1, 2), (3, 4), InclusionSeed.UP),
        ((3, 4), (1, 2), InclusionSeed.DOWN),
        ((1, 4), (2, 4), InclusionSeed.UNSEEDED),
        ((1, 5), (2, 4), InclusionSeed.UNSEEDED),
        ((1, 3), (2, 4), InclusionSeed.UP),
    ],
)
def test_seed_is_strict_and_otherwise_defers(previous, current, expected):
    assert derive_inclusion_seed(interval(*previous), interval(*current)) == expected


def test_merge_up_down_and_provenance_are_deterministic():
    first = interval(1, 5, "stroke:a")
    second = interval(2, 4, "stroke:b")
    up = merge_included_intervals(
        first, second, InclusionSeed.UP, context=inclusion_context()
    )
    down = merge_included_intervals(
        first, second, InclusionSeed.DOWN, context=inclusion_context()
    )
    assert (up.low, up.high) == (2, 5)
    assert (down.low, down.high) == (1, 4)
    assert up.source_stroke_logical_ids == ("stroke:a", "stroke:b")
    assert up == merge_included_intervals(
        first, second, InclusionSeed.UP, context=inclusion_context()
    )


def test_unseeded_and_cross_boundary_merge_fail_closed():
    first, second = interval(1, 5, "stroke:a"), interval(2, 4, "stroke:b")
    with pytest.raises(SegmentRuleContractError, match="DIRECTION_UNSEEDED"):
        merge_included_intervals(
            first, second, InclusionSeed.UNSEEDED, context=inclusion_context()
        )
    with pytest.raises(
        SegmentRuleContractError, match="HYPOTHETICAL_BOUNDARY_DIFFERENT_NATURE"
    ):
        merge_included_intervals(
            first,
            second,
            InclusionSeed.UP,
            context=inclusion_context(
                SequenceBoundaryNature.FIRST_CASE_CROSS_BOUNDARY
            ),
        )
    assert merge_included_intervals(
        first,
        second,
        InclusionSeed.UP,
        context=inclusion_context(SequenceBoundaryNature.SECOND_FEATURE_SEQUENCE),
    ) == interval(2, 5, "stroke:a", "stroke:b")


def test_merge_rejects_cross_sequence_and_duplicate_provenance():
    first, second = interval(1, 5, "stroke:a"), interval(2, 4, "stroke:b")
    cross = InclusionContext(
        "sequence:a", "sequence:b", SegmentDirection.UP, SegmentDirection.UP
    )
    with pytest.raises(SegmentRuleContractError, match="CROSS_SEQUENCE"):
        merge_included_intervals(first, second, InclusionSeed.UP, context=cross)
    duplicate = interval(2, 4, "stroke:a")
    with pytest.raises(SegmentRuleContractError, match="DUPLICATE_FEATURE"):
        merge_included_intervals(
            first, duplicate, InclusionSeed.UP, context=inclusion_context()
        )


def test_feature_sequence_uses_only_opposite_strokes_and_stable_ids():
    strokes = [
        StrokeRuleInput("stroke:1", "runtime-1", SegmentDirection.UP, 1, 4, 1, 2, "s"),
        StrokeRuleInput("stroke:2", "runtime-2", SegmentDirection.DOWN, 2, 5, 2, 3, "s"),
        StrokeRuleInput("stroke:3", "runtime-3", SegmentDirection.UP, 1, 6, 3, 4, "s"),
        StrokeRuleInput("stroke:4", "runtime-4", SegmentDirection.DOWN, 3, 5, 4, 5, "s"),
    ]
    result = build_feature_sequence(SegmentDirection.UP, strokes, sequence_id="s")
    assert result.source_stroke_logical_ids == ("stroke:2", "stroke:4")
    assert tuple(x.source_stroke_logical_ids for x in result.intervals) == (
        ("stroke:2",), ("stroke:4",),
    )


def test_feature_sequence_rejects_cross_sequence_and_duplicate_logical_id():
    one = StrokeRuleInput("stroke:1", "a", SegmentDirection.DOWN, 1, 2, 1, 2, "s1")
    two = StrokeRuleInput("stroke:2", "b", SegmentDirection.DOWN, 2, 3, 2, 3, "s2")
    with pytest.raises(SegmentRuleContractError, match="CROSS_SEQUENCE"):
        build_feature_sequence(SegmentDirection.UP, [one, two], sequence_id="s1")
    duplicate = StrokeRuleInput("stroke:1", "c", SegmentDirection.DOWN, 2, 3, 2, 3, "s1")
    with pytest.raises(SegmentRuleContractError, match="DUPLICATE"):
        build_feature_sequence(SegmentDirection.UP, [one, duplicate], sequence_id="s1")


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (((1, 4), (3, 7), (2, 5)), FeatureFractalType.TOP),
        (((3, 7), (1, 4), (2, 6)), FeatureFractalType.BOTTOM),
        (((1, 7), (3, 7), (2, 5)), FeatureFractalType.NONE),
        (((1, 7), (1, 4), (2, 6)), FeatureFractalType.NONE),
    ],
)
def test_strict_feature_fractal(values, expected):
    assert classify_strict_feature_fractal(
        *(interval(*value) for value in values)
    ) == expected


def test_first_case_requires_complete_directional_fractal_and_no_gap():
    up = classify_primary_destruction_case(
        SegmentDirection.UP,
        interval(3, 5),
        interval(4, 8),
        interval(2, 6),
        pen_break_observed=True,
    )
    down = classify_primary_destruction_case(
        SegmentDirection.DOWN,
        interval(3, 7),
        interval(1, 4),
        interval(2, 6),
        pen_break_observed=True,
    )
    assert (up.destruction_case, up.endpoint_price) == (DestructionCase.FIRST_CASE, 8)
    assert (down.destruction_case, down.endpoint_price) == (DestructionCase.FIRST_CASE, 1)


def test_pen_break_only_remains_provisional():
    result = classify_primary_destruction_case(
        SegmentDirection.UP,
        interval(1, 4),
        interval(2, 5),
        interval(3, 6),
        pen_break_observed=True,
    )
    assert result.destruction_case == DestructionCase.NONE
    assert result.reason_code == "PEN_BREAK_PROVISIONAL"


def test_second_case_is_pending_when_primary_fractal_has_gap():
    up = classify_primary_destruction_case(
        SegmentDirection.UP,
        interval(1, 2),
        interval(3, 7),
        interval(2, 5),
        pen_break_observed=True,
    )
    down = classify_primary_destruction_case(
        SegmentDirection.DOWN,
        interval(7, 8),
        interval(2, 6),
        interval(3, 7),
        pen_break_observed=True,
    )
    assert up.destruction_case == DestructionCase.SECOND_CASE_PENDING
    assert down.destruction_case == DestructionCase.SECOND_CASE_PENDING


def test_secondary_fractal_confirms_without_original_gap_closure_input():
    confirmed = classify_secondary_confirmation(
        SegmentDirection.UP,
        interval(3, 7, "stroke:a"),
        interval(1, 4, "stroke:b"),
        interval(2, 6, "stroke:c"),
        context=secondary_context(),
    )
    assert confirmed.destruction_case == DestructionCase.SECOND_CASE_CONFIRMED
    assert confirmed.feature_fractal_type == FeatureFractalType.BOTTOM


def test_pending_second_case_is_invalidated_by_new_original_extreme():
    result = classify_pending_second_case_invalidation(
        pending_context(SegmentDirection.DOWN),
        strict_original_direction_new_extreme=True,
        new_extreme_observed_at_bar=20,
        secondary_confirmed_at_bar=None,
    )
    assert result.destruction_case == DestructionCase.INVALIDATED
    assert result.reason_code == "PENDING_DESTRUCTION_INVALIDATED"
    with pytest.raises(SegmentRuleContractError, match="ALREADY_CONFIRMED"):
        classify_pending_second_case_invalidation(
            pending_context(SegmentDirection.DOWN),
            strict_original_direction_new_extreme=True,
            new_extreme_observed_at_bar=20,
            secondary_confirmed_at_bar=19,
        )


def test_deterministic_winner_is_independent_of_input_order():
    candidates = [
        CandidateChoice("z", 20, 1, "mutex"),
        CandidateChoice("b", 10, 5, "mutex"),
        CandidateChoice("a", 10, 5, "mutex"),
        CandidateChoice("earlier-start", 10, 1, "mutex"),
    ]
    first = choose_deterministic_candidate(candidates)
    second = choose_deterministic_candidate(list(reversed(candidates)))
    assert first.winner_logical_id == "earlier-start"
    assert first == second
    lexical = choose_deterministic_candidate(
        [
            CandidateChoice("b", 10, 1, "mutex"),
            CandidateChoice("a", 10, 1, "mutex"),
        ]
    )
    assert lexical.winner_logical_id == "a"
    assert lexical.restart_at_bar_index == 10


def test_winner_invalidates_only_mutually_exclusive_candidates():
    result = choose_deterministic_candidate(
        [
            CandidateChoice("winner", 10, 1, "group-a"),
            CandidateChoice("loser", 11, 1, "group-a"),
            CandidateChoice("independent", 12, 1, "group-b"),
        ]
    )
    assert result.invalidated_logical_ids == ("loser",)
    assert result.remaining_logical_ids == ("independent",)


def test_lifecycle_mapping_separates_invalidated_and_replaced():
    assert resolve_lifecycle(
        minimum_candidate_window_present=True,
        previously_confirmed=False,
        evidence_complete=False,
        evidence_invalidated=True,
    ) == (LifecycleResolution.INVALIDATED, None)
    assert resolve_lifecycle(
        minimum_candidate_window_present=True,
        previously_confirmed=True,
        evidence_complete=True,
        evidence_invalidated=False,
        reverse_segment_logical_id="segment:reverse",
        reverse_segment_confirmed=True,
    ) == (LifecycleResolution.REPLACED, "segment:reverse")
    with pytest.raises(SegmentRuleContractError, match="REPLACED requires"):
        resolve_lifecycle(
            minimum_candidate_window_present=True,
            previously_confirmed=False,
            evidence_complete=False,
            evidence_invalidated=False,
            reverse_segment_logical_id="segment:reverse",
            reverse_segment_confirmed=True,
        )
    assert resolve_lifecycle(
        minimum_candidate_window_present=True,
        previously_confirmed=False,
        evidence_complete=False,
        evidence_invalidated=False,
    ) == (LifecycleResolution.CANDIDATE, None)


def test_case1_failed_pen_break_is_invalidated_without_reverse_segment():
    result = classify_failed_pen_break(
        pen_break_observed=True,
        required_fractal_formed=False,
        countermove_invalidated=True,
    )
    assert result.destruction_case == DestructionCase.INVALIDATED
    assert result.reason_code == "PEN_BREAK_EVIDENCE_INVALIDATED"


def test_secondary_confirmation_requires_pending_normalized_adjacent_evidence():
    evidence = (
        interval(3, 7, "stroke:a"),
        interval(1, 4, "stroke:b"),
        interval(2, 6, "stroke:c"),
    )
    with pytest.raises(SegmentRuleContractError, match="PENDING"):
        classify_secondary_confirmation(
            SegmentDirection.UP,
            *evidence,
            context=secondary_context(
                pending=PendingSecondCaseContext(
                    DestructionCase.FIRST_CASE,
                    SegmentDirection.UP,
                    "second:a",
                    "endpoint:pending",
                    ("stroke:pending",),
                )
            ),
        )
    with pytest.raises(SegmentRuleContractError, match="NORMALIZATION"):
        classify_secondary_confirmation(
            SegmentDirection.UP,
            *evidence,
            context=secondary_context(normalized=False),
        )
    with pytest.raises(SegmentRuleContractError, match="DIRECTION_MISMATCH"):
        classify_secondary_confirmation(
            SegmentDirection.DOWN,
            *evidence,
            context=secondary_context(),
        )


def test_sg_direction_connection_and_destroyer_rules():
    first = SegmentBoundaryInput(
        "segment:up",
        SegmentDirection.UP,
        SegmentDirection.UP,
        SegmentDirection.UP,
        "endpoint:0",
        "endpoint:1",
    )
    second = SegmentBoundaryInput(
        "segment:down",
        SegmentDirection.DOWN,
        SegmentDirection.DOWN,
        SegmentDirection.DOWN,
        "endpoint:1",
        "endpoint:2",
    )
    assert validate_segment_boundaries(
        second,
        previous_confirmed=first,
        destroyer_direction=SegmentDirection.UP,
    )
    with pytest.raises(SegmentRuleContractError, match="SAME_DIRECTION"):
        validate_segment_boundaries(
            second,
            previous_confirmed=first,
            destroyer_direction=SegmentDirection.DOWN,
        )


def test_frozen_prefix_transition_is_append_only_and_no_backfill():
    assert validate_frozen_prefix_transition(
        before_prefix_hash="abc",
        after_prefix_hash="abc",
        before_event_count=10,
        after_event_count=11,
        original_confirmed_at_bar=20,
        revised_confirmed_at_bar=20,
        correction_occurred=True,
    )
    with pytest.raises(SegmentRuleContractError, match="EVENT_DELETION"):
        validate_frozen_prefix_transition(
            before_prefix_hash="abc",
            after_prefix_hash="abc",
            before_event_count=10,
            after_event_count=9,
            original_confirmed_at_bar=20,
            revised_confirmed_at_bar=20,
            correction_occurred=True,
        )


def test_confirmation_time_uses_latest_visible_bar_and_never_backfills():
    assert confirmation_bar(10, [12, 14, 13]) == 14
    with pytest.raises(SegmentRuleContractError, match="BACKFILL"):
        confirmation_bar(10, [8, 9])


def test_oracle_is_deterministic_and_has_no_mutable_instance_state():
    loaded = profile()
    before = deepcopy(loaded)
    validate_segment_canonical_rules_profile(loaded)
    validate_segment_canonical_rules_profile(loaded)
    assert loaded == before
    assert PriceInterval.__dataclass_params__.frozen
    assert CandidateChoice.__dataclass_params__.frozen
    from chan_parser.contracts import segment_rules

    assert not hasattr(segment_rules, "EXPECTED_PROFILE")


def test_provenance_is_strictly_immutable_and_secondary_sources_are_unique():
    with pytest.raises(SegmentRuleContractError, match="immutable tuple"):
        PriceInterval(1, 2, ["stroke:a"])
    with pytest.raises(SegmentRuleContractError, match="DUPLICATE_SECOND"):
        classify_secondary_confirmation(
            SegmentDirection.UP,
            interval(3, 7, "stroke:a"),
            interval(1, 4, "stroke:a"),
            interval(2, 6, "stroke:c"),
            context=secondary_context(
                normalized_source_logical_ids=("stroke:a", "stroke:a", "stroke:c")
            ),
        )


def test_lifecycle_rejects_contradictory_confirmation_and_replacement_evidence():
    with pytest.raises(SegmentRuleContractError, match="candidate window"):
        resolve_lifecycle(
            minimum_candidate_window_present=False,
            previously_confirmed=False,
            evidence_complete=True,
            evidence_invalidated=False,
        )
    with pytest.raises(SegmentRuleContractError, match="logical_id required"):
        resolve_lifecycle(
            minimum_candidate_window_present=True,
            previously_confirmed=True,
            evidence_complete=True,
            evidence_invalidated=False,
            reverse_segment_confirmed=True,
        )


def test_frozen_prefix_correction_requires_appended_event():
    with pytest.raises(SegmentRuleContractError, match="APPEND_REQUIRED"):
        validate_frozen_prefix_transition(
            before_prefix_hash="abc",
            after_prefix_hash="abc",
            before_event_count=10,
            after_event_count=10,
            original_confirmed_at_bar=20,
            revised_confirmed_at_bar=21,
            correction_occurred=True,
        )


def test_segment_boundary_rejects_identical_endpoints():
    with pytest.raises(SegmentRuleContractError, match="ENDPOINT_INVALID"):
        SegmentBoundaryInput(
            "segment:invalid",
            SegmentDirection.UP,
            SegmentDirection.UP,
            SegmentDirection.UP,
            "endpoint:1",
            "endpoint:1",
        )


@pytest.mark.parametrize(
    "factory",
    [
        lambda: CandidateChoice(1, 2, 1, "group"),
        lambda: InclusionContext(
            1, "sequence", SegmentDirection.UP, SegmentDirection.UP
        ),
        lambda: SegmentBoundaryInput(
            1,
            SegmentDirection.UP,
            SegmentDirection.UP,
            SegmentDirection.UP,
            "endpoint:0",
            "endpoint:1",
        ),
    ],
)
def test_stable_identity_fields_require_exact_strings(factory):
    with pytest.raises(SegmentRuleContractError):
        factory()
