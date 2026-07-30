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


def validator() -> SegmentContractValidator:
    return SegmentContractValidator(profile())


def test_profile_is_contract_only():
    loaded = profile()
    assert loaded["status"] == "CONTRACT_ONLY"
    assert loaded["segment"]["implementation_enabled"] is False
    assert loaded["prohibited_in_this_profile"]["center_or_zhongshu"] is True


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
    assert result.reason_code == "SEGMENT_ENDPOINT_NOT_CONTIGUOUS"


def test_disconnected_bar_endpoints_are_rejected():
    window = valid_window()
    window[1].start_bar_index = 7
    result = validator().validate_candidate_window(window)
    assert not result.accepted
    assert result.reason_code == "SEGMENT_ENDPOINT_NOT_CONTIGUOUS"


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


def test_confirmed_target_rejects_unconfirmed_window_stroke():
    window = valid_window(tail_status=StructureStatus.PROVISIONAL)
    evidence = [stroke(4, StrokeDirection.DOWN, 16, 21)]
    result = validator().validate_candidate_window(
        window,
        target_status=StructureStatus.CONFIRMED,
        destruction_evidence=evidence,
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
    evidence = [
        stroke(
            4,
            StrokeDirection.DOWN,
            16,
            21,
            status=StructureStatus.PROVISIONAL,
        )
    ]
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


def test_confirmed_eligibility_reports_no_future_leak_boundary():
    evidence = [stroke(4, StrokeDirection.DOWN, 16, 21)]
    result = validator().validate_candidate_window(
        valid_window(),
        target_status=StructureStatus.CONFIRMED,
        destruction_evidence=evidence,
    )
    assert result.accepted
    assert result.earliest_confirmation_bar == 21
    assert result.detail["segment_confirmed"] is False


def test_candidate_key_is_deterministic_and_evidence_sensitive():
    window = valid_window()
    evidence = [stroke(4, StrokeDirection.DOWN, 16, 21)]
    first = validator().validate_candidate_window(
        window,
        target_status=StructureStatus.CONFIRMED,
        destruction_evidence=evidence,
    )
    second = validator().validate_candidate_window(
        deepcopy(window),
        target_status=StructureStatus.CONFIRMED,
        destruction_evidence=deepcopy(evidence),
    )
    assert first.candidate_key == second.candidate_key

    changed_evidence = deepcopy(evidence)
    changed_evidence[0].end_price -= 1.0
    changed = validator().validate_candidate_window(
        deepcopy(window),
        target_status=StructureStatus.CONFIRMED,
        destruction_evidence=changed_evidence,
    )
    assert first.candidate_key != changed.candidate_key


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
