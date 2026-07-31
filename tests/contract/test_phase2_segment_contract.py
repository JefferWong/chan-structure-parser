"""Phase 2 executable segment-contract tests."""
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from chan_parser.contracts.segment import (
    SegmentContractError,
    SegmentContractValidator,
)
from chan_parser.domain.lifecycle import StructureStatus, StrokeDirection
from chan_parser.domain.segment import Segment
from chan_parser.domain.stroke import Stroke


PROFILE_PATH = (
    Path(__file__).resolve().parents[2]
    / "configs"
    / "profiles"
    / "minimal_segment_contract_v1.yaml"
)


def profile() -> dict:
    with PROFILE_PATH.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def stroke(
    number: int,
    direction: StrokeDirection,
    start: int,
    end: int,
    *,
    status: StructureStatus = StructureStatus.CONFIRMED,
    start_fractal: str | None = None,
    end_fractal: str | None = None,
    price_shift: float = 0.0,
) -> Stroke:
    start_fx = start_fractal or f"fx_{start:03d}"
    end_fx = end_fractal or f"fx_{end:03d}"
    start_price = 100.0 + price_shift
    end_price = (
        start_price + 10.0
        if direction == StrokeDirection.UP
        else start_price - 10.0
    )
    return Stroke(
        object_id=f"stroke-object-{number}",
        logical_id=f"stroke:{start_fx}->{end_fx}",
        status=status,
        stroke_id=f"stroke_{number:06d}",
        direction=direction,
        start_fractal_id=start_fx,
        end_fractal_id=end_fx,
        start_price=start_price,
        end_price=end_price,
        start_bar_index=start,
        end_bar_index=end,
        merged_bar_count=end - start + 1,
        max_price=max(start_price, end_price),
        min_price=min(start_price, end_price),
    )


def valid_window(
    *,
    tail_status: StructureStatus = StructureStatus.CONFIRMED,
) -> list[Stroke]:
    return [
        stroke(1, StrokeDirection.UP, 1, 6),
        stroke(2, StrokeDirection.DOWN, 6, 11),
        stroke(
            3,
            StrokeDirection.UP,
            11,
            16,
            status=tail_status,
        ),
    ]


def valid_evidence() -> list[Stroke]:
    return [stroke(4, StrokeDirection.DOWN, 16, 21)]


def validator() -> SegmentContractValidator:
    return SegmentContractValidator(profile())


def test_profile_is_contract_only():
    loaded = profile()
    assert loaded["profile_version"] == "0.2.0"
    assert loaded["status"] == "CONTRACT_ONLY"
    assert loaded["segment"]["implementation_enabled"] is False
    assert loaded["prohibited_in_this_profile"]["center_or_zhongshu"] is True


def test_profile_wrapper_is_required():
    loaded = profile()["segment"]
    with pytest.raises(SegmentContractError):
        SegmentContractValidator(loaded)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("status",), "ACTIVE"),
        (("phase1_baseline_commit",), "wrong-baseline"),
        (("prohibited_in_this_profile", "center_or_zhongshu"), False),
        (("segment", "identity", "candidate_scheme"), "content_evidence_v1"),
    ],
)
def test_top_level_and_prohibition_changes_fail_closed(path, value):
    loaded = profile()
    target = loaded
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(SegmentContractError):
        SegmentContractValidator(loaded)



def test_unknown_segment_option_fails_closed():
    loaded = profile()
    loaded["segment"]["allow_gap_without_destruction"] = True
    with pytest.raises(SegmentContractError):
        SegmentContractValidator(loaded)


def test_unsupported_mode_fails_closed():
    loaded = profile()
    loaded["segment"]["mode"] = "three_strokes_are_a_segment"
    with pytest.raises(SegmentContractError):
        SegmentContractValidator(loaded)


def test_enabling_implementation_fails_closed():
    loaded = profile()
    loaded["segment"]["implementation_enabled"] = True
    with pytest.raises(SegmentContractError):
        SegmentContractValidator(loaded)


def test_valid_three_stroke_window_is_only_contract_eligible():
    result = validator().validate_candidate_window(valid_window())
    assert result.accepted
    assert result.reason_code == "SEGMENT_CONTRACT_ELIGIBLE"
    assert result.direction == StrokeDirection.UP
    assert result.feature_sequence_stroke_ids == ("stroke_000002",)
    assert result.confirmation_evidence_key == ""
    assert result.detail["contract_only"] is True
    assert result.detail["segment_constructed"] is False
    assert result.detail["segment_confirmed"] is False


def test_fewer_than_three_strokes_are_rejected():
    result = validator().validate_candidate_window(valid_window()[:2])
    assert not result.accepted
    assert result.reason_code == "SEGMENT_MINIMUM_STROKES"


