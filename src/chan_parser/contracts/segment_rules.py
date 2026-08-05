"""Pure reference oracle for the Phase 2 canonical segment rule contract.

The functions in this module classify immutable inputs. They do not construct
segments, retain history, emit lifecycle events, or call parser engines.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
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


class FeatureIntervalSemantics(str, Enum):
    NORMALIZED_FEATURE_RANGE = "NORMALIZED_FEATURE_RANGE"
    STRUCTURAL_PRICE_RANGE = "STRUCTURAL_PRICE_RANGE"


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
            or self.start_bar_index < 0
            or self.end_bar_index < 0
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
    original_segment_continues: bool = False


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
            or self.start_bar_index < 0
            or self.endpoint_bar_index < 0
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
class FeatureEndpointEvidence:
    endpoint_id: str
    defining_stroke_logical_ids: tuple[str, ...]
    price: float
    bar_index: int

    def __post_init__(self) -> None:
        if (
            type(self.endpoint_id) is not str
            or not self.endpoint_id
            or type(self.defining_stroke_logical_ids) is not tuple
            or not self.defining_stroke_logical_ids
            or any(
                type(value) is not str or not value
                for value in self.defining_stroke_logical_ids
            )
        ):
            raise SegmentRuleContractError("feature endpoint identity required")
        if len(self.defining_stroke_logical_ids) != len(
            set(self.defining_stroke_logical_ids)
        ):
            raise SegmentRuleContractError("duplicate feature endpoint provenance")
        if not _is_finite_number(self.price):
            raise SegmentRuleContractError("feature endpoint price must be finite")
        if type(self.bar_index) is not int or self.bar_index < 0:
            raise SegmentRuleContractError(
                "feature endpoint bar must be a nonnegative integer"
            )


@dataclass(frozen=True)
class FeatureElementRuleInput:
    logical_id: str
    sequence_id: str
    direction: SegmentDirection
    interval_semantics: FeatureIntervalSemantics
    start_endpoint: FeatureEndpointEvidence
    end_endpoint: FeatureEndpointEvidence
    high_endpoint: FeatureEndpointEvidence
    low_endpoint: FeatureEndpointEvidence
    interval: PriceInterval
    normalized: bool
    visible_at_bar_index: int

    def __post_init__(self) -> None:
        identifiers = (self.logical_id, self.sequence_id)
        if any(type(value) is not str or not value for value in identifiers):
            raise SegmentRuleContractError("feature element stable IDs required")
        if not isinstance(self.direction, SegmentDirection):
            raise SegmentRuleContractError("feature element direction required")
        if not isinstance(self.interval_semantics, FeatureIntervalSemantics):
            raise SegmentRuleContractError("feature interval semantics required")
        endpoints = (
            self.start_endpoint,
            self.end_endpoint,
            self.high_endpoint,
            self.low_endpoint,
        )
        if any(not isinstance(item, FeatureEndpointEvidence) for item in endpoints):
            raise SegmentRuleContractError("feature endpoint evidence required")
        endpoint_registry: dict[str, FeatureEndpointEvidence] = {}
        for item in endpoints:
            existing = endpoint_registry.get(item.endpoint_id)
            if existing is not None and existing != item:
                raise SegmentRuleContractError(
                    "feature endpoint identity evidence mismatch"
                )
            endpoint_registry[item.endpoint_id] = item
        if self.start_endpoint.endpoint_id == self.end_endpoint.endpoint_id:
            raise SegmentRuleContractError("feature element endpoints must differ")
        if self.start_endpoint.bar_index >= self.end_endpoint.bar_index:
            raise SegmentRuleContractError("feature element bar order invalid")
        if not isinstance(self.interval, PriceInterval):
            raise SegmentRuleContractError("feature element interval required")
        if not self.interval.source_stroke_logical_ids:
            raise SegmentRuleContractError("feature element provenance required")
        if self.high_endpoint.price != self.interval.high:
            raise SegmentRuleContractError("feature high endpoint price mismatch")
        if self.low_endpoint.price != self.interval.low:
            raise SegmentRuleContractError("feature low endpoint price mismatch")
        if self.interval_semantics == FeatureIntervalSemantics.STRUCTURAL_PRICE_RANGE:
            if not (
                self.interval.low
                <= self.start_endpoint.price
                <= self.interval.high
                and self.interval.low
                <= self.end_endpoint.price
                <= self.interval.high
            ):
                raise SegmentRuleContractError(
                    "structural endpoint price outside feature interval"
                )
        if not (
            self.start_endpoint.bar_index
            <= self.high_endpoint.bar_index
            <= self.end_endpoint.bar_index
        ):
            raise SegmentRuleContractError("feature high endpoint bar mismatch")
        if not (
            self.start_endpoint.bar_index
            <= self.low_endpoint.bar_index
            <= self.end_endpoint.bar_index
        ):
            raise SegmentRuleContractError("feature low endpoint bar mismatch")
        provenance = set(self.interval.source_stroke_logical_ids)
        if not set(self.high_endpoint.defining_stroke_logical_ids).issubset(provenance):
            raise SegmentRuleContractError("feature high endpoint provenance mismatch")
        if not set(self.low_endpoint.defining_stroke_logical_ids).issubset(provenance):
            raise SegmentRuleContractError("feature low endpoint provenance mismatch")
        if (
            self.direction == SegmentDirection.UP
            and self.start_endpoint.price >= self.end_endpoint.price
        ) or (
            self.direction == SegmentDirection.DOWN
            and self.start_endpoint.price <= self.end_endpoint.price
        ):
            raise SegmentRuleContractError("feature endpoint direction mismatch")
        if type(self.normalized) is not bool:
            raise SegmentRuleContractError("feature normalized flag must be bool")
        if (
            type(self.visible_at_bar_index) is not int
            or self.visible_at_bar_index
            < max(
                self.end_endpoint.bar_index,
                self.high_endpoint.bar_index,
                self.low_endpoint.bar_index,
            )
        ):
            raise SegmentRuleContractError("feature visibility bar invalid")


@dataclass(frozen=True)
class PrimarySequenceContext:
    candidate_direction: SegmentDirection
    sequence_id: str
    normalized_source_logical_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.candidate_direction, SegmentDirection)
            or type(self.sequence_id) is not str
            or not self.sequence_id
        ):
            raise SegmentRuleContractError(
                "valid primary direction and sequence ID required"
            )
        _validate_normalized_provenance(self.normalized_source_logical_ids)


@dataclass(frozen=True)
class PrimaryDestructionEvidence:
    evidence_key: str
    destruction_case: DestructionCase
    reason_code: str
    candidate_direction: SegmentDirection
    primary_sequence_id: str
    primary_element_logical_ids: tuple[str, str, str]
    feature_fractal_type: FeatureFractalType
    endpoint: FeatureEndpointEvidence | None

    def __post_init__(self) -> None:
        if (
            type(self.evidence_key) is not str
            or not self.evidence_key
            or not isinstance(self.destruction_case, DestructionCase)
            or type(self.reason_code) is not str
            or not self.reason_code
            or not isinstance(self.candidate_direction, SegmentDirection)
            or type(self.primary_sequence_id) is not str
            or not self.primary_sequence_id
            or type(self.primary_element_logical_ids) is not tuple
            or len(self.primary_element_logical_ids) != 3
            or any(
                type(value) is not str or not value
                for value in self.primary_element_logical_ids
            )
            or len(set(self.primary_element_logical_ids)) != 3
            or not isinstance(self.feature_fractal_type, FeatureFractalType)
        ):
            raise SegmentRuleContractError("malformed primary destruction evidence")
        if self.destruction_case in {
            DestructionCase.FIRST_CASE,
            DestructionCase.SECOND_CASE_PENDING,
        }:
            if not isinstance(self.endpoint, FeatureEndpointEvidence):
                raise SegmentRuleContractError("primary endpoint evidence required")
        elif self.endpoint is not None:
            raise SegmentRuleContractError("primary NONE evidence forbids endpoint")
        if self.evidence_key != _primary_evidence_key(
            self.destruction_case,
            self.candidate_direction,
            self.primary_sequence_id,
            self.primary_element_logical_ids,
            self.feature_fractal_type,
            self.endpoint,
        ):
            raise SegmentRuleContractError("primary evidence key mismatch")


@dataclass(frozen=True)
class PendingSecondCaseContext:
    primary_evidence: PrimaryDestructionEvidence
    secondary_sequence_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.primary_evidence, PrimaryDestructionEvidence):
            raise SegmentRuleContractError("primary destruction evidence required")
        if (
            self.primary_evidence.destruction_case
            != DestructionCase.SECOND_CASE_PENDING
            or self.primary_evidence.endpoint is None
        ):
            raise SegmentRuleContractError(
                "SECOND_CASE_PENDING primary evidence required"
            )
        if (
            type(self.secondary_sequence_id) is not str
            or not self.secondary_sequence_id
        ):
            raise SegmentRuleContractError("secondary sequence ID required")

    @property
    def original_direction(self) -> SegmentDirection:
        return self.primary_evidence.candidate_direction

    @property
    def pending_endpoint(self) -> FeatureEndpointEvidence:
        endpoint = self.primary_evidence.endpoint
        if endpoint is None:
            raise SegmentRuleContractError("primary endpoint evidence required")
        return endpoint


@dataclass(frozen=True)
class OriginalDirectionExtremeEvidence:
    primary_evidence_key: str
    pending_endpoint_id: str
    observed_extreme_price: float
    observed_at_bar_index: int
    observed_source_stroke_logical_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            type(self.primary_evidence_key) is not str
            or not self.primary_evidence_key
            or type(self.pending_endpoint_id) is not str
            or not self.pending_endpoint_id
        ):
            raise SegmentRuleContractError("extreme evidence binding required")
        _validate_evidence_provenance(
            self.observed_source_stroke_logical_ids,
            "observed extreme",
        )
        if not _is_finite_number(self.observed_extreme_price):
            raise SegmentRuleContractError("extreme evidence prices must be finite")
        if (
            type(self.observed_at_bar_index) is not int
            or self.observed_at_bar_index < 0
        ):
            raise SegmentRuleContractError(
                "extreme evidence bars must be nonnegative integers"
            )


@dataclass(frozen=True)
class SecondaryConfirmationEvidence:
    evidence_key: str
    primary_evidence_key: str
    pending_endpoint_id: str
    secondary_sequence_id: str
    feature_element_logical_ids: tuple[str, str, str]
    feature_elements: tuple[
        FeatureElementRuleInput,
        FeatureElementRuleInput,
        FeatureElementRuleInput,
    ]
    normalized_source_logical_ids: tuple[str, ...]
    feature_fractal_type: FeatureFractalType
    confirmed_at_bar: int

    def __post_init__(self) -> None:
        if (
            type(self.evidence_key) is not str
            or not self.evidence_key
            or type(self.primary_evidence_key) is not str
            or not self.primary_evidence_key
            or type(self.pending_endpoint_id) is not str
            or not self.pending_endpoint_id
            or type(self.secondary_sequence_id) is not str
            or not self.secondary_sequence_id
            or type(self.feature_element_logical_ids) is not tuple
            or len(self.feature_element_logical_ids) != 3
            or any(
                type(value) is not str or not value
                for value in self.feature_element_logical_ids
            )
            or len(set(self.feature_element_logical_ids)) != 3
            or type(self.feature_elements) is not tuple
            or len(self.feature_elements) != 3
            or any(
                not isinstance(value, FeatureElementRuleInput)
                for value in self.feature_elements
            )
            or not isinstance(self.feature_fractal_type, FeatureFractalType)
            or type(self.confirmed_at_bar) is not int
            or self.confirmed_at_bar < 0
        ):
            raise SegmentRuleContractError(
                "malformed secondary confirmation evidence"
            )
        if tuple(
            item.logical_id for item in self.feature_elements
        ) != self.feature_element_logical_ids:
            raise SegmentRuleContractError(
                "secondary confirmation element identity mismatch"
            )
        _validate_normalized_provenance(self.normalized_source_logical_ids)
        embedded_provenance = tuple(
            source
            for item in self.feature_elements
            for source in item.interval.source_stroke_logical_ids
        )
        if embedded_provenance != self.normalized_source_logical_ids:
            raise SegmentRuleContractError(
                "secondary confirmation normalized provenance mismatch"
            )
        if self.confirmed_at_bar != self.feature_elements[2].visible_at_bar_index:
            raise SegmentRuleContractError(
                "secondary confirmation time not derived from right element"
            )
        if self.evidence_key != _secondary_evidence_key(
            self.primary_evidence_key,
            self.pending_endpoint_id,
            self.secondary_sequence_id,
            self.feature_elements,
            self.normalized_source_logical_ids,
            self.feature_fractal_type,
            self.confirmed_at_bar,
        ):
            raise SegmentRuleContractError("secondary evidence key mismatch")


@dataclass(frozen=True)
class SecondarySequenceContext:
    pending: PendingSecondCaseContext
    normalized_source_logical_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.pending, PendingSecondCaseContext):
            raise SegmentRuleContractError("pending second-case context required")
        _validate_normalized_provenance(self.normalized_source_logical_ids)


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
    if type(value) is int:
        return True
    if type(value) is float:
        return math.isfinite(value)
    return False


def _validate_evidence_provenance(value: object, label: str) -> None:
    if (
        type(value) is not tuple
        or not value
        or any(type(item) is not str or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise SegmentRuleContractError(f"{label} provenance must be unique")


def _endpoint_payload(
    endpoint: FeatureEndpointEvidence | None,
) -> dict[str, object] | None:
    if endpoint is None:
        return None
    return {
        "endpoint_id": endpoint.endpoint_id,
        "price": endpoint.price,
        "bar_index": endpoint.bar_index,
        "defining_stroke_logical_ids": endpoint.defining_stroke_logical_ids,
    }


def _feature_element_payload(element: FeatureElementRuleInput) -> dict[str, object]:
    """Return the deterministic, complete evidence payload for one element."""
    return {
        "logical_id": element.logical_id,
        "sequence_id": element.sequence_id,
        "direction": element.direction.value,
        "interval_semantics": element.interval_semantics.value,
        "start_endpoint": _endpoint_payload(element.start_endpoint),
        "end_endpoint": _endpoint_payload(element.end_endpoint),
        "high_endpoint": _endpoint_payload(element.high_endpoint),
        "low_endpoint": _endpoint_payload(element.low_endpoint),
        "interval": {
            "low": element.interval.low,
            "high": element.interval.high,
            "source_stroke_logical_ids": element.interval.source_stroke_logical_ids,
        },
        "normalized": element.normalized,
        "visible_at_bar_index": element.visible_at_bar_index,
    }


def _stable_evidence_key(kind: str, payload: Mapping[str, object]) -> str:
    canonical = json.dumps(
        {"kind": kind, **payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _primary_evidence_key(
    destruction_case: DestructionCase,
    candidate_direction: SegmentDirection,
    sequence_id: str,
    element_ids: tuple[str, str, str],
    fractal_type: FeatureFractalType,
    endpoint: FeatureEndpointEvidence | None,
) -> str:
    return _stable_evidence_key(
        "PRIMARY_DESTRUCTION_V1",
        {
            "case": destruction_case.value,
            "candidate_direction": candidate_direction.value,
            "sequence_id": sequence_id,
            "element_ids": element_ids,
            "fractal_type": fractal_type.value,
            "endpoint": _endpoint_payload(endpoint),
            "rule_version": "minimal_segment_canonical_rules_v1",
        },
    )


def _secondary_evidence_key(
    primary_evidence_key: str,
    pending_endpoint_id: str,
    sequence_id: str,
    elements: tuple[
        FeatureElementRuleInput,
        FeatureElementRuleInput,
        FeatureElementRuleInput,
    ],
    normalized_source_logical_ids: tuple[str, ...],
    fractal_type: FeatureFractalType,
    confirmed_at_bar: int,
) -> str:
    return _stable_evidence_key(
        "SECONDARY_CONFIRMATION_V1",
        {
            "primary_evidence_key": primary_evidence_key,
            "pending_endpoint_id": pending_endpoint_id,
            "sequence_id": sequence_id,
            "elements": tuple(_feature_element_payload(item) for item in elements),
            "normalized_source_logical_ids": normalized_source_logical_ids,
            "fractal_type": fractal_type.value,
            "confirmed_at_bar": confirmed_at_bar,
            "rule_version": "minimal_segment_canonical_rules_v1",
        },
    )


def _validate_normalized_provenance(value: object) -> None:
    if (
        type(value) is not tuple
        or not value
        or any(type(item) is not str or not item for item in value)
    ):
        raise SegmentRuleContractError("normalized provenance tuple required")
    if len(value) != len(set(value)):
        raise SegmentRuleContractError("duplicate normalized provenance")


def _require_price_intervals(*values: object) -> None:
    if any(not isinstance(value, PriceInterval) for value in values):
        raise SegmentRuleContractError("PriceInterval values required")


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
    if not isinstance(strokes, Sequence) or any(
        not isinstance(stroke, StrokeRuleInput) for stroke in strokes
    ):
        raise SegmentRuleContractError("strokes must contain StrokeRuleInput values")
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
    _require_price_intervals(first, second)
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
    _require_price_intervals(first, second)
    return (
        first.high < second.low
        or second.high < first.low
    )


def derive_inclusion_seed(
    previous: PriceInterval, current: PriceInterval
) -> InclusionSeed:
    _require_price_intervals(previous, current)
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
    if not isinstance(first, PriceInterval) or not isinstance(second, PriceInterval):
        raise SegmentRuleContractError("PriceInterval values required")
    if not isinstance(context, InclusionContext):
        raise SegmentRuleContractError("InclusionContext required")
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
    _require_price_intervals(left, center, right)
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
    left: FeatureElementRuleInput,
    center: FeatureElementRuleInput,
    right: FeatureElementRuleInput,
    *,
    context: PrimarySequenceContext,
) -> PrimaryDestructionEvidence:
    if not isinstance(context, PrimarySequenceContext):
        raise SegmentRuleContractError("primary sequence context required")
    intervals = _validate_standard_element_window(
        left,
        center,
        right,
        sequence_id=context.sequence_id,
        normalized_source_logical_ids=context.normalized_source_logical_ids,
        reason_prefix="PRIMARY",
        required_direction=(
            SegmentDirection.DOWN
            if context.candidate_direction == SegmentDirection.UP
            else SegmentDirection.UP
        ),
    )
    fractal_type = classify_strict_feature_fractal(*intervals)
    required = (
        FeatureFractalType.TOP
        if context.candidate_direction == SegmentDirection.UP
        else FeatureFractalType.BOTTOM
    )
    if fractal_type != required:
        return _make_primary_evidence(
            context,
            (left, center, right),
            DestructionCase.NONE,
            "FEATURE_FRACTAL_REQUIRED",
            fractal_type,
            None,
        )
    endpoint = (
        center.high_endpoint
        if required == FeatureFractalType.TOP
        else center.low_endpoint
    )
    if has_feature_gap(left.interval, center.interval):
        return _make_primary_evidence(
            context,
            (left, center, right),
            DestructionCase.SECOND_CASE_PENDING,
            "SECOND_CASE_GAP_PENDING",
            fractal_type,
            endpoint,
        )
    return _make_primary_evidence(
        context,
        (left, center, right),
        DestructionCase.FIRST_CASE,
        "FIRST_CASE_NO_GAP_CONFIRMED",
        fractal_type,
        endpoint,
    )


def _make_primary_evidence(
    context: PrimarySequenceContext,
    elements: tuple[
        FeatureElementRuleInput,
        FeatureElementRuleInput,
        FeatureElementRuleInput,
    ],
    destruction_case: DestructionCase,
    reason_code: str,
    fractal_type: FeatureFractalType,
    endpoint: FeatureEndpointEvidence | None,
) -> PrimaryDestructionEvidence:
    element_ids = tuple(item.logical_id for item in elements)
    key = _primary_evidence_key(
        destruction_case,
        context.candidate_direction,
        context.sequence_id,
        element_ids,
        fractal_type,
        endpoint,
    )
    return PrimaryDestructionEvidence(
        key,
        destruction_case,
        reason_code,
        context.candidate_direction,
        context.sequence_id,
        element_ids,
        fractal_type,
        endpoint,
    )


def build_pending_second_case_context(
    primary_evidence: PrimaryDestructionEvidence,
    *,
    secondary_sequence_id: str,
) -> PendingSecondCaseContext:
    return PendingSecondCaseContext(primary_evidence, secondary_sequence_id)


def classify_secondary_confirmation(
    left: FeatureElementRuleInput,
    center: FeatureElementRuleInput,
    right: FeatureElementRuleInput,
    *,
    context: SecondarySequenceContext,
) -> SecondaryConfirmationEvidence | OracleDecision:
    if not isinstance(context, SecondarySequenceContext):
        raise SegmentRuleContractError("secondary sequence context required")
    original_direction = context.pending.original_direction
    intervals = _validate_standard_element_window(
        left,
        center,
        right,
        sequence_id=context.pending.secondary_sequence_id,
        normalized_source_logical_ids=context.normalized_source_logical_ids,
        reason_prefix="SECOND_SEQUENCE",
        required_direction=original_direction,
    )
    if left.start_endpoint != context.pending.pending_endpoint:
        raise SegmentRuleContractError("SECOND_SEQUENCE_ENDPOINT_NOT_ADJACENT")
    fractal_type = classify_strict_feature_fractal(*intervals)
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
    element_ids = tuple(item.logical_id for item in (left, center, right))
    confirmed_at_bar = right.visible_at_bar_index
    key = _secondary_evidence_key(
        context.pending.primary_evidence.evidence_key,
        context.pending.pending_endpoint.endpoint_id,
        context.pending.secondary_sequence_id,
        (left, center, right),
        context.normalized_source_logical_ids,
        fractal_type,
        confirmed_at_bar,
    )
    return SecondaryConfirmationEvidence(
        key,
        context.pending.primary_evidence.evidence_key,
        context.pending.pending_endpoint.endpoint_id,
        context.pending.secondary_sequence_id,
        element_ids,
        (left, center, right),
        context.normalized_source_logical_ids,
        fractal_type,
        confirmed_at_bar,
    )


def classify_pending_second_case_invalidation(
    context: PendingSecondCaseContext,
    *,
    extreme_evidence: OriginalDirectionExtremeEvidence,
) -> OracleDecision:
    return resolve_second_case_outcome(
        context,
        secondary_confirmation=None,
        extreme_evidence=extreme_evidence,
    )


def resolve_second_case_outcome(
    context: PendingSecondCaseContext,
    *,
    secondary_confirmation: SecondaryConfirmationEvidence | None,
    extreme_evidence: OriginalDirectionExtremeEvidence | None,
) -> OracleDecision:
    if not isinstance(context, PendingSecondCaseContext):
        raise SegmentRuleContractError("pending second-case context required")
    primary_key = context.primary_evidence.evidence_key
    endpoint = context.pending_endpoint
    if secondary_confirmation is not None:
        if not isinstance(secondary_confirmation, SecondaryConfirmationEvidence):
            raise SegmentRuleContractError(
                "secondary confirmation evidence required"
            )
        if secondary_confirmation.primary_evidence_key != primary_key:
            raise SegmentRuleContractError("SECONDARY_PRIMARY_EVIDENCE_KEY_MISMATCH")
        if secondary_confirmation.pending_endpoint_id != endpoint.endpoint_id:
            raise SegmentRuleContractError("SECONDARY_PENDING_ENDPOINT_MISMATCH")
        if (
            secondary_confirmation.secondary_sequence_id
            != context.secondary_sequence_id
        ):
            raise SegmentRuleContractError("SECONDARY_SEQUENCE_ID_MISMATCH")
        required_secondary_fractal = (
            FeatureFractalType.BOTTOM
            if context.original_direction == SegmentDirection.UP
            else FeatureFractalType.TOP
        )
        if (
            secondary_confirmation.feature_fractal_type
            != required_secondary_fractal
        ):
            raise SegmentRuleContractError(
                "SECONDARY_CONFIRMATION_FRACTAL_MISMATCH"
            )
        if secondary_confirmation.confirmed_at_bar < endpoint.bar_index:
            raise SegmentRuleContractError(
                "secondary confirmation precedes pending endpoint"
            )
        _validate_standard_element_window(
            *secondary_confirmation.feature_elements,
            sequence_id=context.secondary_sequence_id,
            normalized_source_logical_ids=(
                secondary_confirmation.normalized_source_logical_ids
            ),
            reason_prefix="SECOND_SEQUENCE",
            required_direction=context.original_direction,
        )
        if secondary_confirmation.feature_elements[0].start_endpoint != endpoint:
            raise SegmentRuleContractError(
                "SECOND_SEQUENCE_ENDPOINT_NOT_ADJACENT"
            )
        actual_fractal = classify_strict_feature_fractal(
            *tuple(
                item.interval
                for item in secondary_confirmation.feature_elements
            )
        )
        if actual_fractal != secondary_confirmation.feature_fractal_type:
            raise SegmentRuleContractError(
                "secondary confirmation fractal evidence mismatch"
            )
    strict_extreme = False
    if extreme_evidence is not None:
        if not isinstance(extreme_evidence, OriginalDirectionExtremeEvidence):
            raise SegmentRuleContractError("original-direction extreme evidence required")
        if extreme_evidence.primary_evidence_key != primary_key:
            raise SegmentRuleContractError("EXTREME_PRIMARY_EVIDENCE_KEY_MISMATCH")
        if extreme_evidence.pending_endpoint_id != endpoint.endpoint_id:
            raise SegmentRuleContractError("EXTREME_PENDING_ENDPOINT_MISMATCH")
        if extreme_evidence.observed_at_bar_index <= endpoint.bar_index:
            raise SegmentRuleContractError(
                "extreme evidence must follow pending endpoint"
            )
        strict_extreme = (
            extreme_evidence.observed_extreme_price > endpoint.price
            if context.original_direction == SegmentDirection.UP
            else extreme_evidence.observed_extreme_price < endpoint.price
        )
    if secondary_confirmation is not None and (
        not strict_extreme
        or extreme_evidence is None
        or secondary_confirmation.confirmed_at_bar
        <= extreme_evidence.observed_at_bar_index
    ):
        return OracleDecision(
            DestructionCase.SECOND_CASE_CONFIRMED,
            "SECOND_SEQUENCE_FRACTAL_CONFIRMED",
            secondary_confirmation.feature_fractal_type,
        )
    if strict_extreme:
        return OracleDecision(
            DestructionCase.INVALIDATED,
            "PENDING_DESTRUCTION_INVALIDATED",
            original_segment_continues=True,
        )
    return OracleDecision(
        DestructionCase.SECOND_CASE_PENDING,
        "ORIGINAL_DIRECTION_EXTREME_NOT_STRICT"
        if extreme_evidence is not None
        else "SECOND_CASE_EVIDENCE_PENDING",
    )


def resolve_second_case_evidence_sequence(
    context: PendingSecondCaseContext,
    evidence_sequence: Sequence[
        SecondaryConfirmationEvidence | OriginalDirectionExtremeEvidence
    ],
) -> OracleDecision:
    """Normalize evidence arrival order before the sole time arbiter runs."""
    if not isinstance(context, PendingSecondCaseContext):
        raise SegmentRuleContractError("pending second-case context required")
    if type(evidence_sequence) is not tuple:
        raise SegmentRuleContractError("evidence sequence must be an immutable tuple")
    secondary: SecondaryConfirmationEvidence | None = None
    extreme: OriginalDirectionExtremeEvidence | None = None
    for item in evidence_sequence:
        if isinstance(item, SecondaryConfirmationEvidence):
            if secondary is not None:
                raise SegmentRuleContractError(
                    "duplicate secondary confirmation evidence"
                )
            secondary = item
        elif isinstance(item, OriginalDirectionExtremeEvidence):
            if extreme is not None:
                raise SegmentRuleContractError(
                    "duplicate original-direction extreme evidence"
                )
            extreme = item
        else:
            raise SegmentRuleContractError("unsupported second-case evidence")
    return resolve_second_case_outcome(
        context,
        secondary_confirmation=secondary,
        extreme_evidence=extreme,
    )


def _validate_standard_element_window(
    left: FeatureElementRuleInput,
    center: FeatureElementRuleInput,
    right: FeatureElementRuleInput,
    *,
    sequence_id: str,
    normalized_source_logical_ids: tuple[str, ...],
    reason_prefix: str,
    required_direction: SegmentDirection,
) -> tuple[PriceInterval, PriceInterval, PriceInterval]:
    elements = (left, center, right)
    if any(not isinstance(item, FeatureElementRuleInput) for item in elements):
        raise SegmentRuleContractError("three feature elements required")
    logical_ids = tuple(item.logical_id for item in elements)
    if len(logical_ids) != len(set(logical_ids)):
        raise SegmentRuleContractError(f"DUPLICATE_{reason_prefix}_ELEMENT_ID")
    if any(item.sequence_id != sequence_id for item in elements):
        raise SegmentRuleContractError(f"{reason_prefix}_ID_MISMATCH")
    if any(item.direction != required_direction for item in elements):
        raise SegmentRuleContractError(f"{reason_prefix}_DIRECTION_MISMATCH")
    if any(not item.normalized for item in elements):
        raise SegmentRuleContractError(f"{reason_prefix}_NORMALIZATION_REQUIRED")
    endpoint_registry: dict[str, FeatureEndpointEvidence] = {}
    for item in elements:
        for evidence in (
            item.start_endpoint,
            item.end_endpoint,
            item.high_endpoint,
            item.low_endpoint,
        ):
            existing = endpoint_registry.get(evidence.endpoint_id)
            if existing is not None and existing != evidence:
                raise SegmentRuleContractError(
                    f"{reason_prefix}_ENDPOINT_IDENTITY_MISMATCH"
                )
            endpoint_registry[evidence.endpoint_id] = evidence
    if left.end_endpoint != center.start_endpoint:
        raise SegmentRuleContractError(f"{reason_prefix}_LEFT_CENTER_DISCONNECTED")
    if center.end_endpoint != right.start_endpoint:
        raise SegmentRuleContractError(f"{reason_prefix}_CENTER_RIGHT_DISCONNECTED")
    intervals = tuple(item.interval for item in elements)
    if any(not item.source_stroke_logical_ids for item in intervals):
        raise SegmentRuleContractError(f"{reason_prefix}_PROVENANCE_REQUIRED")
    combined_provenance = tuple(
        source
        for item in intervals
        for source in item.source_stroke_logical_ids
    )
    if len(combined_provenance) != len(set(combined_provenance)):
        raise SegmentRuleContractError(f"DUPLICATE_{reason_prefix}_PROVENANCE")
    if combined_provenance != normalized_source_logical_ids:
        raise SegmentRuleContractError(f"{reason_prefix}_PROVENANCE_MISMATCH")
    return intervals


def choose_deterministic_candidate(
    candidates: Sequence[CandidateChoice],
) -> CandidateResolution:
    if not isinstance(candidates, Sequence) or not candidates:
        raise SegmentRuleContractError("at least one confirmable candidate required")
    if any(not isinstance(item, CandidateChoice) for item in candidates):
        raise SegmentRuleContractError(
            "candidates must contain CandidateChoice values"
        )
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
    if (
        not isinstance(right_element_visible_bars, Sequence)
        or isinstance(right_element_visible_bars, (str, bytes, bytearray))
    ):
        raise SegmentRuleContractError(
            "right-element visibility must be a non-string Sequence"
        )
    if type(endpoint_bar_index) is not int or any(
        type(item) is not int for item in right_element_visible_bars
    ):
        raise SegmentRuleContractError("confirmation bars must be integers")
    if not right_element_visible_bars:
        raise SegmentRuleContractError("right-element visibility evidence required")
    if endpoint_bar_index < 0 or any(
        item < 0 for item in right_element_visible_bars
    ):
        raise SegmentRuleContractError("confirmation bars must be nonnegative")
    confirmed_at = max(right_element_visible_bars)
    if confirmed_at < endpoint_bar_index:
        raise SegmentRuleContractError("CONFIRMATION_TIME_BACKFILL_FORBIDDEN")
    return confirmed_at


def resolve_lifecycle(
    *,
    minimum_candidate_window_present: bool,
    provisional_evidence_present: bool,
    previously_confirmed: bool,
    evidence_complete: bool,
    evidence_invalidated: bool,
    reverse_segment_logical_id: str | None = None,
    reverse_segment_confirmed: bool = False,
) -> tuple[LifecycleResolution, str | None]:
    flags = (
        minimum_candidate_window_present,
        provisional_evidence_present,
        previously_confirmed,
        evidence_complete,
        evidence_invalidated,
        reverse_segment_confirmed,
    )
    if any(type(flag) is not bool for flag in flags):
        raise SegmentRuleContractError("lifecycle evidence flags must be bool")
    if reverse_segment_logical_id is not None and (
        type(reverse_segment_logical_id) is not str
        or not reverse_segment_logical_id
    ):
        raise SegmentRuleContractError(
            "reverse segment logical_id must be a nonempty string"
        )
    if reverse_segment_logical_id is not None and not reverse_segment_confirmed:
        raise SegmentRuleContractError(
            "reverse segment logical_id requires confirmed reverse segment"
        )
    any_evidence = (
        provisional_evidence_present
        or evidence_complete
        or evidence_invalidated
        or previously_confirmed
        or reverse_segment_confirmed
        or reverse_segment_logical_id is not None
    )
    if not minimum_candidate_window_present:
        reason = (
            "LIFECYCLE_EVIDENCE_WITHOUT_CANDIDATE"
            if any_evidence
            else "NO_CANDIDATE_LIFECYCLE_FORBIDDEN"
        )
        raise SegmentRuleContractError(reason)
    if provisional_evidence_present and evidence_complete:
        raise SegmentRuleContractError(
            "provisional and complete evidence are contradictory"
        )
    if evidence_complete and evidence_invalidated:
        raise SegmentRuleContractError(
            "complete and invalidated evidence are contradictory"
        )
    if provisional_evidence_present and evidence_invalidated:
        raise SegmentRuleContractError(
            "provisional and invalidated evidence are contradictory"
        )
    if reverse_segment_confirmed and not reverse_segment_logical_id:
        raise SegmentRuleContractError("confirmed reverse segment logical_id required")
    if previously_confirmed:
        if not evidence_complete or provisional_evidence_present:
            raise SegmentRuleContractError(
                "previously confirmed segment requires complete candidate evidence"
            )
        if evidence_invalidated:
            raise SegmentRuleContractError("confirmed segment cannot be INVALIDATED")
        if reverse_segment_logical_id is not None:
            return LifecycleResolution.REPLACED, reverse_segment_logical_id
        return LifecycleResolution.CONFIRMED, None
    if reverse_segment_logical_id is not None:
        raise SegmentRuleContractError("REPLACED requires a previously confirmed segment")
    if evidence_invalidated:
        return LifecycleResolution.INVALIDATED, None
    if evidence_complete:
        return LifecycleResolution.CONFIRMED, None
    if provisional_evidence_present:
        return LifecycleResolution.PROVISIONAL, None
    return LifecycleResolution.CANDIDATE, None


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
            original_segment_continues=True,
        )
    return OracleDecision(DestructionCase.NONE, "PEN_BREAK_PROVISIONAL")


def validate_segment_boundaries(
    current: SegmentBoundaryInput,
    *,
    previous_confirmed: SegmentBoundaryInput | None = None,
    destroyer_direction: SegmentDirection | None = None,
) -> bool:
    if not isinstance(current, SegmentBoundaryInput):
        raise SegmentRuleContractError("current SegmentBoundaryInput required")
    if previous_confirmed is not None and not isinstance(
        previous_confirmed, SegmentBoundaryInput
    ):
        raise SegmentRuleContractError(
            "previous_confirmed SegmentBoundaryInput required"
        )
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
    if (
        type(before_prefix_hash) is not str
        or not before_prefix_hash
        or type(after_prefix_hash) is not str
        or not after_prefix_hash
    ):
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
    confirmation_time_changed = (
        revised_confirmed_at_bar != original_confirmed_at_bar
    )
    if confirmation_time_changed and not correction_occurred:
        raise SegmentRuleContractError("CORRECTION_FLAG_REQUIRED")
    if correction_occurred and after_event_count <= before_event_count:
        raise SegmentRuleContractError("CORRECTION_EVENT_APPEND_REQUIRED")
    return True
