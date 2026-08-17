"""Contract tests for pure Segment lifecycle event intents."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from chan_parser.contracts.segment_lifecycle import (
    SegmentLifecycleContractError,
    derive_segment_lifecycle_intents,
    filter_new_segment_lifecycle_intents,
    validate_segment_lifecycle_profile,
)
from chan_parser.contracts.segment_rules import (
    DestructionCase,
    FeatureElementRuleInput,
    FeatureEndpointEvidence,
    FeatureFractalType,
    FeatureIntervalSemantics,
    PriceInterval,
    PrimarySequenceContext,
    SegmentDirection,
    classify_primary_destruction_case,
)
from chan_parser.domain.lifecycle import EventType, StructureStatus, StrokeDirection
from chan_parser.domain.segment import Segment


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "configs/profiles/minimal_segment_lifecycle_contract_v1.yaml"


def profile() -> dict:
    return yaml.safe_load(PROFILE.read_text(encoding="utf-8"))


def endpoint(name: str, price: float, bar: int, stroke: str) -> FeatureEndpointEvidence:
    return FeatureEndpointEvidence(name, (stroke,), price, bar)


def element(
    name: str,
    low: float,
    high: float,
    start_bar: int,
    *,
    sequence_id: str = "primary:test",
) -> FeatureElementRuleInput:
    high_endpoint = endpoint(f"{name}:high", high, start_bar, f"stroke:{name}")
    low_endpoint = endpoint(f"{name}:low", low, start_bar + 1, f"stroke:{name}")
    return FeatureElementRuleInput(
        name,
        sequence_id,
        SegmentDirection.DOWN,
        FeatureIntervalSemantics.STRUCTURAL_PRICE_RANGE,
        high_endpoint,
        low_endpoint,
        high_endpoint,
        low_endpoint,
        PriceInterval(low, high, (f"stroke:{name}",)),
        True,
        start_bar + 1,
    )


def first_case_evidence():
    elements = (
        element("left", 1, 4, 1),
        element("center", 3, 7, 3),
        element("right", 2, 5, 5),
    )
    return classify_primary_destruction_case(
        *elements,
        context=PrimarySequenceContext(
            SegmentDirection.UP,
            "primary:test",
            tuple(
                source
                for item in elements
                for source in item.interval.source_stroke_logical_ids
            ),
        ),
    )


def pending_evidence():
    elements = (
        element("left", 1, 2, 1),
        element("center", 3, 7, 3),
        element("right", 2.5, 5, 5),
    )
    return classify_primary_destruction_case(
        *elements,
        context=PrimarySequenceContext(
            SegmentDirection.UP,
            "primary:test",
            tuple(
                source
                for item in elements
                for source in item.interval.source_stroke_logical_ids
            ),
        ),
    )


def confirmed_segment() -> Segment:
    evidence = first_case_evidence()
    assert evidence.endpoint is not None
    return Segment(
        object_id="segment_000001_000005_U_r1",
        logical_id="segment:stroke:0->stroke:4",
        revision=1,
        status=StructureStatus.CONFIRMED,
        created_at_bar=8,
        confirmed_at_bar=8,
        rule_profile="minimal_segment_engine_core_v1",
        rule_version="0.1.0",
        segment_id="segment_000001_000005_U",
        direction=StrokeDirection.UP,
        start_stroke_id="stroke_000000",
        end_stroke_id="stroke_000004",
        stroke_ids=["stroke_000000", "stroke_000001", "stroke_000002", "stroke_000003", "stroke_000004"],
        feature_sequence_stroke_ids=["stroke_000001", "stroke_000003", "stroke_000005"],
        destruction_evidence_stroke_ids=["stroke_000005"],
        start_price=0,
        end_price=evidence.endpoint.price,
        start_bar_index=0,
        end_bar_index=evidence.endpoint.bar_index,
        confirmation_requirements=[],
        repaint_risk="NONE",
    )


def derive():
    return derive_segment_lifecycle_intents(
        outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
        segment=confirmed_segment(),
        primary_evidence=first_case_evidence(),
    )


def test_profile_exact_mapping_accepts_frozen_profile():
    assert validate_segment_lifecycle_profile(profile()) is None


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        (("profile_version",), "0.2.0"),
        (("source_segment_baseline_commit",), "wrong"),
        (("transition", "target_status"), "PROVISIONAL"),
        (("direct_confirmed_intents", "event_types"), ["OBJECT_CONFIRMED"]),
        (("integration", "event_emission_enabled"), True),
    ],
)
def test_profile_wrong_values_fail_closed(mutation, value):
    loaded = profile()
    target = loaded
    for key in mutation[:-1]:
        target = target[key]
    target[mutation[-1]] = value
    with pytest.raises(SegmentLifecycleContractError):
        validate_segment_lifecycle_profile(loaded)


@pytest.mark.parametrize("change", ["missing", "unknown", "wrong_type"])
def test_profile_shape_fails_closed(change):
    loaded = profile()
    if change == "missing":
        del loaded["transition"]["source_status"]
    elif change == "unknown":
        loaded["transition"]["surprise"] = False
    else:
        loaded["transition"]["provisional_allowed"] = 0
    with pytest.raises(SegmentLifecycleContractError):
        validate_segment_lifecycle_profile(loaded)


def test_first_case_returns_exact_ordered_direct_confirmed_intents():
    intents = derive()
    assert tuple(item.event_type for item in intents) == (
        EventType.CREATED,
        EventType.CONFIRMED,
    )
    assert tuple(item.reason_code for item in intents) == (
        "SEGMENT_FIRST_CASE_CREATED",
        "SEGMENT_FIRST_CASE_CONFIRMED",
    )
    assert all(item.object_type == "segment" for item in intents)


def test_both_intents_bind_the_segment_confirmation_bar():
    intents = derive()
    assert {item.occurred_at_bar_id for item in intents} == {"bar_000009"}


def test_intent_identity_and_profile_are_derived_from_segment():
    segment = confirmed_segment()
    intents = derive_segment_lifecycle_intents(
        outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
        segment=segment,
        primary_evidence=first_case_evidence(),
    )
    assert all(item.object_id == segment.object_id for item in intents)
    assert all(item.logical_id == segment.logical_id for item in intents)
    assert all(item.rule_profile == segment.rule_profile for item in intents)
    assert all(item.rule_version == segment.rule_version for item in intents)


def test_detail_is_complete_and_internally_derived():
    evidence = first_case_evidence()
    intent = derive()[0]
    assert set(intent.detail) == {
        "segment_id", "direction", "start_stroke_id", "end_stroke_id",
        "stroke_ids", "feature_sequence_stroke_ids",
        "destruction_evidence_stroke_ids", "primary_evidence_key",
        "primary_sequence_id", "primary_element_logical_ids",
        "feature_fractal_type", "endpoint",
    }
    assert intent.detail["primary_evidence_key"] == evidence.evidence_key
    assert intent.detail["endpoint"]["endpoint_id"] == evidence.endpoint.endpoint_id
    with pytest.raises(TypeError):
        intent.detail["direction"] = "DOWN"


def test_intent_keys_are_stable_content_derived_and_event_specific():
    first = derive()
    second = derive()
    assert tuple(item.intent_key for item in first) == tuple(
        item.intent_key for item in second
    )
    assert first[0].intent_key != first[1].intent_key
    assert all(item.intent_key.startswith("segment_lifecycle:") for item in first)


@pytest.mark.parametrize(
    ("history", "expected_indices", "fails_closed"),
    [
        ("NONE", (0, 1), False),
        ("CREATED_ONLY", (1,), False),
        ("CONFIRMED_ONLY", (), True),
        ("CREATED_AND_CONFIRMED", (), False),
    ],
)
def test_filter_requires_canonical_history_prefix(
    history, expected_indices, fails_closed
):
    intents = derive()
    histories = {
        "NONE": set(),
        "CREATED_ONLY": {intents[0].intent_key},
        "CONFIRMED_ONLY": {intents[1].intent_key},
        "CREATED_AND_CONFIRMED": {
            intents[0].intent_key,
            intents[1].intent_key,
        },
    }
    existing = histories[history]
    before = set(existing)
    intents_before = tuple(intents)
    if fails_closed:
        with pytest.raises(
            SegmentLifecycleContractError,
            match="SEGMENT_LIFECYCLE_HISTORY_NOT_CANONICAL_PREFIX",
        ):
            filter_new_segment_lifecycle_intents(intents, existing)
    else:
        assert filter_new_segment_lifecycle_intents(intents, existing) == tuple(
            intents[index] for index in expected_indices
        )
    assert existing == before
    assert intents == intents_before


def test_filter_idempotency_ignores_unrelated_existing_keys():
    intents = derive()
    unrelated = {"segment_lifecycle:unrelated"}
    assert filter_new_segment_lifecycle_intents(intents, unrelated) == intents
    assert unrelated == {"segment_lifecycle:unrelated"}


@pytest.mark.parametrize(
    "outcome",
    ["SEGMENT_FEATURE_WINDOW_INCOMPLETE", "SEGMENT_PRIMARY_FRACTAL_NOT_FOUND"],
)
def test_incomplete_and_no_fractal_have_zero_intents(outcome):
    assert derive_segment_lifecycle_intents(
        outcome_code=outcome, segment=None, primary_evidence=None
    ) == ()


def test_second_case_pending_has_zero_intents():
    assert pending_evidence().destruction_case == DestructionCase.SECOND_CASE_PENDING
    assert derive_segment_lifecycle_intents(
        outcome_code="SEGMENT_SECOND_CASE_PENDING",
        segment=None,
        primary_evidence=pending_evidence(),
    ) == ()


@pytest.mark.parametrize(
    ("outcome", "segment", "evidence"),
    [
        ("UNKNOWN", None, None),
        ("SEGMENT_FIRST_CASE_CONFIRMED", None, first_case_evidence()),
        ("SEGMENT_FIRST_CASE_CONFIRMED", confirmed_segment(), None),
        ("SEGMENT_FIRST_CASE_CONFIRMED", confirmed_segment(), pending_evidence()),
        ("SEGMENT_SECOND_CASE_PENDING", confirmed_segment(), pending_evidence()),
        ("SEGMENT_SECOND_CASE_PENDING", None, first_case_evidence()),
    ],
)
def test_outcome_payload_mismatches_fail_closed(outcome, segment, evidence):
    with pytest.raises(SegmentLifecycleContractError):
        derive_segment_lifecycle_intents(
            outcome_code=outcome, segment=segment, primary_evidence=evidence
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("status", StructureStatus.PROVISIONAL, "status"),
        ("object_id", "", "object_id"),
        ("logical_id", "", "logical_id"),
        ("segment_id", "", "segment_id"),
        ("created_at_bar", 7, "same bar"),
        ("confirmed_at_bar", 3, "same bar"),
        ("direction", StrokeDirection.DOWN, "direction"),
        ("end_bar_index", 2, "endpoint bar"),
        ("end_price", 999.0, "endpoint price"),
    ],
)
def test_invalid_first_case_segment_fails_closed(field, value, message):
    with pytest.raises(SegmentLifecycleContractError, match=message):
        derive_segment_lifecycle_intents(
            outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
            segment=replace(confirmed_segment(), **{field: value}),
            primary_evidence=first_case_evidence(),
        )


def test_future_visible_confirmation_fails_closed():
    segment = replace(
        confirmed_segment(),
        created_at_bar=2,
        confirmed_at_bar=2,
    )
    with pytest.raises(SegmentLifecycleContractError, match="precede"):
        derive_segment_lifecycle_intents(
            outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
            segment=segment,
            primary_evidence=first_case_evidence(),
        )


def test_filter_rejects_noncanonical_or_duplicate_intents():
    intents = derive()
    with pytest.raises(SegmentLifecycleContractError):
        filter_new_segment_lifecycle_intents((intents[1], intents[0]), set())
    with pytest.raises(SegmentLifecycleContractError):
        filter_new_segment_lifecycle_intents((intents[0], intents[0]), set())


def test_filter_rejects_fabricated_content_or_key():
    intents = derive()
    forged_key = replace(intents[0], intent_key="segment_lifecycle:forged")
    with pytest.raises(SegmentLifecycleContractError, match="intent_key"):
        filter_new_segment_lifecycle_intents((forged_key, intents[1]), set())
    forged_reason = replace(intents[0], reason_code="FORGED")
    with pytest.raises(SegmentLifecycleContractError):
        filter_new_segment_lifecycle_intents((forged_reason, intents[1]), set())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "CONFIRMED"),
        ("direction", "UP"),
        ("rule_profile", "foreign"),
        ("rule_version", "9.9.9"),
        ("start_stroke_id", ""),
        ("stroke_ids", [["mutable"]]),
        ("feature_sequence_stroke_ids", ["duplicate", "duplicate"]),
        ("destruction_evidence_stroke_ids", []),
    ],
)
def test_segment_source_types_and_profile_fail_closed(field, value):
    with pytest.raises(SegmentLifecycleContractError):
        derive_segment_lifecycle_intents(
            outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
            segment=replace(confirmed_segment(), **{field: value}),
            primary_evidence=first_case_evidence(),
        )


def test_canonical_evidence_reason_is_required():
    forged = replace(first_case_evidence(), reason_code="FORGED")
    with pytest.raises(SegmentLifecycleContractError, match="reason"):
        derive_segment_lifecycle_intents(
            outcome_code="SEGMENT_FIRST_CASE_CONFIRMED",
            segment=confirmed_segment(),
            primary_evidence=forged,
        )