def test_even_stroke_count_is_rejected():
    window = valid_window()
    window.append(stroke(4, StrokeDirection.DOWN, 16, 21))
    result = validator().validate_candidate_window(window)
    assert not result.accepted
    assert result.reason_code == "SEGMENT_ODD_STROKE_COUNT_REQUIRED"



def test_missing_stable_logical_identity_is_rejected():
    window = valid_window()
    window[1].logical_id = None
    result = validator().validate_candidate_window(window)
    assert not result.accepted
    assert result.reason_code == "SEGMENT_LOGICAL_ID_REQUIRED"


def test_duplicate_logical_stroke_is_rejected_despite_unique_runtime_ids():
    window = valid_window()
    window[2].logical_id = window[0].logical_id
    window[2].object_id = "different-runtime-object"
    window[2].stroke_id = "different-stroke-id"
    result = validator().validate_candidate_window(window)
    assert not result.accepted
    assert result.reason_code == "SEGMENT_DUPLICATE_STROKE_ID"


def test_non_alternating_directions_are_rejected():
    window = valid_window()
    window[1].direction = StrokeDirection.UP
    result = validator().validate_candidate_window(window)
    assert not result.accepted
    assert result.reason_code == "SEGMENT_STROKE_DIRECTION_NOT_ALTERNATING"


def test_disconnected_fractal_endpoints_are_rejected():
    window = valid_window()
    window[1].start_fractal_id = "unrelated_fx"
    result = validator().validate_candidate_window(window)
    assert not result.accepted
    assert result.reason_code == "SEGMENT_STROKE_ENDPOINT_NOT_CONTIGUOUS"


def test_disconnected_bar_endpoints_are_rejected():
    window = valid_window()
    window[1].start_bar_index = 7
    result = validator().validate_candidate_window(window)
    assert not result.accepted
    assert result.reason_code == "SEGMENT_STROKE_ENDPOINT_NOT_CONTIGUOUS"


def test_invalid_stroke_bar_range_is_rejected():
    window = valid_window()
    window[1].end_bar_index = window[1].start_bar_index
    result = validator().validate_candidate_window(window)
    assert not result.accepted
    assert result.reason_code == "SEGMENT_STROKE_BAR_RANGE_INVALID"


def test_only_tail_may_be_provisional_for_candidate():
    window = valid_window()
    window[1].status = StructureStatus.PROVISIONAL
    result = validator().validate_candidate_window(window)
    assert not result.accepted
    assert result.reason_code == "SEGMENT_ONLY_TAIL_MAY_BE_PROVISIONAL"


def test_provisional_tail_is_allowed_for_provisional_target():
    window = valid_window(tail_status=StructureStatus.PROVISIONAL)
    result = validator().validate_candidate_window(
        window,
        target_status=StructureStatus.PROVISIONAL,
    )
    assert result.accepted


def test_nonconfirmed_target_rejects_destruction_evidence():
    result = validator().validate_candidate_window(
        valid_window(),
        target_status=StructureStatus.PROVISIONAL,
        destruction_evidence=valid_evidence(),
    )
    assert not result.accepted
    assert result.reason_code == "SEGMENT_DESTRUCTION_EVIDENCE_NOT_ALLOWED_FOR_TARGET"


def test_confirmed_target_rejects_unconfirmed_window_stroke():
    window = valid_window(tail_status=StructureStatus.PROVISIONAL)
    result = validator().validate_candidate_window(
        window,
        target_status=StructureStatus.CONFIRMED,
        destruction_evidence=valid_evidence(),
    )
    assert not result.accepted
    assert result.reason_code == "SEGMENT_UNCONFIRMED_WINDOW_STROKE"


def test_confirmed_target_requires_explicit_destruction_evidence():
    result = validator().validate_candidate_window(
        valid_window(),
        target_status=StructureStatus.CONFIRMED,
    )
    assert not result.accepted
    assert result.reason_code == "SEGMENT_DESTRUCTION_EVIDENCE_REQUIRED"


def test_destruction_evidence_must_be_confirmed():
    evidence = valid_evidence()
    evidence[0].status = StructureStatus.PROVISIONAL
    result = validator().validate_candidate_window(
        valid_window(),
        target_status=StructureStatus.CONFIRMED,
        destruction_evidence=evidence,
    )
    assert not result.accepted
    assert result.reason_code == "SEGMENT_DESTRUCTION_EVIDENCE_UNCONFIRMED"


def test_destruction_evidence_must_follow_candidate():
    evidence = [stroke(4, StrokeDirection.DOWN, 15, 20)]
    result = validator().validate_candidate_window(
        valid_window(),
        target_status=StructureStatus.CONFIRMED,
        destruction_evidence=evidence,
    )
    assert not result.accepted
    assert result.reason_code == "SEGMENT_DESTRUCTION_EVIDENCE_TOO_EARLY"


