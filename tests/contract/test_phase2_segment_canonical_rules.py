"""Executable gates for the stateless Phase 2 segment-rule oracle."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import inspect
import json
from pathlib import Path

import pytest
import yaml

from chan_parser.contracts.segment_rules import (
    CandidateChoice,
    DestructionCase,
    FeatureElementRuleInput,
    FeatureEndpointEvidence,
    FeatureFractalType,
    FeatureIntervalSemantics,
    InclusionContext,
    InclusionSeed,
    IntervalRelation,
    LifecycleResolution,
    OriginalDirectionExtremeEvidence,
    PendingSecondCaseContext,
    PriceInterval,
    PrimarySequenceContext,
    PrimaryDestructionEvidence,
    SecondaryConfirmationEvidence,
    SecondarySequenceContext,
    SegmentBoundaryInput,
    SegmentDirection,
    SegmentRuleContractError,
    StrokeRuleInput,
    SequenceBoundaryNature,
    build_feature_sequence,
    build_pending_second_case_context,
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
    resolve_second_case_outcome,
    resolve_second_case_evidence_sequence,
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
    "CASE2-UP-STRICT-NEW-HIGH-INVALIDATE-001",
    "CASE2-DOWN-STRICT-NEW-LOW-INVALIDATE-001",
    "CASE2-UP-EQUAL-HIGH-STAYS-PENDING-001",
    "CASE2-DOWN-EQUAL-LOW-STAYS-PENDING-001",
    "CASE2-WRONG-DIRECTION-STAYS-PENDING-001",
    "CASE2-EXTREME-BAR-ORDER-REJECT-001",
    "CASE2-NEGATIVE-BAR-REJECT-001",
    "CASE2-SECOND-SEQUENCE-INCLUSION-001", "WINNER-LEFTMOST-001",
    "WINNER-SAME-ENDPOINT-001", "LIFECYCLE-INVALIDATED-001",
    "LIFECYCLE-REPLACED-001", "TIMING-NO-BACKFILL-001",
    "SG-CONNECTION-001", "FREEZE-APPEND-001",
    "LIFECYCLE-NO-CANDIDATE-REJECT-001",
    "LIFECYCLE-CANDIDATE-001", "LIFECYCLE-PROVISIONAL-001",
    "LIFECYCLE-EVIDENCE-WITHOUT-CANDIDATE-REJECT-001",
    "LIFECYCLE-CONTRADICTORY-EVIDENCE-REJECT-001",
    "SECOND-SEQUENCE-UNRELATED-ENDPOINT-REJECT-001",
    "SECOND-SEQUENCE-NONCONTIGUOUS-LEFT-CENTER-ACCEPT-001",
    "SECOND-SEQUENCE-NONCONTIGUOUS-CENTER-RIGHT-ACCEPT-001",
    "SECOND-SEQUENCE-ID-MISMATCH-REJECT-001",
    "SECOND-SEQUENCE-NONNORMALIZED-ELEMENT-REJECT-001",
    "SECOND-SEQUENCE-DUPLICATE-ELEMENT-ID-REJECT-001",
    "PRIMARY-SEQUENCE-ID-MISMATCH-REJECT-001",
    "PRIMARY-DUPLICATE-ELEMENT-ID-REJECT-001",
    "PRIMARY-NONNORMALIZED-ELEMENT-REJECT-001",
    "PRIMARY-NONCONTIGUOUS-LEFT-CENTER-ACCEPT-001",
    "PRIMARY-NONCONTIGUOUS-CENTER-RIGHT-ACCEPT-001",
    "PRIMARY-EMPTY-PROVENANCE-REJECT-001",
    "PRIMARY-DUPLICATE-PROVENANCE-REJECT-001",
    "PRIMARY-PROVENANCE-MISMATCH-REJECT-001",
    "PRIMARY-WRONG-FEATURE-DIRECTION-REJECT-001",
    "PRIMARY-ENDPOINT-PRICE-MISMATCH-REJECT-001",
    "PRIMARY-ENDPOINT-BAR-MISMATCH-REJECT-001",
    "PRIMARY-BAR-ORDER-REJECT-001",
    "SECONDARY-WRONG-FEATURE-DIRECTION-REJECT-001",
    "SECONDARY-ENDPOINT-EVIDENCE-MISMATCH-REJECT-001",
    "PENDING-CONTEXT-REQUIRES-PRIMARY-PENDING-EVIDENCE-001",
    "PENDING-CONTEXT-REJECTS-FIRST-CASE-001",
    "PENDING-CONTEXT-REJECTS-NONE-CASE-001",
    "PENDING-CONTEXT-ENDPOINT-DERIVED-001",
    "PENDING-CONTEXT-CALLER-CANNOT-REPLACE-ENDPOINT-001",
    "PRIMARY-EVIDENCE-KEY-DETERMINISTIC-001",
    "CASE2-ARBITRATION-CONFIRM-BEFORE-EXTREME-001",
    "CASE2-ARBITRATION-EXTREME-BEFORE-CONFIRM-001",
    "CASE2-ARBITRATION-SAME-BAR-CONFIRM-WINS-001",
    "CASE2-ARBITRATION-NONSTRICT-THEN-CONFIRM-001",
    "CASE2-ARBITRATION-EVIDENCE-KEY-MISMATCH-REJECT-001",
    "CASE2-ARBITRATION-ENDPOINT-MISMATCH-REJECT-001",
    "CASE2-ARBITRATION-ORDER-INDEPENDENT-001",
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


def endpoint(
    endpoint_id: str, price: float, bar_index: int, *sources: str
) -> FeatureEndpointEvidence:
    return FeatureEndpointEvidence(endpoint_id, tuple(sources), price, bar_index)


def standard_elements(
    left_values: tuple[float, float],
    center_values: tuple[float, float],
    right_values: tuple[float, float],
    *,
    sequence_ids: tuple[str, str, str] = ("primary:a",) * 3,
    element_ids: tuple[str, str, str] = (
        "primary:left",
        "primary:center",
        "primary:right",
    ),
    endpoint_ids: tuple[str, str, str, str] = (
        "endpoint:0",
        "endpoint:1",
        "endpoint:2",
        "endpoint:3",
    ),
    normalized: tuple[bool, bool, bool] = (True, True, True),
    directions: tuple[SegmentDirection, ...] = (SegmentDirection.DOWN,) * 3,
    provenance: tuple[tuple[str, ...], ...] = (
        ("stroke:1",),
        ("stroke:2",),
        ("stroke:3",),
    ),
    shared_endpoints: tuple[FeatureEndpointEvidence, ...] | None = None,
    visible_bars: tuple[int, int, int] = (10, 20, 30),
    extremum_overrides: dict[
        tuple[int, str], FeatureEndpointEvidence
    ] | None = None,
) -> tuple[FeatureElementRuleInput, ...]:
    values = (left_values, center_values, right_values)
    if shared_endpoints is None:
        shared_endpoints = tuple(
            endpoint(
                endpoint_ids[index],
                40 - index * 10
                if directions[0] == SegmentDirection.DOWN
                else index * 10,
                index * 10,
                f"endpoint-source:{index}",
            )
            for index in range(4)
        )
    extremum_overrides = extremum_overrides or {}
    elements = []
    for index in range(3):
        source = provenance[index][0] if provenance[index] else "missing"
        high = extremum_overrides.get(
            (index, "high"),
            endpoint(
                f"{element_ids[index]}:high",
                values[index][1],
                shared_endpoints[index].bar_index,
                source,
            ),
        )
        low = extremum_overrides.get(
            (index, "low"),
            endpoint(
                f"{element_ids[index]}:low",
                values[index][0],
                shared_endpoints[index].bar_index,
                source,
            ),
        )
        elements.append(FeatureElementRuleInput(
            element_ids[index],
            sequence_ids[index],
            directions[index],
            FeatureIntervalSemantics.NORMALIZED_FEATURE_RANGE,
            shared_endpoints[index],
            shared_endpoints[index + 1],
            high,
            low,
            interval(*values[index], *provenance[index]),
            normalized[index],
            visible_bars[index],
        ))
    return tuple(elements)


def primary_context(
    direction: SegmentDirection,
    *,
    sequence_id: str = "primary:a",
    provenance: tuple[str, ...] = ("stroke:1", "stroke:2", "stroke:3"),
) -> PrimarySequenceContext:
    return PrimarySequenceContext(direction, sequence_id, provenance)


def classify_primary(
    direction: SegmentDirection,
    left: tuple[float, float],
    center: tuple[float, float],
    right: tuple[float, float],
) -> PrimaryDestructionEvidence:
    feature_direction = (
        SegmentDirection.DOWN
        if direction == SegmentDirection.UP
        else SegmentDirection.UP
    )
    return classify_primary_destruction_case(
        *standard_elements(
            left,
            center,
            right,
            directions=(feature_direction,) * 3,
        ),
        context=primary_context(direction),
    )


def pending_context(
    direction: SegmentDirection = SegmentDirection.UP,
    *,
    endpoint_id: str = "endpoint:pending",
    defining_sources: tuple[str, ...] = ("stroke:2",),
    price: float = 10,
    bar_index: int = 10,
) -> PendingSecondCaseContext:
    if direction == SegmentDirection.UP:
        values = (
            (price - 9, price - 8),
            (price - 7, price),
            (price - 8, price - 2),
        )
        endpoint_kind = "high"
        feature_direction = SegmentDirection.DOWN
    else:
        values = (
            (price + 7, price + 8),
            (price, price + 6),
            (price + 3, price + 7),
        )
        endpoint_kind = "low"
        feature_direction = SegmentDirection.UP
    pending_endpoint = endpoint(
        endpoint_id, price, bar_index, *defining_sources
    )
    shared_prices = (
        (40, 30, 20, 10)
        if feature_direction == SegmentDirection.DOWN
        else (0, 10, 20, 30)
    )
    shared = tuple(
        endpoint(
            f"primary:shared:{index}",
            shared_prices[index],
            bar_index + (index - 1) * 10,
            f"primary:endpoint-source:{index}",
        )
        for index in range(4)
    )
    primary = classify_primary_destruction_case(
        *standard_elements(
            *values,
            directions=(feature_direction,) * 3,
            provenance=(("stroke:1",), defining_sources, ("stroke:3",)),
            shared_endpoints=shared,
            visible_bars=(
                shared[1].bar_index,
                shared[2].bar_index,
                shared[3].bar_index,
            ),
            extremum_overrides={(1, endpoint_kind): pending_endpoint},
        ),
        context=primary_context(
            direction,
            provenance=("stroke:1",) + defining_sources + ("stroke:3",),
        ),
    )
    return build_pending_second_case_context(
        primary,
        secondary_sequence_id="second:a",
    )


def secondary_elements(
    left_values: tuple[float, float] = (3, 7),
    center_values: tuple[float, float] = (1, 4),
    right_values: tuple[float, float] = (2, 6),
    *,
    pending: PendingSecondCaseContext | None = None,
    sequence_ids: tuple[str, str, str] = ("second:a",) * 3,
    element_ids: tuple[str, str, str] = (
        "element:left",
        "element:center",
        "element:right",
    ),
    normalized: tuple[bool, bool, bool] = (True, True, True),
    directions: tuple[SegmentDirection, ...] | None = None,
    visible_bars: tuple[int, int, int] = (40, 50, 60),
) -> tuple[FeatureElementRuleInput, ...]:
    pending = pending or pending_context()
    directions = directions or (pending.original_direction,) * 3
    step = 10 if directions[0] == SegmentDirection.UP else -10
    shared = (
        pending.pending_endpoint,
        endpoint(
            "secondary:end:1",
            pending.pending_endpoint.price + step,
            30,
            "endpoint-source:s1",
        ),
        endpoint(
            "secondary:end:2",
            pending.pending_endpoint.price + 2 * step,
            40,
            "endpoint-source:s2",
        ),
        endpoint(
            "secondary:end:3",
            pending.pending_endpoint.price + 3 * step,
            50,
            "endpoint-source:s3",
        ),
    )
    return standard_elements(
        left_values,
        center_values,
        right_values,
        sequence_ids=sequence_ids,
        element_ids=element_ids,
        normalized=normalized,
        directions=directions,
        provenance=(("stroke:a",), ("stroke:b",), ("stroke:c",)),
        shared_endpoints=shared,
        visible_bars=visible_bars,
    )


def secondary_context(
    *,
    pending: PendingSecondCaseContext | None = None,
    normalized_source_logical_ids: tuple[str, ...] = (
        "stroke:a",
        "stroke:b",
        "stroke:c",
    ),
) -> SecondarySequenceContext:
    return SecondarySequenceContext(
        pending or pending_context(),
        normalized_source_logical_ids,
    )


def extreme_evidence(
    context: PendingSecondCaseContext,
    observed_price: float,
    observed_bar: int,
    *,
    sources: tuple[str, ...] = ("stroke:observed",),
    primary_evidence_key: str | None = None,
    pending_endpoint_id: str | None = None,
) -> OriginalDirectionExtremeEvidence:
    return OriginalDirectionExtremeEvidence(
        primary_evidence_key or context.primary_evidence.evidence_key,
        pending_endpoint_id or context.pending_endpoint.endpoint_id,
        observed_price,
        observed_bar,
        sources,
    )


def confirmation_evidence(
    context: PendingSecondCaseContext,
    confirmed_at_bar: int = 60,
) -> SecondaryConfirmationEvidence:
    result = classify_secondary_confirmation(
        *secondary_elements(
            pending=context,
            visible_bars=(40, 50, confirmed_at_bar),
        ),
        context=secondary_context(pending=context),
    )
    assert isinstance(result, SecondaryConfirmationEvidence)
    return result


def all_fixture_cases() -> list[dict]:
    cases = []
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        cases.extend(json.loads(path.read_text(encoding="utf-8")))
    return cases


def test_profile_loads_and_is_rules_only():
    loaded = profile()
    validate_segment_canonical_rules_profile(loaded)
    assert loaded["status"] == "CANONICAL_RULES_ONLY"
    assert loaded["profile_version"] == "1.0.1"
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
    assert len(cases) == len(REQUIRED_FIXTURE_IDS)
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
        relation = classify_interval_relation(first, second)
        included = relation in {
            IntervalRelation.CONTAINS,
            IntervalRelation.CONTAINED_BY,
            IntervalRelation.EQUAL,
        }
        if "included" in data:
            assert included is data["included"]
        boundary = SequenceBoundaryNature.NORMAL
        if data.get("crosses_first_case_boundary", False):
            boundary = SequenceBoundaryNature.FIRST_CASE_CROSS_BOUNDARY
        elif data.get("second_feature_sequence", False):
            boundary = SequenceBoundaryNature.SECOND_FEATURE_SEQUENCE
        if expected.get("merged") is False:
            with pytest.raises(SegmentRuleContractError, match=case["reason_code"]):
                merge_included_intervals(
                    first,
                    second,
                    InclusionSeed(data.get("seed", "UP")),
                    context=inclusion_context(boundary),
                )
            assert expected["merged"] is False
            return
        merged = merge_included_intervals(
            first,
            second,
            InclusionSeed(data.get("seed", "UP")),
            context=inclusion_context(boundary),
        )
        if "merged" in expected and isinstance(expected["merged"], list):
            assert [merged.low, merged.high] == expected["merged"]
        elif "merged" in expected:
            assert expected["merged"] is True
        if "sources" in expected:
            assert list(merged.source_stroke_logical_ids) == expected["sources"]
        return
    if fixture_id.startswith("FRACTAL-"):
        if fixture_id == "FRACTAL-WRONG-DIRECTION-REJECT-001":
            fractal = classify_strict_feature_fractal(
                interval(*data["left"]),
                interval(*data["center"]),
                interval(*data["right"]),
            )
            assert fractal.value == data["fractal"]
            result = classify_primary(
                SegmentDirection(data["candidate_direction"]),
                tuple(data["left"]),
                tuple(data["center"]),
                tuple(data["right"]),
            )
            assert (
                result.destruction_case != DestructionCase.NONE
            ) is expected["destruction_confirmed"]
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
        result = classify_primary(
            SegmentDirection(data["direction"]),
            tuple(data["left"]),
            tuple(data["center"]),
            tuple(data["right"]),
        )
        assert result.destruction_case.value == expected["case"]
        assert result.endpoint is not None
        assert result.endpoint.price == expected["endpoint"]
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
        if "original_segment_continues" in expected:
            assert (
                result.original_segment_continues
                is expected["original_segment_continues"]
            )
        return
    if fixture_id == "CASE1-FAILED-CONTINUATION-001":
        result = classify_failed_pen_break(
            pen_break_observed=data["pen_break"],
            required_fractal_formed=data["complete_fractal"],
            countermove_invalidated=data["countermove_invalidated"],
        )
        assert result.destruction_case.value == expected["case"]
        assert result.reason_code == case["reason_code"]
        if "original_segment_continues" in expected:
            assert (
                result.original_segment_continues
                is expected["original_segment_continues"]
            )
        return
    if fixture_id in {"CASE2-UP-PENDING-001", "CASE2-DOWN-PENDING-001"}:
        direction = SegmentDirection(data["direction"])
        result = classify_primary(
            direction,
            tuple(data["left"]),
            tuple(data["center"]),
            tuple(data["right"]),
        )
        assert result.destruction_case.value == expected["case"]
        assert result.reason_code == case["reason_code"]
        if "original_segment_continues" in expected:
            assert (
                result.original_segment_continues
                is expected["original_segment_continues"]
            )
        return
    if fixture_id in {
        "CASE2-SECOND-FRACTAL-CONFIRM-001",
        "CASE2-GAP-NOT-CLOSED-CONFIRM-001",
    }:
        if "original_gap_closed" in expected:
            assert has_feature_gap(
                interval(*data["primary_gap_first"]),
                interval(*data["primary_gap_center"]),
            ) is not expected["original_gap_closed"]
        canonical_context = secondary_context()
        result = classify_secondary_confirmation(
            *secondary_elements(
                tuple(data["left"]),
                tuple(data["center"]),
                tuple(data["right"]),
            ),
            context=canonical_context,
        )
        assert isinstance(result, SecondaryConfirmationEvidence)
        outcome = resolve_second_case_outcome(
            canonical_context,
            secondary_confirmation=result,
            extreme_evidence=None,
        )
        assert outcome.destruction_case.value == expected["case"]
        assert result.feature_fractal_type.value == data["second_sequence_fractal"]
        assert outcome.reason_code == case["reason_code"]
        return
    if fixture_id.startswith("CASE2-") and fixture_id in {
        "CASE2-UP-STRICT-NEW-HIGH-INVALIDATE-001",
        "CASE2-DOWN-STRICT-NEW-LOW-INVALIDATE-001",
        "CASE2-UP-EQUAL-HIGH-STAYS-PENDING-001",
        "CASE2-DOWN-EQUAL-LOW-STAYS-PENDING-001",
        "CASE2-WRONG-DIRECTION-STAYS-PENDING-001",
        "CASE2-EXTREME-BAR-ORDER-REJECT-001",
        "CASE2-NEGATIVE-BAR-REJECT-001",
    }:
        direction = SegmentDirection(data["original_direction"])
        def classify_fixture_extreme():
            context = pending_context(
                direction,
                endpoint_id=data["pending_endpoint_id"],
                defining_sources=tuple(
                    data["pending_endpoint_source_logical_ids"]
                ),
                price=data["pending_endpoint_price"],
                bar_index=data["pending_endpoint_bar_index"],
            )
            return classify_pending_second_case_invalidation(
                context,
                extreme_evidence=extreme_evidence(
                    context,
                    data["observed_extreme_price"],
                    data["observed_at_bar_index"],
                ),
            )
        if expected.get("accepted") is False:
            with pytest.raises(SegmentRuleContractError, match=case["reason_code"]):
                classify_fixture_extreme()
            return
        result = classify_fixture_extreme()
        assert result.destruction_case.value == expected["case"]
        assert result.reason_code == case["reason_code"]
        if "original_segment_continues" in expected:
            assert (
                result.original_segment_continues
                is expected["original_segment_continues"]
            )
        return
    if fixture_id == "CASE2-SECOND-SEQUENCE-INCLUSION-001":
        merged = merge_included_intervals(
            interval(*data["a"], "stroke:a"),
            interval(*data["b"], "stroke:b"),
            InclusionSeed(data["seed"]),
            context=inclusion_context(
                SequenceBoundaryNature(data["sequence_boundary_nature"])
            ),
        )
        assert [merged.low, merged.high] == expected["merged"]
        assert list(merged.source_stroke_logical_ids) == expected[
            "source_stroke_logical_ids"
        ]
        return
    if fixture_id.startswith("SECOND-SEQUENCE-"):
        mutation = data["mutation"]
        kwargs = {}
        if mutation == "unrelated_left_start":
            elements = list(secondary_elements())
            elements[0] = replace(
                elements[0],
                start_endpoint=replace(
                    elements[0].start_endpoint,
                    endpoint_id="endpoint:unrelated",
                ),
            )
            with pytest.raises(SegmentRuleContractError, match=case["reason_code"]):
                classify_secondary_confirmation(
                    *elements,
                    context=secondary_context(),
                )
            return
        elif mutation in {
            "noncontiguous_left_center",
            "noncontiguous_center_right",
        }:
            elements = list(secondary_elements())
            target_index = 1 if mutation == "noncontiguous_left_center" else 2
            elements[target_index] = replace(
                elements[target_index],
                start_endpoint=replace(
                    elements[target_index].start_endpoint,
                    endpoint_id=f"endpoint:noncontiguous:{target_index}",
                ),
            )
            context = secondary_context()
            evidence = classify_secondary_confirmation(*elements, context=context)
            assert isinstance(evidence, SecondaryConfirmationEvidence)
            outcome = resolve_second_case_outcome(
                context,
                secondary_confirmation=evidence,
                extreme_evidence=None,
            )
            assert expected["accepted"] is True
            assert outcome.destruction_case.value == expected["case"]
            return
        elif mutation == "sequence_mismatch":
            kwargs["sequence_ids"] = ("second:a", "second:b", "second:a")
        elif mutation == "nonnormalized_center":
            kwargs["normalized"] = (True, False, True)
        elif mutation == "duplicate_element_id":
            kwargs["element_ids"] = (
                "element:left", "element:left", "element:right"
            )
        with pytest.raises(SegmentRuleContractError, match=case["reason_code"]):
            classify_secondary_confirmation(
                *secondary_elements(**kwargs),
                context=secondary_context(),
            )
        return
    if fixture_id.startswith("PRIMARY-"):
        if fixture_id == "PRIMARY-EVIDENCE-KEY-DETERMINISTIC-001":
            first = pending_context().primary_evidence
            second = pending_context().primary_evidence
            assert first.evidence_key == second.evidence_key
            return
        mutation = data["mutation"]
        element_kwargs = {}
        context_kwargs = {}
        if mutation == "sequence_mismatch":
            element_kwargs["sequence_ids"] = (
                "primary:a", "primary:b", "primary:a"
            )
        elif mutation == "duplicate_element_id":
            element_kwargs["element_ids"] = (
                "primary:left", "primary:left", "primary:right"
            )
        elif mutation == "nonnormalized_center":
            element_kwargs["normalized"] = (True, False, True)
        elif mutation in {
            "noncontiguous_left_center",
            "noncontiguous_center_right",
        }:
            elements = list(standard_elements((1, 2), (3, 7), (2, 5)))
            target_index = 1 if mutation == "noncontiguous_left_center" else 2
            elements[target_index] = replace(
                elements[target_index],
                start_endpoint=replace(
                    elements[target_index].start_endpoint,
                    endpoint_id=f"endpoint:noncontiguous:{target_index}",
                ),
            )
            result = classify_primary_destruction_case(
                *elements,
                context=primary_context(SegmentDirection.UP),
            )
            assert expected["accepted"] is True
            assert result.destruction_case.value == expected["case"]
            return
        elif mutation == "empty_provenance":
            element_kwargs["provenance"] = (
                (),
                ("stroke:2",),
                ("stroke:3",),
            )
            context_kwargs["provenance"] = ("stroke:2", "stroke:3")
        elif mutation == "duplicate_provenance":
            element_kwargs["provenance"] = (
                ("stroke:1",),
                ("stroke:1",),
                ("stroke:3",),
            )
            context_kwargs["provenance"] = (
                "stroke:1", "stroke:2", "stroke:3"
            )
        elif mutation == "provenance_mismatch":
            context_kwargs["provenance"] = (
                "stroke:1", "stroke:2", "stroke:other"
            )
        elif mutation == "wrong_feature_direction":
            element_kwargs["directions"] = (SegmentDirection.UP,) * 3
        elif mutation in {"endpoint_price_mismatch", "endpoint_bar_mismatch"}:
            elements = list(standard_elements((1, 2), (3, 7), (2, 5)))
            shared = elements[1].start_endpoint
            elements[1] = replace(
                elements[1],
                start_endpoint=replace(
                    shared,
                    price=shared.price + 1
                    if mutation == "endpoint_price_mismatch"
                    else shared.price,
                    bar_index=shared.bar_index - 1
                    if mutation == "endpoint_bar_mismatch"
                    else shared.bar_index,
                ),
            )
            with pytest.raises(SegmentRuleContractError, match=case["reason_code"]):
                classify_primary_destruction_case(
                    *elements,
                    context=primary_context(SegmentDirection.UP),
                )
            return
        elif mutation == "bar_order":
            elements = list(standard_elements((1, 2), (3, 7), (2, 5)))
            with pytest.raises(SegmentRuleContractError, match=case["reason_code"]):
                replace(
                    elements[0],
                    end_endpoint=replace(
                        elements[0].end_endpoint,
                        bar_index=elements[0].start_endpoint.bar_index,
                    ),
                )
            return
        with pytest.raises(SegmentRuleContractError, match=case["reason_code"]):
            classify_primary_destruction_case(
                *standard_elements((1, 2), (3, 7), (2, 5), **element_kwargs),
                context=primary_context(
                    SegmentDirection.UP,
                    **context_kwargs,
                ),
            )
        return
    if fixture_id.startswith("SECONDARY-"):
        mutation = data["mutation"]
        context = secondary_context()
        elements = list(secondary_elements(pending=context.pending))
        if mutation == "wrong_feature_direction":
            elements = list(
                secondary_elements(
                    pending=context.pending,
                    directions=(SegmentDirection.DOWN,) * 3,
                )
            )
        elif mutation == "endpoint_evidence_mismatch":
            elements[1] = replace(
                elements[1],
                start_endpoint=replace(
                    elements[1].start_endpoint,
                    price=elements[1].start_endpoint.price + 1,
                ),
            )
        with pytest.raises(SegmentRuleContractError, match=case["reason_code"]):
            classify_secondary_confirmation(*elements, context=context)
        return
    if fixture_id.startswith("PENDING-CONTEXT-"):
        if fixture_id == "PENDING-CONTEXT-REQUIRES-PRIMARY-PENDING-EVIDENCE-001":
            with pytest.raises(SegmentRuleContractError, match=case["reason_code"]):
                PendingSecondCaseContext(object(), "second:a")
            return
        if fixture_id == "PENDING-CONTEXT-REJECTS-FIRST-CASE-001":
            primary = classify_primary(
                SegmentDirection.UP, (3, 5), (4, 8), (2, 6)
            )
            with pytest.raises(SegmentRuleContractError, match=case["reason_code"]):
                build_pending_second_case_context(
                    primary, secondary_sequence_id="second:a"
                )
            return
        if fixture_id == "PENDING-CONTEXT-REJECTS-NONE-CASE-001":
            primary = classify_primary(
                SegmentDirection.UP, (3, 7), (1, 4), (2, 6)
            )
            with pytest.raises(SegmentRuleContractError, match=case["reason_code"]):
                build_pending_second_case_context(
                    primary, secondary_sequence_id="second:a"
                )
            return
        if fixture_id == "PENDING-CONTEXT-ENDPOINT-DERIVED-001":
            context = pending_context()
            assert context.pending_endpoint is context.primary_evidence.endpoint
            return
        if fixture_id == "PENDING-CONTEXT-CALLER-CANNOT-REPLACE-ENDPOINT-001":
            parameters = inspect.signature(PendingSecondCaseContext).parameters
            assert all(name not in parameters for name in data[
                "forbidden_constructor_fields"
            ])
            context = build_pending_second_case_context(
                pending_context().primary_evidence,
                secondary_sequence_id="second:a",
            )
            outcome = resolve_second_case_outcome(
                context,
                secondary_confirmation=None,
                extreme_evidence=None,
            )
            assert outcome.destruction_case == DestructionCase.SECOND_CASE_PENDING
            return
    if fixture_id.startswith("CASE2-ARBITRATION-"):
        context = pending_context()
        if data.get("mutation") == "evidence_key_mismatch":
            bad = extreme_evidence(
                context,
                11,
                70,
                primary_evidence_key="primary:other",
            )
            with pytest.raises(SegmentRuleContractError, match=case["reason_code"]):
                resolve_second_case_outcome(
                    context,
                    secondary_confirmation=None,
                    extreme_evidence=bad,
                )
            return
        if data.get("mutation") == "endpoint_mismatch":
            bad = extreme_evidence(
                context,
                11,
                70,
                pending_endpoint_id="endpoint:other",
            )
            with pytest.raises(SegmentRuleContractError, match=case["reason_code"]):
                resolve_second_case_outcome(
                    context,
                    secondary_confirmation=None,
                    extreme_evidence=bad,
                )
            return
        confirmation = confirmation_evidence(context, data["confirmation_bar"])
        canonical_context = secondary_context(pending=context)
        extreme = extreme_evidence(
            context,
            data["extreme_price"],
            data["extreme_bar"],
        )
        first = resolve_second_case_outcome(
            canonical_context,
            secondary_confirmation=confirmation,
            extreme_evidence=extreme,
        )
        if fixture_id == "CASE2-ARBITRATION-ORDER-INDEPENDENT-001":
            confirmation_first = resolve_second_case_evidence_sequence(
                canonical_context,
                (confirmation, extreme),
            )
            extreme_first = resolve_second_case_evidence_sequence(
                canonical_context,
                (extreme, confirmation),
            )
            assert first == confirmation_first == extreme_first
            return
        assert first.destruction_case.value == expected["case"]
        assert first.reason_code == case["reason_code"]
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
    if fixture_id.startswith("LIFECYCLE-"):
        kwargs = {
            "minimum_candidate_window_present": True,
            "provisional_evidence_present": False,
            "previously_confirmed": False,
            "evidence_complete": False,
            "evidence_invalidated": False,
            "reverse_segment_logical_id": None,
            "reverse_segment_confirmed": False,
            **data,
        }
        if expected.get("accepted") is False:
            with pytest.raises(SegmentRuleContractError, match=case["reason_code"]):
                resolve_lifecycle(**kwargs)
            return
        status, replaced_by = resolve_lifecycle(**kwargs)
        assert status.value == expected["status"]
        assert replaced_by == expected.get("replaced_by")
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


def test_feature_sequence_malformed_stroke_fails_closed_before_attribute_access():
    for malformed in ([object()], object()):
        with pytest.raises(
            SegmentRuleContractError,
            match="strokes must contain StrokeRuleInput values",
        ):
            build_feature_sequence(
                SegmentDirection.UP, malformed, sequence_id="fixture-sequence"
            )


def test_merge_malformed_interval_fails_closed_before_relation_classification():
    with pytest.raises(
        SegmentRuleContractError,
        match="PriceInterval values required",
    ):
        merge_included_intervals(
            object(),
            interval(2, 4, "stroke:b"),
            InclusionSeed.UP,
            context=inclusion_context(),
        )


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
    up = classify_primary(
        SegmentDirection.UP,
        (3, 5),
        (4, 8),
        (2, 6),
    )
    down = classify_primary(
        SegmentDirection.DOWN,
        (3, 7),
        (1, 4),
        (2, 6),
    )
    assert up.endpoint is not None and down.endpoint is not None
    assert (up.destruction_case, up.endpoint.price) == (DestructionCase.FIRST_CASE, 8)
    assert (down.destruction_case, down.endpoint.price) == (DestructionCase.FIRST_CASE, 1)


def test_pen_break_only_remains_provisional():
    result = classify_failed_pen_break(
        pen_break_observed=True,
        required_fractal_formed=False,
        countermove_invalidated=False,
    )
    assert result.destruction_case == DestructionCase.NONE
    assert result.reason_code == "PEN_BREAK_PROVISIONAL"


def test_second_case_is_pending_when_primary_fractal_has_gap():
    up = classify_primary(
        SegmentDirection.UP,
        (1, 2),
        (3, 7),
        (2, 5),
    )
    down = classify_primary(
        SegmentDirection.DOWN,
        (7, 8),
        (2, 6),
        (3, 7),
    )
    assert up.destruction_case == DestructionCase.SECOND_CASE_PENDING
    assert down.destruction_case == DestructionCase.SECOND_CASE_PENDING


def test_secondary_fractal_confirms_without_original_gap_closure_input():
    confirmed = classify_secondary_confirmation(
        *secondary_elements(),
        context=secondary_context(),
    )
    assert isinstance(confirmed, SecondaryConfirmationEvidence)
    assert confirmed.feature_fractal_type == FeatureFractalType.BOTTOM


def test_pending_second_case_is_invalidated_by_new_original_extreme():
    context = pending_context(SegmentDirection.DOWN, price=10, bar_index=10)
    result = classify_pending_second_case_invalidation(
        context,
        extreme_evidence=extreme_evidence(context, 9, 20),
    )
    assert result.destruction_case == DestructionCase.INVALIDATED
    assert result.reason_code == "PENDING_DESTRUCTION_INVALIDATED"
    with pytest.raises(SegmentRuleContractError, match="follow pending endpoint"):
        classify_pending_second_case_invalidation(
            context,
            extreme_evidence=extreme_evidence(context, 9, 10),
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
        provisional_evidence_present=False,
        previously_confirmed=False,
        evidence_complete=False,
        evidence_invalidated=True,
    ) == (LifecycleResolution.INVALIDATED, None)
    assert resolve_lifecycle(
        minimum_candidate_window_present=True,
        provisional_evidence_present=False,
        previously_confirmed=True,
        evidence_complete=True,
        evidence_invalidated=False,
        reverse_segment_logical_id="segment:reverse",
        reverse_segment_confirmed=True,
    ) == (LifecycleResolution.REPLACED, "segment:reverse")
    with pytest.raises(SegmentRuleContractError, match="REPLACED requires"):
        resolve_lifecycle(
            minimum_candidate_window_present=True,
            provisional_evidence_present=False,
            previously_confirmed=False,
            evidence_complete=False,
            evidence_invalidated=False,
            reverse_segment_logical_id="segment:reverse",
            reverse_segment_confirmed=True,
        )
    assert resolve_lifecycle(
        minimum_candidate_window_present=True,
        provisional_evidence_present=False,
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
    with pytest.raises(SegmentRuleContractError, match="PENDING"):
        PendingSecondCaseContext(
            classify_primary(
                SegmentDirection.UP,
                (3, 5),
                (4, 8),
                (2, 6),
            ),
            "second:a",
        )
    with pytest.raises(SegmentRuleContractError, match="NORMALIZATION"):
        classify_secondary_confirmation(
            *secondary_elements(normalized=(True, False, True)),
            context=secondary_context(),
        )
    with pytest.raises(SegmentRuleContractError, match="DIRECTION_MISMATCH"):
        classify_secondary_confirmation(
            *secondary_elements(
                directions=(SegmentDirection.DOWN,) * 3
            ),
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


@pytest.mark.parametrize("invalid_hash", [1, True, None, ""])
@pytest.mark.parametrize("field", ["before_prefix_hash", "after_prefix_hash"])
def test_frozen_prefix_hashes_require_nonempty_exact_strings(field, invalid_hash):
    kwargs = {
        "before_prefix_hash": "abc",
        "after_prefix_hash": "abc",
        "before_event_count": 10,
        "after_event_count": 11,
        "original_confirmed_at_bar": 20,
        "revised_confirmed_at_bar": 20,
        "correction_occurred": True,
    }
    kwargs[field] = invalid_hash
    with pytest.raises(SegmentRuleContractError, match="hashes required"):
        validate_frozen_prefix_transition(**kwargs)


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
    with pytest.raises(SegmentRuleContractError, match="duplicate normalized"):
        classify_secondary_confirmation(
            *secondary_elements(),
            context=secondary_context(
                normalized_source_logical_ids=(
                    "stroke:pending",
                    "stroke:a",
                    "stroke:b",
                    "stroke:b",
                )
            ),
        )


def test_lifecycle_rejects_contradictory_confirmation_and_replacement_evidence():
    with pytest.raises(
        SegmentRuleContractError, match="LIFECYCLE_EVIDENCE_WITHOUT_CANDIDATE"
    ):
        resolve_lifecycle(
            minimum_candidate_window_present=False,
            provisional_evidence_present=False,
            previously_confirmed=False,
            evidence_complete=True,
            evidence_invalidated=False,
        )
    with pytest.raises(SegmentRuleContractError, match="logical_id required"):
        resolve_lifecycle(
            minimum_candidate_window_present=True,
            provisional_evidence_present=False,
            previously_confirmed=True,
            evidence_complete=True,
            evidence_invalidated=False,
            reverse_segment_confirmed=True,
        )


def test_candidate_choice_malformed_input_fails_closed_before_attribute_access():
    with pytest.raises(
        SegmentRuleContractError,
        match="candidates must contain CandidateChoice values",
    ):
        choose_deterministic_candidate([object()])
    with pytest.raises(
        SegmentRuleContractError,
        match="at least one confirmable candidate required",
    ):
        choose_deterministic_candidate(object())


def test_segment_boundary_malformed_inputs_fail_closed_before_attribute_access():
    valid = SegmentBoundaryInput(
        "segment:up",
        SegmentDirection.UP,
        SegmentDirection.UP,
        SegmentDirection.UP,
        "endpoint:0",
        "endpoint:1",
    )
    with pytest.raises(
        SegmentRuleContractError,
        match="current SegmentBoundaryInput required",
    ):
        validate_segment_boundaries(object())
    with pytest.raises(
        SegmentRuleContractError,
        match="previous_confirmed SegmentBoundaryInput required",
    ):
        validate_segment_boundaries(valid, previous_confirmed=object())


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


def test_confirmation_time_change_requires_correction_flag():
    with pytest.raises(SegmentRuleContractError, match="CORRECTION_FLAG_REQUIRED"):
        validate_frozen_prefix_transition(
            before_prefix_hash="abc",
            after_prefix_hash="abc",
            before_event_count=10,
            after_event_count=10,
            original_confirmed_at_bar=20,
            revised_confirmed_at_bar=21,
            correction_occurred=False,
        )


def test_unchanged_confirmation_with_correction_still_requires_append():
    with pytest.raises(SegmentRuleContractError, match="APPEND_REQUIRED"):
        validate_frozen_prefix_transition(
            before_prefix_hash="abc",
            after_prefix_hash="abc",
            before_event_count=10,
            after_event_count=10,
            original_confirmed_at_bar=20,
            revised_confirmed_at_bar=20,
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


@pytest.mark.parametrize("bad_index", [True, -1, 1.5])
def test_stroke_bar_indices_are_nonnegative_exact_ints(bad_index):
    with pytest.raises(SegmentRuleContractError):
        StrokeRuleInput(
            "stroke:test",
            "runtime:test",
            SegmentDirection.UP,
            1,
            2,
            bad_index,
            3,
            "sequence:test",
        )


@pytest.mark.parametrize("bad_index", [True, -1, 1.5])
def test_candidate_bar_indices_are_nonnegative_exact_ints(bad_index):
    with pytest.raises(SegmentRuleContractError):
        CandidateChoice("candidate:test", 3, bad_index, "group:test")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"observed_at_bar_index": 2.5},
        {"observed_at_bar_index": -1},
        {"observed_extreme_price": float("inf")},
    ],
)
def test_extreme_evidence_rejects_invalid_prices_and_bar_indices(kwargs):
    values = {
        "primary_evidence_key": "primary:key",
        "pending_endpoint_id": "endpoint:pending",
        "observed_extreme_price": 11,
        "observed_at_bar_index": 20,
        "observed_source_stroke_logical_ids": ("stroke:observed",),
    }
    values.update(kwargs)
    with pytest.raises(SegmentRuleContractError):
        OriginalDirectionExtremeEvidence(**values)


@pytest.mark.parametrize(
    ("endpoint_bar", "visible_bars"),
    [
        (True, [2]),
        (1.5, [2]),
        (-1, [2]),
        (1, [True]),
        (1, [2.5]),
        (1, [-1]),
    ],
)
def test_confirmation_bar_rejects_invalid_index_types_and_ranges(
    endpoint_bar, visible_bars
):
    with pytest.raises(SegmentRuleContractError):
        confirmation_bar(endpoint_bar, visible_bars)


@pytest.mark.parametrize("invalid", [object(), None])
def test_public_interval_apis_fail_closed_before_attribute_access(invalid):
    valid = interval(1, 2, "stroke:valid")
    calls = (
        lambda: classify_interval_relation(invalid, valid),
        lambda: has_feature_gap(valid, invalid),
        lambda: derive_inclusion_seed(invalid, valid),
        lambda: classify_strict_feature_fractal(valid, invalid, valid),
    )
    for call in calls:
        with pytest.raises(SegmentRuleContractError):
            call()


@pytest.mark.parametrize("invalid", [object(), None])
def test_merge_context_fails_closed_before_attribute_access(invalid):
    with pytest.raises(SegmentRuleContractError):
        merge_included_intervals(
            interval(1, 4, "stroke:a"),
            interval(2, 3, "stroke:b"),
            InclusionSeed.UP,
            context=invalid,
        )


@pytest.mark.parametrize("invalid", [object(), None, "12", b"12"])
def test_confirmation_visibility_requires_non_string_sequence(invalid):
    with pytest.raises(SegmentRuleContractError):
        confirmation_bar(10, invalid)


def test_normalized_feature_range_allows_structural_endpoints_outside_interval():
    element = standard_elements((1, 2), (3, 7), (2, 5))[0]
    assert element.interval_semantics == (
        FeatureIntervalSemantics.NORMALIZED_FEATURE_RANGE
    )
    assert element.start_endpoint.price > element.interval.high
    assert element.end_endpoint.price > element.interval.high


def test_structural_price_range_rejects_endpoints_outside_interval():
    element = standard_elements((1, 2), (3, 7), (2, 5))[0]
    with pytest.raises(SegmentRuleContractError, match="outside feature interval"):
        replace(
            element,
            interval_semantics=FeatureIntervalSemantics.STRUCTURAL_PRICE_RANGE,
        )


def test_structural_price_range_accepts_endpoints_inside_interval():
    element = standard_elements((1, 2), (3, 7), (2, 5))[0]
    structural = replace(
        element,
        interval_semantics=FeatureIntervalSemantics.STRUCTURAL_PRICE_RANGE,
        interval=PriceInterval(30, 40, element.interval.source_stroke_logical_ids),
        high_endpoint=replace(element.high_endpoint, price=40),
        low_endpoint=replace(element.low_endpoint, price=30),
    )
    assert structural.start_endpoint.price == 40
    assert structural.end_endpoint.price == 30


def test_feature_interval_semantics_fails_closed_without_valid_enum():
    element = standard_elements((1, 2), (3, 7), (2, 5))[0]
    with pytest.raises(SegmentRuleContractError, match="semantics required"):
        replace(element, interval_semantics="NORMALIZED_FEATURE_RANGE")


def test_primary_fractal_and_pen_break_are_separate_apis():
    assert "pen_break_observed" not in inspect.signature(
        classify_primary_destruction_case
    ).parameters
    assert {
        "pen_break_observed",
        "required_fractal_formed",
        "countermove_invalidated",
    } == set(inspect.signature(classify_failed_pen_break).parameters)


def test_endpoint_defining_strokes_are_distinct_from_left_element_provenance():
    left, center, right = secondary_elements()
    context = secondary_context()
    assert set(
        context.pending.pending_endpoint.defining_stroke_logical_ids
    ).isdisjoint(left.interval.source_stroke_logical_ids)
    result = classify_secondary_confirmation(
        left,
        center,
        right,
        context=context,
    )
    assert isinstance(result, SecondaryConfirmationEvidence)


@pytest.mark.parametrize(
    "overrides",
    [
        {"provisional_evidence_present": True, "evidence_complete": True},
        {"evidence_complete": True, "evidence_invalidated": True},
        {"provisional_evidence_present": True, "evidence_invalidated": True},
    ],
)
def test_lifecycle_rejects_all_contradictory_evidence(overrides):
    values = {
        "minimum_candidate_window_present": True,
        "provisional_evidence_present": False,
        "previously_confirmed": False,
        "evidence_complete": False,
        "evidence_invalidated": False,
    }
    values.update(overrides)
    with pytest.raises(SegmentRuleContractError):
        resolve_lifecycle(**values)


def test_lifecycle_requires_confirmed_reverse_id_pair():
    with pytest.raises(SegmentRuleContractError, match="confirmed reverse"):
        resolve_lifecycle(
            minimum_candidate_window_present=True,
            provisional_evidence_present=False,
            previously_confirmed=True,
            evidence_complete=True,
            evidence_invalidated=False,
            reverse_segment_confirmed=True,
        )
    with pytest.raises(SegmentRuleContractError, match="confirmed reverse"):
        resolve_lifecycle(
            minimum_candidate_window_present=True,
            provisional_evidence_present=False,
            previously_confirmed=True,
            evidence_complete=True,
            evidence_invalidated=False,
            reverse_segment_logical_id="segment:reverse",
            reverse_segment_confirmed=False,
        )


@pytest.mark.parametrize("invalid_id", ["", 123])
def test_lifecycle_rejects_invalid_reverse_logical_id_types(invalid_id):
    with pytest.raises(SegmentRuleContractError, match="nonempty string"):
        resolve_lifecycle(
            minimum_candidate_window_present=True,
            provisional_evidence_present=False,
            previously_confirmed=True,
            evidence_complete=True,
            evidence_invalidated=False,
            reverse_segment_logical_id=invalid_id,
            reverse_segment_confirmed=bool(invalid_id),
        )


def test_extreme_evidence_cannot_replace_pending_price_or_bar():
    parameters = inspect.signature(OriginalDirectionExtremeEvidence).parameters
    assert "pending_endpoint_price" not in parameters
    assert "pending_endpoint_bar_index" not in parameters
    assert "original_direction" not in parameters


def test_pending_context_rejects_duplicate_endpoint_provenance():
    with pytest.raises(SegmentRuleContractError, match="duplicate feature"):
        FeatureEndpointEvidence(
            "endpoint:pending",
            ("stroke:pending", "stroke:pending"),
            10,
            10,
        )


def test_secondary_confirmation_rejects_invalid_context_type():
    with pytest.raises(SegmentRuleContractError, match="context required"):
        classify_secondary_confirmation(
            *secondary_elements(),
            context=None,
        )


def test_pending_invalidation_rejects_invalid_context_type():
    valid_context = pending_context()
    with pytest.raises(SegmentRuleContractError, match="context required"):
        classify_pending_second_case_invalidation(
            None,
            extreme_evidence=extreme_evidence(valid_context, 11, 20),
        )


def test_secondary_confirmation_api_does_not_accept_original_gap_closure():
    parameters = inspect.signature(classify_secondary_confirmation).parameters
    assert "original_gap_closed" not in parameters
    assert "pen_break_observed" not in parameters


def test_primary_classifier_rejects_bare_price_intervals():
    with pytest.raises(SegmentRuleContractError, match="feature elements"):
        classify_primary_destruction_case(
            interval(1, 2),
            interval(3, 7),
            interval(2, 5),
            context=primary_context(SegmentDirection.UP),
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"endpoint_id": 1},
        {"defining_stroke_logical_ids": ["stroke:pending"]},
        {"defining_stroke_logical_ids": ()},
        {"defining_stroke_logical_ids": ("",)},
        {"price": float("nan")},
        {"bar_index": True},
        {"bar_index": -1},
    ],
)
def test_pending_endpoint_evidence_fails_closed(kwargs):
    values = {
        "endpoint_id": "endpoint:pending",
        "defining_stroke_logical_ids": ("stroke:pending",),
        "price": 10,
        "bar_index": 10,
    }
    values.update(kwargs)
    with pytest.raises(SegmentRuleContractError):
        FeatureEndpointEvidence(**values)


def test_pending_endpoint_is_single_source_for_price_and_bar():
    context = pending_context(SegmentDirection.UP, price=100, bar_index=50)
    pending = classify_pending_second_case_invalidation(
        context,
        extreme_evidence=extreme_evidence(context, 99, 51),
    )
    assert pending.destruction_case == DestructionCase.SECOND_CASE_PENDING
    with pytest.raises(SegmentRuleContractError, match="follow pending endpoint"):
        classify_pending_second_case_invalidation(
            context,
            extreme_evidence=extreme_evidence(context, 101, 50),
        )


def test_huge_integers_do_not_leak_native_overflow():
    huge = 10**309
    assert PriceInterval(0, huge).high == huge
    evidence = OriginalDirectionExtremeEvidence(
        "primary:key",
        "endpoint:pending",
        huge,
        20,
        ("stroke:observed",),
    )
    assert evidence.observed_extreme_price == huge


def test_primary_evidence_key_binds_order_direction_sequence_and_endpoint():
    evidence = pending_context().primary_evidence
    repeated = pending_context().primary_evidence
    assert evidence.evidence_key == repeated.evidence_key
    with pytest.raises(SegmentRuleContractError, match="key mismatch"):
        replace(evidence, evidence_key="forged")


def test_pending_context_exposes_only_primary_derived_baseline():
    context = pending_context()
    assert context.pending_endpoint is context.primary_evidence.endpoint
    assert context.original_direction == context.primary_evidence.candidate_direction
    assert set(inspect.signature(PendingSecondCaseContext).parameters) == {
        "primary_evidence",
        "secondary_sequence_id",
    }


def test_secondary_confirmation_binds_primary_endpoint_and_visibility_time():
    context = pending_context()
    evidence = confirmation_evidence(context, 73)
    assert evidence.primary_evidence_key == context.primary_evidence.evidence_key
    assert evidence.pending_endpoint_id == context.pending_endpoint.endpoint_id
    assert evidence.confirmed_at_bar == 73
    with pytest.raises(SegmentRuleContractError, match="key mismatch"):
        replace(evidence, evidence_key="forged")


def test_secondary_evidence_key_binds_full_element_payload():
    evidence = confirmation_evidence(pending_context())
    left = evidence.feature_elements[0]
    changed_interval = PriceInterval(
        left.interval.low,
        left.interval.high + 1,
        left.interval.source_stroke_logical_ids,
    )
    changed_high = replace(left.high_endpoint, price=changed_interval.high)
    changed_left = replace(
        left,
        interval=changed_interval,
        high_endpoint=changed_high,
    )
    with pytest.raises(SegmentRuleContractError, match="key mismatch"):
        replace(
            evidence,
            feature_elements=(changed_left,) + evidence.feature_elements[1:],
        )


def test_secondary_evidence_key_binds_legal_replaced_provenance():
    evidence = confirmation_evidence(pending_context())
    left = evidence.feature_elements[0]
    changed_source = ("stroke:replacement",)
    changed_left = replace(
        left,
        interval=replace(
            left.interval,
            source_stroke_logical_ids=changed_source,
        ),
        high_endpoint=replace(
            left.high_endpoint,
            defining_stroke_logical_ids=changed_source,
        ),
        low_endpoint=replace(
            left.low_endpoint,
            defining_stroke_logical_ids=changed_source,
        ),
    )
    normalized_sources = changed_source + evidence.normalized_source_logical_ids[1:]
    with pytest.raises(SegmentRuleContractError, match="key mismatch"):
        replace(
            evidence,
            feature_elements=(changed_left,) + evidence.feature_elements[1:],
            normalized_source_logical_ids=normalized_sources,
        )


def test_original_secondary_evidence_still_arbitrates():
    pending = pending_context()
    context = secondary_context(pending=pending)
    result = resolve_second_case_outcome(
        context,
        secondary_confirmation=confirmation_evidence(pending),
        extreme_evidence=None,
    )
    assert result.destruction_case == DestructionCase.SECOND_CASE_CONFIRMED


def test_secondary_evidence_must_match_canonical_context_provenance():
    pending = pending_context()
    canonical_context = secondary_context(pending=pending)
    evidence = confirmation_evidence(pending)
    assert evidence.primary_evidence_key == pending.primary_evidence.evidence_key
    assert evidence.pending_endpoint_id == pending.pending_endpoint.endpoint_id
    assert evidence.secondary_sequence_id == pending.secondary_sequence_id
    assert (
        evidence.normalized_source_logical_ids
        == canonical_context.normalized_source_logical_ids
    )
    confirmed = resolve_second_case_outcome(
        canonical_context,
        secondary_confirmation=evidence,
        extreme_evidence=None,
    )
    assert confirmed.destruction_case == DestructionCase.SECOND_CASE_CONFIRMED

    mismatched_context = SecondarySequenceContext(
        pending,
        ("stroke:other-a", "stroke:other-b", "stroke:other-c"),
    )
    assert mismatched_context.pending is canonical_context.pending
    assert (
        mismatched_context.pending.secondary_sequence_id
        == canonical_context.pending.secondary_sequence_id
    )
    with pytest.raises(
        SegmentRuleContractError,
        match="SECONDARY_NORMALIZED_PROVENANCE_MISMATCH",
    ):
        resolve_second_case_outcome(
            mismatched_context,
            secondary_confirmation=evidence,
            extreme_evidence=None,
        )


@pytest.mark.parametrize(
    "invalid",
    [object(), "not-evidence"],
)
def test_evidence_chain_public_entries_fail_closed(invalid):
    context = pending_context()
    with pytest.raises(SegmentRuleContractError):
        resolve_second_case_outcome(
            context,
            secondary_confirmation=invalid,
            extreme_evidence=None,
        )
    with pytest.raises(SegmentRuleContractError):
        resolve_second_case_outcome(
            context,
            secondary_confirmation=None,
            extreme_evidence=invalid,
        )


def test_arbitration_is_order_independent_and_same_bar_confirmation_wins():
    context = pending_context()
    canonical_context = secondary_context(pending=context)
    confirmation = confirmation_evidence(context, 70)
    extreme = extreme_evidence(context, 11, 70)
    confirmation_first = resolve_second_case_evidence_sequence(
        canonical_context,
        (confirmation, extreme),
    )
    extreme_first = resolve_second_case_evidence_sequence(
        canonical_context,
        (extreme, confirmation),
    )
    assert confirmation_first == extreme_first
    assert confirmation_first.destruction_case == (
        DestructionCase.SECOND_CASE_CONFIRMED
    )


def test_feature_element_rejects_endpoint_identity_rebinding():
    element = standard_elements((1, 2), (3, 7), (2, 5))[0]
    with pytest.raises(SegmentRuleContractError, match="identity evidence mismatch"):
        replace(
            element,
            high_endpoint=replace(
                element.high_endpoint,
                endpoint_id=element.start_endpoint.endpoint_id,
            ),
        )


def test_feature_element_direction_is_semantic_after_normalization():
    element = standard_elements((1, 2), (3, 7), (2, 5))[0]
    flat_price = element.start_endpoint.price
    normalized = replace(
        element,
        end_endpoint=replace(
            element.end_endpoint,
            price=flat_price,
        ),
    )
    assert normalized.direction == SegmentDirection.DOWN


def test_standard_feature_window_allows_time_gap_and_rejects_overlap():
    gap = list(standard_elements((1, 2), (3, 7), (2, 5)))
    gap_start_bar = gap[0].end_endpoint.bar_index + 2
    gap[1] = replace(
        gap[1],
        start_endpoint=replace(
            gap[1].start_endpoint,
            endpoint_id="endpoint:gap-start",
            bar_index=gap_start_bar,
        ),
        high_endpoint=replace(
            gap[1].high_endpoint,
            bar_index=gap_start_bar,
        ),
        low_endpoint=replace(
            gap[1].low_endpoint,
            bar_index=gap_start_bar,
        ),
    )
    result = classify_primary_destruction_case(
        *gap,
        context=primary_context(SegmentDirection.UP),
    )
    assert result.destruction_case == DestructionCase.SECOND_CASE_PENDING

    overlap = list(standard_elements((1, 2), (3, 7), (2, 5)))
    overlap[1] = replace(
        overlap[1],
        start_endpoint=replace(
            overlap[1].start_endpoint,
            endpoint_id="endpoint:overlap",
            bar_index=overlap[0].end_endpoint.bar_index - 1,
        ),
    )
    with pytest.raises(
        SegmentRuleContractError,
        match="PRIMARY_LEFT_CENTER_TIME_OVERLAP",
    ):
        classify_primary_destruction_case(
            *overlap,
            context=primary_context(SegmentDirection.UP),
        )
