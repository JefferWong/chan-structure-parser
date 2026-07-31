"""Pure reference oracle for the Phase 2 canonical segment rule contract.

The functions in this module classify immutable inputs. They do not construct
segments, retain history, emit lifecycle events, or call parser engines.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Mapping, Sequence


class SegmentRuleContractError(ValueError):
    """Raised when an input or canonical-rules profile fails closed."""


class RuleClassification(str, Enum):
    ORIGINAL_CANONICAL_CORE = "ORIGINAL_CANONICAL_CORE"
    ENGINEERING_DETERMINISM_V1 = "ENGINEERING_DETERMINISM_V1"


class SegmentDirection(str, Enum):
    UP = "UP"
    DOWN = "DOWN"


class IntervalRelation(str, Enum):
    DISJOINT = "DISJOINT"
    TOUCHING = "TOUCHING"
    CONTAINS = "CONTAINS"
    CONTAINED_BY = "CONTAINED_BY"
    EQUAL = "EQUAL"
    OVERLAP = "OVERLAP"


class InclusionSeed(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    UNSEEDED = "UNSEEDED"


class FeatureFractalType(str, Enum):
    TOP = "TOP"
    BOTTOM = "BOTTOM"
    NONE = "NONE"


class DestructionCase(str, Enum):
    NONE = "NONE"
    FIRST_CASE = "FIRST_CASE"
    SECOND_CASE_PENDING = "SECOND_CASE_PENDING"
    SECOND_CASE_CONFIRMED = "SECOND_CASE_CONFIRMED"
    INVALIDATED = "INVALIDATED"


class LifecycleResolution(str, Enum):
    CANDIDATE = "CANDIDATE"
    PROVISIONAL = "PROVISIONAL"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    REPLACED = "REPLACED"


class SequenceBoundaryNature(str, Enum):
    NORMAL = "NORMAL"
    FIRST_CASE_CROSS_BOUNDARY = "FIRST_CASE_CROSS_BOUNDARY"
    SECOND_FEATURE_SEQUENCE = "SECOND_FEATURE_SEQUENCE"


@dataclass(frozen=True)
class PriceInterval:
    low: float
    high: float
    source_stroke_logical_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _is_finite_number(self.low) or not _is_finite_number(self.high):
            raise SegmentRuleContractError("interval bounds must be finite numbers")
        if self.low > self.high:
            raise SegmentRuleContractError("closed interval requires low <= high")
        if type(self.source_stroke_logical_ids) is not tuple or any(
            type(value) is not str for value in self.source_stroke_logical_ids
        ):
            raise SegmentRuleContractError(
                "provenance must be an immutable tuple of logical-ID strings"
            )
        if len(self.source_stroke_logical_ids) != len(
            set(self.source_stroke_logical_ids)
        ):
            raise SegmentRuleContractError("duplicate provenance logical_id")
        if any(not value for value in self.source_stroke_logical_ids):
            raise SegmentRuleContractError("provenance logical_id must be non-empty")


@dataclass(frozen=True)
class StrokeRuleInput:
    logical_id: str
    object_id: str
    direction: SegmentDirection
    low: float
    high: float
    start_bar_index: int
    end_bar_index: int
    sequence_id: str

    def __post_init__(self) -> None:
        if (
            type(self.logical_id) is not str
            or not self.logical_id
            or type(self.object_id) is not str
            or not self.object_id
            or type(self.sequence_id) is not str
            or not self.sequence_id
            or not isinstance(self.direction, SegmentDirection)
        ):
            raise SegmentRuleContractError(
                "stable IDs, sequence_id, and valid direction required"
            )
        if (
            not _is_finite_number(self.low)
            or not _is_finite_number(self.high)
            or type(self.start_bar_index) is not int
            or type(self.end_bar_index) is not int
            or self.low > self.high
            or self.start_bar_index >= self.end_bar_index
        ):
            raise SegmentRuleContractError("invalid stroke interval or bar order")


@dataclass(frozen=True)
class FeatureSequenceResult:
    source_stroke_logical_ids: tuple[str, ...]
    intervals: tuple[PriceInterval, ...]


@dataclass(frozen=True)
class OracleDecision:
    destruction_case: DestructionCase
    reason_code: str
    feature_fractal_type: FeatureFractalType = FeatureFractalType.NONE
    endpoint_price: float | None = None


@dataclass(frozen=True)
class CandidateChoice:
    logical_id: str
    endpoint_bar_index: int
    start_bar_index: int
    mutually_exclusive_group: str

    def __post_init__(self) -> None:
        if (
            type(self.logical_id) is not str
            or not self.logical_id
            or type(self.mutually_exclusive_group) is not str
            or not self.mutually_exclusive_group
        ):
            raise SegmentRuleContractError(
                "candidate logical_id and exclusivity group required"
            )
        if (
            type(self.start_bar_index) is not int
            or type(self.endpoint_bar_index) is not int
            or self.start_bar_index > self.endpoint_bar_index
        ):
            raise SegmentRuleContractError("candidate bar range invalid")


@dataclass(frozen=True)
class CandidateResolution:
    winner_logical_id: str
    invalidated_logical_ids: tuple[str, ...]
    remaining_logical_ids: tuple[str, ...]
    restart_at_bar_index: int


@dataclass(frozen=True)
class InclusionContext:
    first_sequence_id: str
    second_sequence_id: str
    first_candidate_direction: SegmentDirection
    second_candidate_direction: SegmentDirection
    boundary_nature: SequenceBoundaryNature = SequenceBoundaryNature.NORMAL

    def __post_init__(self) -> None:
        if (
            type(self.first_sequence_id) is not str
            or not self.first_sequence_id
            or type(self.second_sequence_id) is not str
            or not self.second_sequence_id
        ):
            raise SegmentRuleContractError("feature sequence IDs required")
        if not isinstance(self.first_candidate_direction, SegmentDirection) or not isinstance(
            self.second_candidate_direction, SegmentDirection
        ):
            raise SegmentRuleContractError("valid candidate directions required")
        if not isinstance(self.boundary_nature, SequenceBoundaryNature):
            raise SegmentRuleContractError("valid sequence boundary nature required")


@dataclass(frozen=True)
class PendingSecondCaseContext:
    primary_case: DestructionCase
    original_direction: SegmentDirection
    sequence_id: str
    pending_endpoint_id: str
    pending_endpoint_source_logical_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.primary_case, DestructionCase)
            or not isinstance(self.original_direction, SegmentDirection)
            or type(self.sequence_id) is not str
            or not self.sequence_id
        ):
            raise SegmentRuleContractError("valid primary case and sequence ID required")
        if (
            type(self.pending_endpoint_id) is not str
            or not self.pending_endpoint_id
            or type(self.pending_endpoint_source_logical_ids) is not tuple
            or not self.pending_endpoint_source_logical_ids
            or any(
                type(value) is not str or not value
                for value in self.pending_endpoint_source_logical_ids
            )
        ):
            raise SegmentRuleContractError("secondary endpoint IDs required")


@dataclass(frozen=True)
class SecondarySequenceContext:
    pending: PendingSecondCaseContext
    normalized: bool
    adjacent_to_pending_endpoint: bool
    element_sequence_ids: tuple[str, str, str]
    normalized_source_logical_ids: tuple[str, ...]
    left_start_endpoint_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.pending, PendingSecondCaseContext):
            raise SegmentRuleContractError("pending second-case context required")
        if type(self.normalized) is not bool or type(self.adjacent_to_pending_endpoint) is not bool:
            raise SegmentRuleContractError("secondary evidence flags must be bool")
        if (
            type(self.element_sequence_ids) is not tuple
            or len(self.element_sequence_ids) != 3
            or any(
                value != self.pending.sequence_id for value in self.element_sequence_ids
            )
        ):
            raise SegmentRuleContractError("SECOND_SEQUENCE_ID_MISMATCH")
        if (
            type(self.normalized_source_logical_ids) is not tuple
            or any(
                type(value) is not str or not value
                for value in self.normalized_source_logical_ids
            )
        ):
            raise SegmentRuleContractError("normalized provenance tuple required")
        if type(self.left_start_endpoint_id) is not str or not self.left_start_endpoint_id:
            raise SegmentRuleContractError("secondary left endpoint ID required")


@dataclass(frozen=True)
class SegmentBoundaryInput:
    logical_id: str
    direction: SegmentDirection
    start_stroke_direction: SegmentDirection
    end_stroke_direction: SegmentDirection
    start_endpoint_id: str
    end_endpoint_id: str

    def __post_init__(self) -> None:
        if (
            type(self.logical_id) is not str
            or not self.logical_id
            or type(self.start_endpoint_id) is not str
            or not self.start_endpoint_id
            or type(self.end_endpoint_id) is not str
            or not self.end_endpoint_id
            or self.start_endpoint_id == self.end_endpoint_id
            or not isinstance(self.direction, SegmentDirection)
            or not isinstance(self.start_stroke_direction, SegmentDirection)
            or not isinstance(self.end_stroke_direction, SegmentDirection)
        ):
            raise SegmentRuleContractError("SG_DIRECTION_OR_ENDPOINT_INVALID")


def _expected_profile() -> dict[str, Any]:
    """Return a fresh profile oracle so callers cannot mutate contract state."""
    return {
    "profile_id": "minimal_segment_canonical_rules_v1",
    "profile_version": "1.0.0",
    "status": "CANONICAL_RULES_ONLY",
    "phase1_profile_id": "minimal_strict_v1",
    "phase1_baseline_commit": "de1b7f589ebe3c2a41fa6501d793200a7b595426",
    "segment_contract_profile_id": "minimal_segment_contract_v1",
    "segment_contract_profile_version": "0.2.0",
    "segment_contract_baseline_commit": "f7eecdd657530f928ffbf869832e76f1dd17b92e",
    "implementation_enabled": False,
    "parser_integration_enabled": False,
    "rules": {
        "feature_sequence": {
            "source": "opposite_direction_strokes",
            "same_sequence_inclusion_only": True,
        },
        "interval": {
            "model": "closed",
            "containment_includes_equality": True,
            "touching_is_gap": False,
            "gap_requires_strict_separation": True,
        },
        "inclusion": {
            "seed_policy": "latest_strict_non_inclusion_pair",
            "unseeded_policy": "defer",
            "equal_boundary_seed": "unresolved",
            "merge_up": "max_high_max_low",
            "merge_down": "min_high_min_low",
            "first_case_cross_boundary_merge": "forbidden",
            "second_sequence_inclusion": "required",
        },
        "fractal": {
            "window_size": 3,
            "comparison": "strict_high_and_low",
            "equal_extrema": "unresolved",
            "up_segment_required_type": "TOP",
            "down_segment_required_type": "BOTTOM",
        },
        "destruction": {
            "pen_break_confirms_segment_break": False,
            "first_case_requires_no_gap": True,
            "second_case_requires_gap": True,
            "second_sequence_fractal_required": True,
            "second_sequence_case_classification_required": False,
            "original_gap_closure_required": False,
            "original_direction_new_extreme_invalidates_pending": True,
        },
        "timing": {
            "confirmation_uses_latest_visible_evidence_bar": True,
            "endpoint_backfill_forbidden": True,
        },
        "arbitration": {"policy": "leftmost_confirmable_endpoint_v1"},
        "lifecycle_mapping": {
            "unconfirmed_failed_candidate": "INVALIDATED",
            "confirmed_segment_superseded_by_reverse": "REPLACED",
            "destroyed_status_allowed": False,
        },
    },
    "prohibited": {
        "segment_engine": True,
        "parser_integration": True,
        "center_or_zhongshu": True,
        "czsc_or_chanpy": True,
        "trading_signal": True,
        "position_or_execution": True,
    },
    }


def validate_segment_canonical_rules_profile(profile: Mapping[str, Any]) -> None:
    """Require the complete v1 profile with no defaults or unknown keys."""
    _validate_exact_mapping(profile, _expected_profile(), "profile")


def _is_finite_number(value: object) -> bool:
    return (
        type(value) in {int, float}
        and math.isfinite(value)
    )


def _validate_exact_mapping(
    actual: Mapping[str, Any], expected: Mapping[str, Any], path: str
) -> None:
    if not isinstance(actual, Mapping):
        raise SegmentRuleContractError(f"{path} must be a mapping")
    missing = set(expected) - set(actual)
    unknown = set(actual) - set(expected)
    if missing or unknown:
        raise SegmentRuleContractError(
            f"{path} keys invalid: missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    for key, expected_value in expected.items():
        actual_value = actual[key]
        child_path = f"{path}.{key}"
        if isinstance(expected_value, Mapping):
            _validate_exact_mapping(actual_value, expected_value, child_path)
            continue
        if type(actual_value) is not type(expected_value) or actual_value != expected_value:
            raise SegmentRuleContractError(
                f"{child_path}={actual_value!r} is unsupported; "
                f"expected {expected_value!r}"
            )


def build_feature_sequence(
    candidate_direction: SegmentDirection,
    strokes: Sequence[StrokeRuleInput],
    *,
    sequence_id: str,
) -> FeatureSequenceResult:
    """Apply FS-001/FS-002 without constructing a segment."""
    if not isinstance(candidate_direction, SegmentDirection):
        raise SegmentRuleContractError("valid candidate direction required")
    if not sequence_id or any(stroke.sequence_id != sequence_id for stroke in strokes):
        raise SegmentRuleContractError("FS_CROSS_SEQUENCE_REJECTED")
    logical_ids = tuple(stroke.logical_id for stroke in strokes)
    if len(logical_ids) != len(set(logical_ids)):
        raise SegmentRuleContractError("FS_DUPLICATE_LOGICAL_ID")
    if any(
        current.start_bar_index < previous.end_bar_index
        for previous, current in zip(strokes, strokes[1:])
    ):
        raise SegmentRuleContractError("FS_BAR_ORDER_INVALID")
    required = (
        SegmentDirection.DOWN
        if candidate_direction == SegmentDirection.UP
        else SegmentDirection.UP
    )
    selected = tuple(stroke for stroke in strokes if stroke.direction == required)
    return FeatureSequenceResult(
        source_stroke_logical_ids=tuple(stroke.logical_id for stroke in selected),
        intervals=tuple(
            PriceInterval(
                stroke.low,
                stroke.high,
                (stroke.logical_id,),
            )
            for stroke in selected
        ),
    )


def classify_interval_relation(
    first: PriceInterval, second: PriceInterval
) -> IntervalRelation:
    if first.low == second.low and first.high == second.high:
        return IntervalRelation.EQUAL
    if first.low <= second.low and first.high >= second.high:
        return IntervalRelation.CONTAINS
    if second.low <= first.low and second.high >= first.high:
        return IntervalRelation.CONTAINED_BY
    if first.high == second.low or second.high == first.low:
        return IntervalRelation.TOUCHING
    if first.high < second.low or second.high < first.low:
        return IntervalRelation.DISJOINT
    return IntervalRelation.OVERLAP


def has_feature_gap(first: PriceInterval, second: PriceInterval) -> bool:
    return (
        first.high < second.low
        or second.high < first.low
    )


def derive_inclusion_seed(
    previous: PriceInterval, current: PriceInterval
) -> InclusionSeed:
    relation = classify_interval_relation(previous, current)
    if relation in {
        IntervalRelation.CONTAINS,
        IntervalRelation.CONTAINED_BY,
        IntervalRelation.EQUAL,
    }:
        return InclusionSeed.UNSEEDED
    if current.high > previous.high and current.low > previous.low:
        return InclusionSeed.UP
    if current.high < previous.high and current.low < previous.low:
        return InclusionSeed.DOWN
    return InclusionSeed.UNSEEDED


def merge_included_intervals(
    first: PriceInterval,
    second: PriceInterval,
    seed: InclusionSeed,
    *,
    context: InclusionContext,
) -> PriceInterval:
    if not isinstance(seed, InclusionSeed):
        raise SegmentRuleContractError("valid inclusion seed required")
    if (
        context.first_sequence_id != context.second_sequence_id
        or context.first_candidate_direction != context.second_candidate_direction
    ):
        raise SegmentRuleContractError("FS_CROSS_SEQUENCE_REJECTED")
    if context.boundary_nature == SequenceBoundaryNature.FIRST_CASE_CROSS_BOUNDARY:
        raise SegmentRuleContractError("HYPOTHETICAL_BOUNDARY_DIFFERENT_NATURE")
    if classify_interval_relation(first, second) not in {
        IntervalRelation.CONTAINS,
        IntervalRelation.CONTAINED_BY,
        IntervalRelation.EQUAL,
    }:
        raise SegmentRuleContractError("FEATURE_INTERVALS_NOT_INCLUDED")
    if seed == InclusionSeed.UNSEEDED:
        raise SegmentRuleContractError("DIRECTION_UNSEEDED")
    if not first.source_stroke_logical_ids or not second.source_stroke_logical_ids:
        raise SegmentRuleContractError("FEATURE_PROVENANCE_REQUIRED")
    overlap = set(first.source_stroke_logical_ids).intersection(
        second.source_stroke_logical_ids
    )
    if overlap:
        raise SegmentRuleContractError("DUPLICATE_FEATURE_PROVENANCE")
    provenance = first.source_stroke_logical_ids + second.source_stroke_logical_ids
    if seed == InclusionSeed.UP:
        return PriceInterval(
            max(first.low, second.low),
            max(first.high, second.high),
            provenance,
        )
    return PriceInterval(
        min(first.low, second.low),
        min(first.high, second.high),
        provenance,
    )


def classify_strict_feature_fractal(
    left: PriceInterval, center: PriceInterval, right: PriceInterval
) -> FeatureFractalType:
    if (
        center.high > left.high
        and center.high > right.high
        and center.low > left.low
        and center.low > right.low
    ):
        return FeatureFractalType.TOP
    if (
        center.high < left.high
        and center.high < right.high
        and center.low < left.low
        and center.low < right.low
    ):
        return FeatureFractalType.BOTTOM
    return FeatureFractalType.NONE


def classify_primary_destruction_case(
    candidate_direction: SegmentDirection,
    left: PriceInterval,
    center: PriceInterval,
    right: PriceInterval,
    *,
    pen_break_observed: bool,
) -> OracleDecision:
    if not isinstance(candidate_direction, SegmentDirection):
        raise SegmentRuleContractError("valid candidate direction required")
    if type(pen_break_observed) is not bool:
        raise SegmentRuleContractError("destruction evidence flags must be bool")
    fractal_type = classify_strict_feature_fractal(left, center, right)
    required = (
        FeatureFractalType.TOP
        if candidate_direction == SegmentDirection.UP
        else FeatureFractalType.BOTTOM
    )
    if fractal_type != required:
        return OracleDecision(
            DestructionCase.NONE,
            "PEN_BREAK_PROVISIONAL" if pen_break_observed else "FEATURE_FRACTAL_REQUIRED",
            fractal_type,
        )
    if has_feature_gap(left, center):
        return OracleDecision(
            DestructionCase.SECOND_CASE_PENDING,
            "SECOND_CASE_GAP_PENDING",
            fractal_type,
            center.high if required == FeatureFractalType.TOP else center.low,
        )
    return OracleDecision(
        DestructionCase.FIRST_CASE,
        "FIRST_CASE_NO_GAP_CONFIRMED",
        fractal_type,
        center.high if required == FeatureFractalType.TOP else center.low,
    )


def classify_secondary_confirmation(
    original_direction: SegmentDirection,
    left: PriceInterval,
    center: PriceInterval,
    right: PriceInterval,
    *,
    context: SecondarySequenceContext,
) -> OracleDecision:
    if not isinstance(original_direction, SegmentDirection):
        raise SegmentRuleContractError("valid original direction required")
    if context.pending.primary_case != DestructionCase.SECOND_CASE_PENDING:
        raise SegmentRuleContractError("SECOND_CASE_PENDING evidence required")
    if context.pending.original_direction != original_direction:
        raise SegmentRuleContractError("SECOND_CASE_DIRECTION_MISMATCH")
    if not context.normalized:
        raise SegmentRuleContractError("SECOND_SEQUENCE_NORMALIZATION_REQUIRED")
    if not context.adjacent_to_pending_endpoint:
        raise SegmentRuleContractError("SECOND_SEQUENCE_ENDPOINT_NOT_ADJACENT")
    if any(not item.source_stroke_logical_ids for item in (left, center, right)):
        raise SegmentRuleContractError("SECOND_SEQUENCE_PROVENANCE_REQUIRED")
    combined_provenance = (
        left.source_stroke_logical_ids
        + center.source_stroke_logical_ids
        + right.source_stroke_logical_ids
    )
    if len(combined_provenance) != len(set(combined_provenance)):
        raise SegmentRuleContractError("DUPLICATE_SECOND_SEQUENCE_PROVENANCE")
    if combined_provenance != context.normalized_source_logical_ids:
        raise SegmentRuleContractError("SECOND_SEQUENCE_PROVENANCE_MISMATCH")
    if context.pending.pending_endpoint_id != context.left_start_endpoint_id:
        raise SegmentRuleContractError("SECOND_SEQUENCE_ENDPOINT_NOT_ADJACENT")
    fractal_type = classify_strict_feature_fractal(left, center, right)
    required = (
        FeatureFractalType.BOTTOM
        if original_direction == SegmentDirection.UP
        else FeatureFractalType.TOP
    )
    if fractal_type != required:
        return OracleDecision(
            DestructionCase.SECOND_CASE_PENDING,
            "SECOND_SEQUENCE_FRACTAL_PENDING",
            fractal_type,
        )
    return OracleDecision(
        DestructionCase.SECOND_CASE_CONFIRMED,
        "SECOND_SEQUENCE_FRACTAL_CONFIRMED",
        fractal_type,
        center.low if required == FeatureFractalType.BOTTOM else center.high,
    )


def classify_pending_second_case_invalidation(
    context: PendingSecondCaseContext,
    *,
    strict_original_direction_new_extreme: bool,
    new_extreme_observed_at_bar: int,
    secondary_confirmed_at_bar: int | None,
) -> OracleDecision:
    """Classify DS-CASE2-FAIL without requiring completed secondary evidence."""
    if context.primary_case != DestructionCase.SECOND_CASE_PENDING:
        raise SegmentRuleContractError("SECOND_CASE_PENDING evidence required")
    if type(strict_original_direction_new_extreme) is not bool:
        raise SegmentRuleContractError("new-extreme flag must be bool")
    if type(new_extreme_observed_at_bar) is not int:
        raise SegmentRuleContractError("new-extreme evidence bar must be int")
    if secondary_confirmed_at_bar is not None and type(secondary_confirmed_at_bar) is not int:
        raise SegmentRuleContractError("secondary confirmation bar must be int or None")
    if not strict_original_direction_new_extreme:
        return OracleDecision(
            DestructionCase.SECOND_CASE_PENDING,
            "SECOND_SEQUENCE_FRACTAL_PENDING",
        )
    if (
        secondary_confirmed_at_bar is not None
        and secondary_confirmed_at_bar <= new_extreme_observed_at_bar
    ):
        raise SegmentRuleContractError("SECOND_CASE_ALREADY_CONFIRMED")
    return OracleDecision(
        DestructionCase.INVALIDATED,
        "PENDING_DESTRUCTION_INVALIDATED",
    )


def choose_deterministic_candidate(
    candidates: Sequence[CandidateChoice],
) -> CandidateResolution:
    if not candidates:
        raise SegmentRuleContractError("at least one confirmable candidate required")
    logical_ids = tuple(item.logical_id for item in candidates)
    if len(logical_ids) != len(set(logical_ids)):
        raise SegmentRuleContractError("duplicate candidate logical_id")
    ordered = sorted(
        candidates,
        key=lambda item: (
            item.endpoint_bar_index,
            item.start_bar_index,
            item.logical_id,
        ),
    )
    winner = ordered[0]
    invalidated = tuple(
        item.logical_id
        for item in ordered[1:]
        if item.mutually_exclusive_group == winner.mutually_exclusive_group
    )
    remaining = tuple(
        item.logical_id
        for item in ordered[1:]
        if item.mutually_exclusive_group != winner.mutually_exclusive_group
    )
    return CandidateResolution(
        winner.logical_id,
        invalidated,
        remaining,
        winner.endpoint_bar_index,
    )


def confirmation_bar(
    endpoint_bar_index: int, right_element_visible_bars: Sequence[int]
) -> int:
    if type(endpoint_bar_index) is not int or any(
        type(item) is not int for item in right_element_visible_bars
    ):
        raise SegmentRuleContractError("confirmation bars must be integers")
    if not right_element_visible_bars:
        raise SegmentRuleContractError("right-element visibility evidence required")
    confirmed_at = max(right_element_visible_bars)
    if confirmed_at < endpoint_bar_index:
        raise SegmentRuleContractError("CONFIRMATION_TIME_BACKFILL_FORBIDDEN")
    return confirmed_at


def resolve_lifecycle(
    *,
    minimum_candidate_window_present: bool,
    previously_confirmed: bool,
    evidence_complete: bool,
    evidence_invalidated: bool,
    reverse_segment_logical_id: str | None = None,
    reverse_segment_confirmed: bool = False,
) -> tuple[LifecycleResolution, str | None]:
    flags = (
        minimum_candidate_window_present,
        previously_confirmed,
        evidence_complete,
        evidence_invalidated,
        reverse_segment_confirmed,
    )
    if any(type(flag) is not bool for flag in flags):
        raise SegmentRuleContractError("lifecycle evidence flags must be bool")
    if evidence_complete and not minimum_candidate_window_present:
        raise SegmentRuleContractError("complete evidence requires candidate window")
    if reverse_segment_confirmed and not reverse_segment_logical_id:
        raise SegmentRuleContractError("confirmed reverse segment logical_id required")
    if previously_confirmed:
        if not minimum_candidate_window_present or not evidence_complete:
            raise SegmentRuleContractError(
                "previously confirmed segment requires complete candidate evidence"
            )
        if evidence_invalidated:
            raise SegmentRuleContractError("confirmed segment cannot be INVALIDATED")
        if reverse_segment_logical_id:
            if not reverse_segment_confirmed:
                raise SegmentRuleContractError(
                    "REPLACED requires a confirmed reverse segment"
                )
            return LifecycleResolution.REPLACED, reverse_segment_logical_id
        return LifecycleResolution.CONFIRMED, None
    if reverse_segment_logical_id:
        raise SegmentRuleContractError("REPLACED requires a previously confirmed segment")
    if evidence_complete and evidence_invalidated:
        raise SegmentRuleContractError("evidence cannot be complete and invalidated")
    if evidence_invalidated:
        return LifecycleResolution.INVALIDATED, None
    if evidence_complete:
        return LifecycleResolution.CONFIRMED, None
    if minimum_candidate_window_present:
        return LifecycleResolution.CANDIDATE, None
    return LifecycleResolution.PROVISIONAL, None


def classify_failed_pen_break(
    *,
    pen_break_observed: bool,
    required_fractal_formed: bool,
    countermove_invalidated: bool,
) -> OracleDecision:
    flags = (pen_break_observed, required_fractal_formed, countermove_invalidated)
    if any(type(flag) is not bool for flag in flags):
        raise SegmentRuleContractError("pen-break evidence flags must be bool")
    if not pen_break_observed:
        return OracleDecision(DestructionCase.NONE, "PEN_BREAK_NOT_OBSERVED")
    if required_fractal_formed:
        raise SegmentRuleContractError("completed fractal is not CASE1 failure")
    if countermove_invalidated:
        return OracleDecision(
            DestructionCase.INVALIDATED,
            "PEN_BREAK_EVIDENCE_INVALIDATED",
        )
    return OracleDecision(DestructionCase.NONE, "PEN_BREAK_PROVISIONAL")


def validate_segment_boundaries(
    current: SegmentBoundaryInput,
    *,
    previous_confirmed: SegmentBoundaryInput | None = None,
    destroyer_direction: SegmentDirection | None = None,
) -> bool:
    for item in (current, previous_confirmed):
        if item is None:
            continue
        if (
            not item.logical_id
            or not item.start_endpoint_id
            or not item.end_endpoint_id
            or not isinstance(item.direction, SegmentDirection)
            or item.start_stroke_direction != item.direction
            or item.end_stroke_direction != item.direction
        ):
            raise SegmentRuleContractError("SG_DIRECTION_OR_ENDPOINT_INVALID")
    if previous_confirmed is not None:
        if previous_confirmed.logical_id == current.logical_id:
            raise SegmentRuleContractError("SG_DUPLICATE_ADJACENT_LOGICAL_ID")
        if previous_confirmed.direction == current.direction:
            raise SegmentRuleContractError("SG_ADJACENT_DIRECTION_NOT_ALTERNATING")
        if previous_confirmed.end_endpoint_id != current.start_endpoint_id:
            raise SegmentRuleContractError("SG_ADJACENT_ENDPOINT_NOT_SHARED")
    if destroyer_direction is not None:
        if not isinstance(destroyer_direction, SegmentDirection):
            raise SegmentRuleContractError("valid destroyer direction required")
        if destroyer_direction == current.direction:
            raise SegmentRuleContractError("SG_SAME_DIRECTION_CANNOT_DESTROY")
    return True


def validate_frozen_prefix_transition(
    *,
    before_prefix_hash: str,
    after_prefix_hash: str,
    before_event_count: int,
    after_event_count: int,
    original_confirmed_at_bar: int,
    revised_confirmed_at_bar: int,
    correction_occurred: bool,
) -> bool:
    if not before_prefix_hash or not after_prefix_hash:
        raise SegmentRuleContractError("frozen-prefix hashes required")
    integers = (
        before_event_count,
        after_event_count,
        original_confirmed_at_bar,
        revised_confirmed_at_bar,
    )
    if any(type(value) is not int or value < 0 for value in integers):
        raise SegmentRuleContractError("frozen-prefix counters must be nonnegative integers")
    if type(correction_occurred) is not bool:
        raise SegmentRuleContractError("correction flag must be bool")
    if before_prefix_hash != after_prefix_hash:
        raise SegmentRuleContractError("FROZEN_PREFIX_REWRITE_FORBIDDEN")
    if after_event_count < before_event_count:
        raise SegmentRuleContractError("LIFECYCLE_EVENT_DELETION_FORBIDDEN")
    if revised_confirmed_at_bar < original_confirmed_at_bar:
        raise SegmentRuleContractError("CONFIRMATION_TIME_BACKFILL_FORBIDDEN")
    if correction_occurred and after_event_count <= before_event_count:
        raise SegmentRuleContractError("CORRECTION_EVENT_APPEND_REQUIRED")
    return True