def test_destruction_evidence_must_connect_to_candidate_tail():
    evidence = [stroke(4, StrokeDirection.DOWN, 17, 22)]
    result = validator().validate_candidate_window(
        valid_window(),
        target_status=StructureStatus.CONFIRMED,
        destruction_evidence=evidence,
    )
    assert not result.accepted
    assert result.reason_code == "SEGMENT_DESTRUCTION_EVIDENCE_ENDPOINT_NOT_CONTIGUOUS"


def test_destruction_evidence_cannot_reuse_window_logical_identity():
    window = valid_window()
    evidence = valid_evidence()
    evidence[0].logical_id = window[1].logical_id
    evidence[0].object_id = "new-runtime-object"
    evidence[0].stroke_id = "new-stroke-id"
    result = validator().validate_candidate_window(
        window,
        target_status=StructureStatus.CONFIRMED,
        destruction_evidence=evidence,
    )
    assert not result.accepted
    assert result.reason_code == "SEGMENT_DESTRUCTION_EVIDENCE_REUSES_WINDOW"


def test_duplicate_destruction_evidence_logical_identity_is_rejected():
    evidence = [
        stroke(4, StrokeDirection.DOWN, 16, 21),
        stroke(5, StrokeDirection.UP, 21, 26),
    ]
    evidence[1].logical_id = evidence[0].logical_id
    result = validator().validate_candidate_window(
        valid_window(),
        target_status=StructureStatus.CONFIRMED,
        destruction_evidence=evidence,
    )
    assert not result.accepted
    assert result.reason_code == "SEGMENT_DESTRUCTION_EVIDENCE_DUPLICATE_STROKE_ID"


def test_destruction_evidence_sequence_must_alternate():
    evidence = [
        stroke(4, StrokeDirection.DOWN, 16, 21),
        stroke(5, StrokeDirection.DOWN, 21, 26),
    ]
    result = validator().validate_candidate_window(
        valid_window(),
        target_status=StructureStatus.CONFIRMED,
        destruction_evidence=evidence,
    )
    assert not result.accepted
    assert result.reason_code == "SEGMENT_DESTRUCTION_EVIDENCE_DIRECTION_NOT_ALTERNATING"


def test_confirmed_eligibility_reports_no_future_leak_boundary():
    result = validator().validate_candidate_window(
        valid_window(),
        target_status=StructureStatus.CONFIRMED,
        destruction_evidence=valid_evidence(),
    )
    assert result.accepted
    assert result.earliest_confirmation_bar == 21
    assert result.confirmation_evidence_key
    assert result.detail["segment_confirmed"] is False


def test_candidate_identity_is_stable_across_lifecycle_and_evidence_is_separate():
    window = valid_window()
    evidence = valid_evidence()
    provisional = validator().validate_candidate_window(
        deepcopy(window),
        target_status=StructureStatus.PROVISIONAL,
    )
    confirmed = validator().validate_candidate_window(
        deepcopy(window),
        target_status=StructureStatus.CONFIRMED,
        destruction_evidence=deepcopy(evidence),
    )
    assert provisional.candidate_key == confirmed.candidate_key
    assert provisional.confirmation_evidence_key == ""
    assert confirmed.confirmation_evidence_key

    changed_evidence = deepcopy(evidence)
    changed_evidence[0].end_price -= 1.0
    changed_confirmation = validator().validate_candidate_window(
        deepcopy(window),
        target_status=StructureStatus.CONFIRMED,
        destruction_evidence=changed_evidence,
    )
    assert changed_confirmation.candidate_key == confirmed.candidate_key
    assert (
        changed_confirmation.confirmation_evidence_key
        != confirmed.confirmation_evidence_key
    )

    changed_window = deepcopy(window)
    changed_window[2].end_price += 1.0
    changed_candidate = validator().validate_candidate_window(changed_window)
    assert changed_candidate.candidate_key != provisional.candidate_key


def test_segment_schema_hash_ignores_runtime_object_identity():
    first = Segment(
        object_id="runtime-a",
        segment_id="segment_000001",
        direction=StrokeDirection.UP,
        start_stroke_id="stroke_000001",
        end_stroke_id="stroke_000003",
        stroke_ids=["stroke_000001", "stroke_000002", "stroke_000003"],
        feature_sequence_stroke_ids=["stroke_000002"],
        start_bar_index=1,
        end_bar_index=16,
        start_price=100,
        end_price=120,
    )
    second = deepcopy(first)
    second.object_id = "runtime-b"
    assert first.content_hash() == second.content_hash()
    assert first.to_dict()["object_id"] != second.to_dict()["object_id"]
