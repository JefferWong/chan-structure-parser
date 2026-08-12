"""Pure Phase 2 SegmentEngine core for primary feature-sequence evaluation.

R1 intentionally stops before parser integration, lifecycle-event emission,
checkpoint/replay, full-vs-incremental orchestration, and second-case
confirmation. It binds confirmed Phase 1 ``Stroke`` objects to the frozen
canonical rule oracle and may materialize only a complete first-case segment.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping, Sequence

from ..contracts.segment_rules import (
    DestructionCase,
    FeatureElementRuleInput,
    FeatureEndpointEvidence,
    FeatureIntervalSemantics,
    InclusionContext,
    InclusionSeed,
    IntervalRelation,
    PendingSecondCaseContext,
    PriceInterval,
    PrimaryDestructionEvidence,
    PrimarySequenceContext,
    SegmentBoundaryInput,
    SegmentDirection,
    StrokeRuleInput,
    build_feature_sequence,
    build_pending_second_case_context,
    classify_interval_relation,
    classify_primary_destruction_case,
    confirmation_bar,
    derive_inclusion_seed,
    merge_included_intervals,
    validate_segment_boundaries,
)
from ..domain.lifecycle import StructureStatus, StrokeDirection
from ..domain.segment import Segment
from ..domain.stroke import Stroke


class SegmentEngineCoreError(ValueError):
    """Raised when the core engine cannot make a deterministic safe decision."""


@dataclass(frozen=True)
class SegmentEngineResult:
    """One deterministic R1 primary-sequence evaluation."""

    reason_code: str
    candidate_direction: StrokeDirection
    feature_elements: tuple[FeatureElementRuleInput, ...]
    primary_evidence: PrimaryDestructionEvidence | None = None
    pending_second_case: PendingSecondCaseContext | None = None
    segment: Segment | None = None


class SegmentEngine:
    """Build standard primary feature elements and materialize CASE1 only."""

    PROFILE_ID = "minimal_segment_engine_core_v1"
    PROFILE_VERSION = "0.1.0"
    CANONICAL_PROFILE_ID = "minimal_segment_canonical_rules_v1"
    CANONICAL_PROFILE_VERSION = "1.0.1"
    CANONICAL_BASELINE = "b2c88f38039cfe0ca0f3682e762bc6df3431de1d"

    _EXPECTED_PROFILE: dict[str, Any] = {
        "profile_id": PROFILE_ID,
        "profile_version": PROFILE_VERSION,
        "status": "ENGINE_CORE_ONLY",
        "canonical_rules_profile_id": CANONICAL_PROFILE_ID,
        "canonical_rules_profile_version": CANONICAL_PROFILE_VERSION,
        "canonical_rules_baseline_commit": CANONICAL_BASELINE,
        "implementation": {
            "primary_feature_adapter_enabled": True,
            "first_case_materialization_enabled": True,
            "second_case_orchestration_enabled": False,
            "lifecycle_events_enabled": False,
            "parser_integration_enabled": False,
            "checkpoint_integration_enabled": False,
            "full_incremental_integration_enabled": False,
        },
        "evidence_binding": {
            "source_stroke_status": "CONFIRMED",
            "unseeded_inclusion_policy": "fail_closed",
            "equal_extremum_endpoint_policy": "earliest_bar_then_endpoint_id",
        },
        "prohibited": {
            "parser_integration": True,
            "center_or_zhongshu": True,
            "czsc_or_chanpy": True,
            "trading_signal": True,
            "position_or_execution": True,
        },
    }

    def __init__(self, profile: Mapping[str, Any]):
        self._validate_profile(profile)
        self.profile_id = self.PROFILE_ID
        self.profile_version = self.PROFILE_VERSION

    @classmethod
    def _validate_profile(cls, profile: Mapping[str, Any]) -> None:
        cls._validate_exact_mapping(profile, cls._EXPECTED_PROFILE, "profile")

    @classmethod
    def _validate_exact_mapping(
        cls,
        actual: Mapping[str, Any],
        expected: Mapping[str, Any],
        path: str,
    ) -> None:
        if not isinstance(actual, Mapping):
            raise SegmentEngineCoreError(f"{path} must be a mapping")
        missing = set(expected) - set(actual)
        unknown = set(actual) - set(expected)
        if missing or unknown:
            raise SegmentEngineCoreError(
                f"{path} keys invalid: missing={sorted(missing)}, "
                f"unknown={sorted(unknown)}"
            )
        for key, expected_value in expected.items():
            value = actual[key]
            child = f"{path}.{key}"
            if isinstance(expected_value, Mapping):
                cls._validate_exact_mapping(value, expected_value, child)
            elif type(value) is not type(expected_value) or value != expected_value:
                raise SegmentEngineCoreError(
                    f"{child}={value!r} is unsupported; expected {expected_value!r}"
                )

    def process_primary(
        self,
        strokes: Sequence[Stroke],
        *,
        sequence_id: str,
    ) -> SegmentEngineResult:
        """Evaluate the first confirmable primary feature window in source order."""
        source = self._validate_source_strokes(strokes, sequence_id=sequence_id)
        candidate_direction = source[0].direction
        rule_direction = self._to_rule_direction(candidate_direction)

        inputs = tuple(
            StrokeRuleInput(
                stroke.logical_id,
                stroke.object_id,
                self._to_rule_direction(stroke.direction),
                stroke.min_price,
                stroke.max_price,
                stroke.start_bar_index,
                stroke.end_bar_index,
                sequence_id,
            )
            for stroke in source
        )
        feature_sequence = build_feature_sequence(
            rule_direction,
            inputs,
            sequence_id=sequence_id,
        )
        by_logical_id = {stroke.logical_id: stroke for stroke in source}
        selected = tuple(
            by_logical_id[logical_id]
            for logical_id in feature_sequence.source_stroke_logical_ids
        )
        raw_elements = tuple(
            self._raw_feature_element(stroke, sequence_id=sequence_id)
            for stroke in selected
        )
        standard = self._normalize_feature_elements(
            raw_elements,
            candidate_direction=rule_direction,
        )

        if len(standard) < 3:
            return SegmentEngineResult(
                "SEGMENT_FEATURE_WINDOW_INCOMPLETE",
                candidate_direction,
                standard,
            )

        for index in range(len(standard) - 2):
            window = standard[index : index + 3]
            provenance = tuple(
                logical_id
                for element in window
                for logical_id in element.interval.source_stroke_logical_ids
            )
            context = PrimarySequenceContext(
                rule_direction,
                sequence_id,
                provenance,
            )
            evidence = classify_primary_destruction_case(
                *window,
                context=context,
            )
            if evidence.destruction_case == DestructionCase.NONE:
                continue
            if evidence.destruction_case == DestructionCase.SECOND_CASE_PENDING:
                pending = build_pending_second_case_context(
                    evidence,
                    secondary_sequence_id=self._secondary_sequence_id(evidence),
                )
                return SegmentEngineResult(
                    "SEGMENT_SECOND_CASE_PENDING",
                    candidate_direction,
                    standard,
                    primary_evidence=evidence,
                    pending_second_case=pending,
                )
            if evidence.destruction_case == DestructionCase.FIRST_CASE:
                segment = self._materialize_first_case(
                    source,
                    window,
                    evidence,
                )
                return SegmentEngineResult(
                    "SEGMENT_FIRST_CASE_CONFIRMED",
                    candidate_direction,
                    standard,
                    primary_evidence=evidence,
                    segment=segment,
                )
            raise SegmentEngineCoreError(
                f"unsupported R1 primary destruction case: "
                f"{evidence.destruction_case.value}"
            )

        return SegmentEngineResult(
            "SEGMENT_PRIMARY_FRACTAL_NOT_FOUND",
            candidate_direction,
            standard,
        )

    def _validate_source_strokes(
        self,
        strokes: Sequence[Stroke],
        *,
        sequence_id: str,
    ) -> tuple[Stroke, ...]:
        if (
            not isinstance(strokes, Sequence)
            or isinstance(strokes, (str, bytes, bytearray))
            or any(not isinstance(stroke, Stroke) for stroke in strokes)
        ):
            raise SegmentEngineCoreError("SEGMENT_SOURCE_STROKES_REQUIRED")
        values = tuple(strokes)
        if not values:
            raise SegmentEngineCoreError("SEGMENT_SOURCE_STROKES_REQUIRED")
        if type(sequence_id) is not str or not sequence_id:
            raise SegmentEngineCoreError("SEGMENT_SEQUENCE_ID_REQUIRED")

        logical_ids: list[str] = []
        stroke_ids: list[str] = []
        for stroke in values:
            if type(stroke.logical_id) is not str or not stroke.logical_id:
                raise SegmentEngineCoreError("SEGMENT_SOURCE_LOGICAL_ID_REQUIRED")
            if type(stroke.stroke_id) is not str or not stroke.stroke_id:
                raise SegmentEngineCoreError("SEGMENT_SOURCE_STROKE_ID_REQUIRED")
            if type(stroke.object_id) is not str or not stroke.object_id:
                raise SegmentEngineCoreError("SEGMENT_SOURCE_OBJECT_ID_REQUIRED")
            if (
                type(stroke.start_fractal_id) is not str
                or not stroke.start_fractal_id
                or type(stroke.end_fractal_id) is not str
                or not stroke.end_fractal_id
            ):
                raise SegmentEngineCoreError("SEGMENT_SOURCE_ENDPOINT_ID_REQUIRED")
            if not isinstance(stroke.direction, StrokeDirection):
                raise SegmentEngineCoreError("SEGMENT_SOURCE_DIRECTION_REQUIRED")
            if (
                type(stroke.start_bar_index) is not int
                or type(stroke.end_bar_index) is not int
                or stroke.start_bar_index < 0
                or stroke.end_bar_index <= stroke.start_bar_index
            ):
                raise SegmentEngineCoreError("SEGMENT_SOURCE_BAR_RANGE_INVALID")
            if stroke.status != StructureStatus.CONFIRMED:
                raise SegmentEngineCoreError("SEGMENT_SOURCE_STROKE_NOT_CONFIRMED")
            if (
                type(stroke.confirmed_at_bar) is not int
                or stroke.confirmed_at_bar < stroke.end_bar_index
            ):
                raise SegmentEngineCoreError(
                    "SEGMENT_SOURCE_CONFIRMATION_VISIBILITY_INVALID"
                )
            if stroke.direction == StrokeDirection.UP:
                extreme_ok = (
                    stroke.start_price < stroke.end_price
                    and stroke.min_price == stroke.start_price
                    and stroke.max_price == stroke.end_price
                )
            else:
                extreme_ok = (
                    stroke.start_price > stroke.end_price
                    and stroke.max_price == stroke.start_price
                    and stroke.min_price == stroke.end_price
                )
            if not extreme_ok:
                raise SegmentEngineCoreError(
                    "SEGMENT_SOURCE_ENDPOINT_EXTREME_MISMATCH"
                )
            logical_ids.append(stroke.logical_id)
            stroke_ids.append(stroke.stroke_id)

        if len(logical_ids) != len(set(logical_ids)):
            raise SegmentEngineCoreError("SEGMENT_SOURCE_DUPLICATE_LOGICAL_ID")
        if len(stroke_ids) != len(set(stroke_ids)):
            raise SegmentEngineCoreError("SEGMENT_SOURCE_DUPLICATE_STROKE_ID")

        for previous, current in zip(values, values[1:]):
            if previous.direction == current.direction:
                raise SegmentEngineCoreError(
                    "SEGMENT_SOURCE_DIRECTION_NOT_ALTERNATING"
                )
            if (
                previous.end_fractal_id != current.start_fractal_id
                or previous.end_bar_index != current.start_bar_index
            ):
                raise SegmentEngineCoreError(
                    "SEGMENT_SOURCE_ENDPOINT_NOT_CONTIGUOUS"
                )
            if previous.end_price != current.start_price:
                raise SegmentEngineCoreError(
                    "SEGMENT_SOURCE_ENDPOINT_PRICE_NOT_CONTIGUOUS"
                )
        return values

    def _raw_feature_element(
        self,
        stroke: Stroke,
        *,
        sequence_id: str,
    ) -> FeatureElementRuleInput:
        logical_id = stroke.logical_id
        assert logical_id is not None
        start = FeatureEndpointEvidence(
            stroke.start_fractal_id,
            (logical_id,),
            stroke.start_price,
            stroke.start_bar_index,
        )
        end = FeatureEndpointEvidence(
            stroke.end_fractal_id,
            (logical_id,),
            stroke.end_price,
            stroke.end_bar_index,
        )
        if stroke.direction == StrokeDirection.UP:
            low_endpoint, high_endpoint = start, end
        else:
            high_endpoint, low_endpoint = start, end
        interval = PriceInterval(
            stroke.min_price,
            stroke.max_price,
            (logical_id,),
        )
        return FeatureElementRuleInput(
            self._feature_element_id(
                sequence_id,
                interval.source_stroke_logical_ids,
                interval,
                start,
                end,
                high_endpoint,
                low_endpoint,
            ),
            sequence_id,
            self._to_rule_direction(stroke.direction),
            FeatureIntervalSemantics.NORMALIZED_FEATURE_RANGE,
            start,
            end,
            high_endpoint,
            low_endpoint,
            interval,
            True,
            stroke.confirmed_at_bar,
        )

    def _normalize_feature_elements(
        self,
        elements: tuple[FeatureElementRuleInput, ...],
        *,
        candidate_direction: SegmentDirection,
    ) -> tuple[FeatureElementRuleInput, ...]:
        standard: list[FeatureElementRuleInput] = []
        seed = InclusionSeed.UNSEEDED
        included_relations = {
            IntervalRelation.CONTAINS,
            IntervalRelation.CONTAINED_BY,
            IntervalRelation.EQUAL,
        }
        for element in elements:
            if not standard:
                standard.append(element)
                continue
            previous = standard[-1]
            relation = classify_interval_relation(previous.interval, element.interval)
            if relation in included_relations:
                if seed == InclusionSeed.UNSEEDED:
                    raise SegmentEngineCoreError(
                        "SEGMENT_FEATURE_INCLUSION_UNSEEDED"
                    )
                standard[-1] = self._merge_feature_elements(
                    previous,
                    element,
                    seed,
                    candidate_direction=candidate_direction,
                )
                continue
            seed = derive_inclusion_seed(previous.interval, element.interval)
            standard.append(element)
        return tuple(standard)

    def _merge_feature_elements(
        self,
        first: FeatureElementRuleInput,
        second: FeatureElementRuleInput,
        seed: InclusionSeed,
        *,
        candidate_direction: SegmentDirection,
    ) -> FeatureElementRuleInput:
        if first.direction != second.direction:
            raise SegmentEngineCoreError("SEGMENT_FEATURE_DIRECTION_MISMATCH")
        merged = merge_included_intervals(
            first.interval,
            second.interval,
            seed,
            context=InclusionContext(
                first.sequence_id,
                second.sequence_id,
                candidate_direction,
                candidate_direction,
            ),
        )
        high_endpoint = self._select_bound_endpoint(
            merged.high,
            (first.high_endpoint, second.high_endpoint),
            label="high",
        )
        low_endpoint = self._select_bound_endpoint(
            merged.low,
            (first.low_endpoint, second.low_endpoint),
            label="low",
        )
        start = first.start_endpoint
        end = second.end_endpoint
        return FeatureElementRuleInput(
            self._feature_element_id(
                first.sequence_id,
                merged.source_stroke_logical_ids,
                merged,
                start,
                end,
                high_endpoint,
                low_endpoint,
            ),
            first.sequence_id,
            first.direction,
            FeatureIntervalSemantics.NORMALIZED_FEATURE_RANGE,
            start,
            end,
            high_endpoint,
            low_endpoint,
            merged,
            True,
            max(first.visible_at_bar_index, second.visible_at_bar_index),
        )

    @staticmethod
    def _select_bound_endpoint(
        price: float,
        candidates: tuple[FeatureEndpointEvidence, ...],
        *,
        label: str,
    ) -> FeatureEndpointEvidence:
        matches = tuple(item for item in candidates if item.price == price)
        if not matches:
            raise SegmentEngineCoreError(
                f"SEGMENT_FEATURE_{label.upper()}_ENDPOINT_MISSING"
            )
        return min(
            matches,
            key=lambda item: (
                item.bar_index,
                item.endpoint_id,
                item.defining_stroke_logical_ids,
            ),
        )

    def _materialize_first_case(
        self,
        source: tuple[Stroke, ...],
        window: tuple[
            FeatureElementRuleInput,
            FeatureElementRuleInput,
            FeatureElementRuleInput,
        ],
        evidence: PrimaryDestructionEvidence,
    ) -> Segment:
        endpoint = evidence.endpoint
        if endpoint is None:
            raise SegmentEngineCoreError("SEGMENT_FIRST_CASE_ENDPOINT_REQUIRED")
        candidate_direction = source[0].direction
        matches = [
            (index, stroke)
            for index, stroke in enumerate(source)
            if stroke.direction == candidate_direction
            and stroke.end_fractal_id == endpoint.endpoint_id
            and stroke.end_bar_index == endpoint.bar_index
            and stroke.end_price == endpoint.price
        ]
        if len(matches) != 1:
            raise SegmentEngineCoreError(
                "SEGMENT_FIRST_CASE_BOUNDARY_NOT_UNIQUE"
            )
        boundary_index, boundary_stroke = matches[0]
        candidate_window = source[: boundary_index + 1]
        if (
            len(candidate_window) < 3
            or len(candidate_window) % 2 == 0
            or candidate_window[-1].direction != candidate_direction
        ):
            raise SegmentEngineCoreError("SEGMENT_FIRST_CASE_WINDOW_INVALID")

        start_stroke = candidate_window[0]
        logical_id = (
            f"segment:{start_stroke.logical_id}->{boundary_stroke.logical_id}"
        )
        boundary_input = SegmentBoundaryInput(
            logical_id,
            self._to_rule_direction(candidate_direction),
            self._to_rule_direction(start_stroke.direction),
            self._to_rule_direction(boundary_stroke.direction),
            start_stroke.start_fractal_id,
            endpoint.endpoint_id,
        )
        validate_segment_boundaries(boundary_input)

        confirmed_at = confirmation_bar(
            endpoint.bar_index,
            (window[2].visible_at_bar_index,),
        )
        by_logical_id = {stroke.logical_id: stroke for stroke in source}
        feature_logical_ids = tuple(
            logical_source
            for element in window
            for logical_source in element.interval.source_stroke_logical_ids
        )
        feature_stroke_ids = [
            by_logical_id[logical_source].stroke_id
            for logical_source in feature_logical_ids
        ]
        destruction_stroke_ids = [
            by_logical_id[logical_source].stroke_id
            for logical_source in feature_logical_ids
            if by_logical_id[logical_source].end_bar_index > endpoint.bar_index
        ]
        direction_code = "U" if candidate_direction == StrokeDirection.UP else "D"
        segment_id = (
            f"segment_{start_stroke.start_bar_index + 1:06d}_"
            f"{endpoint.bar_index + 1:06d}_{direction_code}"
        )
        return Segment(
            object_id=f"{segment_id}_r1",
            logical_id=logical_id,
            revision=1,
            status=StructureStatus.CONFIRMED,
            created_at_bar=confirmed_at,
            confirmed_at_bar=confirmed_at,
            rule_profile=self.profile_id,
            rule_version=self.profile_version,
            segment_id=segment_id,
            direction=candidate_direction,
            start_stroke_id=start_stroke.stroke_id,
            end_stroke_id=boundary_stroke.stroke_id,
            stroke_ids=[stroke.stroke_id for stroke in candidate_window],
            feature_sequence_stroke_ids=feature_stroke_ids,
            destruction_evidence_stroke_ids=destruction_stroke_ids,
            start_price=start_stroke.start_price,
            end_price=endpoint.price,
            start_bar_index=start_stroke.start_bar_index,
            end_bar_index=endpoint.bar_index,
            confirmation_requirements=[],
            repaint_risk="NONE",
        )

    @staticmethod
    def _feature_element_id(
        sequence_id: str,
        provenance: tuple[str, ...],
        interval: PriceInterval,
        start: FeatureEndpointEvidence,
        end: FeatureEndpointEvidence,
        high: FeatureEndpointEvidence,
        low: FeatureEndpointEvidence,
    ) -> str:
        payload = "|".join(
            (
                sequence_id,
                ",".join(provenance),
                repr(interval.low),
                repr(interval.high),
                start.endpoint_id,
                end.endpoint_id,
                high.endpoint_id,
                low.endpoint_id,
            )
        )
        return f"feature:{hashlib.sha256(payload.encode()).hexdigest()[:20]}"

    @staticmethod
    def _secondary_sequence_id(evidence: PrimaryDestructionEvidence) -> str:
        endpoint = evidence.endpoint
        if endpoint is None:
            raise SegmentEngineCoreError("SEGMENT_PENDING_ENDPOINT_REQUIRED")
        payload = (
            f"{evidence.primary_sequence_id}|{evidence.evidence_key}|"
            f"{endpoint.endpoint_id}"
        )
        return f"secondary:{hashlib.sha256(payload.encode()).hexdigest()[:20]}"

    @staticmethod
    def _to_rule_direction(direction: StrokeDirection) -> SegmentDirection:
        return (
            SegmentDirection.UP
            if direction == StrokeDirection.UP
            else SegmentDirection.DOWN
        )
