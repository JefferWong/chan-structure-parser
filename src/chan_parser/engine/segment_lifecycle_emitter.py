"""Phase 2 Stage B Segment lifecycle emission without parser integration."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from itertools import pairwise
import hashlib
import json
import math
from typing import Any, ClassVar

from ..audit.event_log import EventLog
from ..contracts.segment_lifecycle import (
    PrimaryDestructionEvidence,
    SegmentDirection,
    SegmentLifecycleContractError,
    SegmentLifecycleEventIntent,
    derive_segment_lifecycle_intents,
    filter_new_segment_lifecycle_intents,
)
from ..domain.lifecycle import LifecycleEvent, StructureStatus, StrokeDirection
from ..domain.stroke import Stroke
from .segment import FeatureElementRuleInput, SegmentEngineResult


class SegmentLifecycleEmissionError(ValueError):
    """Raised when Stage B binding or EventLog history fails closed."""


class SegmentLifecycleEmitter:
    """Validate one SegmentEngine result and append its canonical events."""

    PROFILE_ID = "minimal_segment_lifecycle_emission_v1"
    PROFILE_VERSION = "0.1.0"

    _EXPECTED_PROFILE: ClassVar[dict[str, Any]] = {
        "profile_id": PROFILE_ID,
        "profile_version": PROFILE_VERSION,
        "status": "EMITTER_ONLY",
        "source_lifecycle_contract_profile_id": (
            "minimal_segment_lifecycle_contract_v1"
        ),
        "source_lifecycle_contract_profile_version": "0.1.0",
        "source_lifecycle_contract_baseline_commit": (
            "f0b795f4487ec4713bed7a2a3abca14c7ae63f58"
        ),
        "source_segment_profile_id": "minimal_segment_engine_core_v1",
        "source_segment_profile_version": "0.1.0",
        "emission": {
            "event_emission_enabled": True,
            "event_id_authority": "EventLog",
            "intent_key_field": "segment_lifecycle_intent_key",
            "binding_key_field": "segment_lifecycle_binding_key",
            "canonical_prefix_history_required": True,
            "partial_created_recovery_enabled": True,
        },
        "binding": {
            "source_strokes_required_for_first_case": True,
            "exact_primary_feature_elements_required": True,
            "full_feature_visibility_revalidation_required": True,
            "ordered_feature_provenance_required": True,
            "caller_supplied_intents_allowed": False,
            "canonical_oracle_calls_allowed": False,
        },
        "integration": {
            "full_rebuild_reference_integration_enabled": True,
            "parser_integration_enabled": False,
            "checkpoint_integration_enabled": False,
            "bounded_tail_integration_enabled": False,
            "full_incremental_integration_enabled": False,
            "second_case_confirmation_enabled": False,
            "center_or_zhongshu_enabled": False,
        },
    }
    _FIRST_CASE = "SEGMENT_FIRST_CASE_CONFIRMED"
    _STAGE_B_REASON_CODES = frozenset(
        {"SEGMENT_FIRST_CASE_CREATED", "SEGMENT_FIRST_CASE_CONFIRMED"}
    )

    def __init__(self, profile: Mapping[str, Any]):
        self._validate_exact_mapping(profile, self._EXPECTED_PROFILE, "profile")
        self.profile_id = self.PROFILE_ID
        self.profile_version = self.PROFILE_VERSION

    @classmethod
    def reference_profile(cls) -> dict[str, Any]:
        """Return an isolated copy of the authoritative emitter profile."""
        return deepcopy(cls._EXPECTED_PROFILE)

    def emit(
        self,
        *,
        result: SegmentEngineResult,
        source_strokes: Sequence[Stroke],
        event_log: EventLog,
    ) -> tuple[LifecycleEvent, ...]:
        """Append only unseen canonical Segment lifecycle events."""

        if type(result) is not SegmentEngineResult:
            raise SegmentLifecycleEmissionError("SegmentEngineResult required")
        if not isinstance(event_log, EventLog):
            raise SegmentLifecycleEmissionError("EventLog required")

        if result.reason_code != self._FIRST_CASE:
            intents = self._derive_intents(result)
            if intents:
                raise SegmentLifecycleEmissionError(
                    "non-first-case outcome produced lifecycle intents"
                )
            return ()

        source = self._validate_source_strokes(source_strokes)
        emission_binding = self._validate_first_case_binding(result, source)
        binding_key = hashlib.sha256(
            self._canonical_json(emission_binding).encode("utf-8")
        ).hexdigest()
        intents = self._derive_intents(result)
        if len(intents) != 2:
            raise SegmentLifecycleEmissionError(
                "first-case outcome must derive exactly two intents"
            )

        segment = result.segment
        assert segment is not None
        existing_keys = self._reconcile_history(
            event_log=event_log,
            logical_id=segment.logical_id,
            object_id=segment.object_id,
            intents=intents,
            binding_key=binding_key,
            emission_binding=emission_binding,
        )
        try:
            pending = filter_new_segment_lifecycle_intents(
                intents,
                existing_keys,
            )
        except SegmentLifecycleContractError as error:
            raise SegmentLifecycleEmissionError(str(error)) from error

        pending_events = tuple(
            self._event_from_intent(
                intent,
                binding_key=binding_key,
                emission_binding=emission_binding,
            )
            for intent in pending
        )
        if any(event.event_id != "" for event in pending_events):
            raise SegmentLifecycleEmissionError(
                "SEGMENT_LIFECYCLE_PENDING_EVENT_ID_NOT_EMPTY"
            )
        if not pending_events:
            return ()

        snapshot = event_log.snapshot()
        try:
            return tuple(event_log.record(event) for event in pending_events)
        except Exception as error:
            try:
                event_log.restore(snapshot)
            except Exception:
                raise SegmentLifecycleEmissionError(
                    "SEGMENT_LIFECYCLE_EMISSION_ROLLBACK_FAILED"
                ) from error
            raise SegmentLifecycleEmissionError(
                "SEGMENT_LIFECYCLE_EMISSION_RECORD_FAILED"
            ) from error

    @staticmethod
    def _derive_intents(
        result: SegmentEngineResult,
    ) -> tuple[SegmentLifecycleEventIntent, ...]:
        try:
            return derive_segment_lifecycle_intents(
                outcome_code=result.reason_code,
                segment=result.segment,
                primary_evidence=result.primary_evidence,
            )
        except SegmentLifecycleContractError as error:
            raise SegmentLifecycleEmissionError(str(error)) from error

    def _validate_first_case_binding(
        self,
        result: SegmentEngineResult,
        source: tuple[Stroke, ...],
    ) -> dict[str, Any]:
        segment = result.segment
        evidence = result.primary_evidence
        if segment is None:
            raise SegmentLifecycleEmissionError("first-case Segment required")
        if type(evidence) is not PrimaryDestructionEvidence:
            raise SegmentLifecycleEmissionError(
                "first-case primary evidence required"
            )
        endpoint = evidence.endpoint
        if endpoint is None:
            raise SegmentLifecycleEmissionError("first-case endpoint required")
        if type(result.candidate_direction) is not StrokeDirection:
            raise SegmentLifecycleEmissionError("candidate direction invalid")
        if segment.direction != result.candidate_direction:
            raise SegmentLifecycleEmissionError(
                "SEGMENT_CANDIDATE_DIRECTION_MISMATCH"
            )
        rule_direction = (
            SegmentDirection.UP
            if segment.direction == StrokeDirection.UP
            else SegmentDirection.DOWN
        )
        if evidence.candidate_direction != rule_direction:
            raise SegmentLifecycleEmissionError(
                "SEGMENT_EVIDENCE_DIRECTION_MISMATCH"
            )

        matches = tuple(
            (index, stroke)
            for index, stroke in enumerate(source)
            if stroke.direction == result.candidate_direction
            and stroke.end_fractal_id == endpoint.endpoint_id
            and stroke.end_bar_index == endpoint.bar_index
            and stroke.end_price == endpoint.price
        )
        if len(matches) != 1:
            raise SegmentLifecycleEmissionError(
                f"SEGMENT_BOUNDARY_MATCH_COUNT={len(matches)}"
            )
        boundary_index, boundary = matches[0]
        candidate_prefix = source[: boundary_index + 1]
        if (
            len(candidate_prefix) < 3
            or len(candidate_prefix) % 2 == 0
            or candidate_prefix[-1].direction != result.candidate_direction
        ):
            raise SegmentLifecycleEmissionError("SEGMENT_CANDIDATE_PREFIX_INVALID")
        first = candidate_prefix[0]
        expected_logical_id = f"segment:{first.logical_id}->{boundary.logical_id}"
        direction_code = (
            "U" if result.candidate_direction == StrokeDirection.UP else "D"
        )
        expected_segment_id = (
            f"segment_{first.start_bar_index + 1:06d}_"
            f"{endpoint.bar_index + 1:06d}_{direction_code}"
        )
        identity_comparisons = {
            "segment_id": (segment.segment_id, expected_segment_id),
            "object_id": (segment.object_id, f"{expected_segment_id}_r1"),
            "revision": (segment.revision, 1),
        }
        for name, (actual, expected) in identity_comparisons.items():
            if actual != expected or (
                name == "revision" and type(actual) is not int
            ):
                raise SegmentLifecycleEmissionError(
                    f"SEGMENT_IDENTITY_MISMATCH:{name}"
                )
        comparisons = {
            "stroke_ids": (
                segment.stroke_ids,
                [stroke.stroke_id for stroke in candidate_prefix],
            ),
            "start_stroke_id": (segment.start_stroke_id, first.stroke_id),
            "end_stroke_id": (segment.end_stroke_id, boundary.stroke_id),
            "start_bar_index": (
                segment.start_bar_index,
                first.start_bar_index,
            ),
            "end_bar_index": (segment.end_bar_index, endpoint.bar_index),
            "start_price": (segment.start_price, first.start_price),
            "end_price": (segment.end_price, endpoint.price),
            "logical_id": (segment.logical_id, expected_logical_id),
        }
        for name, (actual, expected) in comparisons.items():
            if actual != expected:
                raise SegmentLifecycleEmissionError(
                    f"SEGMENT_SOURCE_BINDING_MISMATCH:{name}"
                )

        feature_elements = self._primary_feature_elements(result, evidence)
        source_by_logical_id = {stroke.logical_id: stroke for stroke in source}
        source_position = {
            stroke.logical_id: index for index, stroke in enumerate(source)
        }
        expected_feature_stroke_direction = (
            StrokeDirection.DOWN
            if evidence.candidate_direction == SegmentDirection.UP
            else StrokeDirection.UP
        )
        feature_source_logical_ids: list[str] = []
        authenticated_visibility: list[int] = []
        previous_source_position = -1
        for element in feature_elements:
            provenance_strokes: list[Stroke] = []
            for logical_id in element.interval.source_stroke_logical_ids:
                stroke = source_by_logical_id.get(logical_id)
                if stroke is None:
                    raise SegmentLifecycleEmissionError(
                        "SEGMENT_FEATURE_SOURCE_NOT_IN_SOURCE_STROKES"
                    )
                if stroke.direction != expected_feature_stroke_direction:
                    raise SegmentLifecycleEmissionError(
                        "SEGMENT_FEATURE_SOURCE_DIRECTION_MISMATCH"
                    )
                position = source_position[logical_id]
                if position <= previous_source_position:
                    raise SegmentLifecycleEmissionError(
                        "SEGMENT_FEATURE_SOURCE_ORDER_MISMATCH"
                    )
                previous_source_position = position
                provenance_strokes.append(stroke)
                feature_source_logical_ids.append(logical_id)
            expected_element_visibility = max(
                stroke.confirmed_at_bar for stroke in provenance_strokes
            )
            if element.visible_at_bar_index != expected_element_visibility:
                raise SegmentLifecycleEmissionError(
                    "SEGMENT_FEATURE_VISIBILITY_SOURCE_MISMATCH"
                )
            authenticated_visibility.append(expected_element_visibility)

        expected_confirmation_bar = max(
            endpoint.bar_index,
            *authenticated_visibility,
        )
        if (
            segment.confirmed_at_bar != expected_confirmation_bar
            or segment.created_at_bar != expected_confirmation_bar
        ):
            raise SegmentLifecycleEmissionError(
                "SEGMENT_FULL_FEATURE_VISIBILITY_MISMATCH"
            )

        expected_feature_stroke_ids = [
            source_by_logical_id[logical_id].stroke_id
            for logical_id in feature_source_logical_ids
        ]
        if segment.feature_sequence_stroke_ids != expected_feature_stroke_ids:
            raise SegmentLifecycleEmissionError(
                "SEGMENT_FEATURE_PROVENANCE_MISMATCH"
            )
        expected_destruction_stroke_ids = [
            source_by_logical_id[logical_id].stroke_id
            for logical_id in feature_source_logical_ids
            if source_by_logical_id[logical_id].end_bar_index
            > endpoint.bar_index
        ]
        if (
            segment.destruction_evidence_stroke_ids
            != expected_destruction_stroke_ids
        ):
            raise SegmentLifecycleEmissionError(
                "SEGMENT_DESTRUCTION_PROVENANCE_MISMATCH"
            )

        return {
            "source_stroke_logical_ids": tuple(
                stroke.logical_id for stroke in source
            ),
            "source_stroke_ids": tuple(stroke.stroke_id for stroke in source),
            "primary_element_logical_ids": tuple(
                evidence.primary_element_logical_ids
            ),
            "primary_feature_visibility": tuple(
                {
                    "logical_id": element.logical_id,
                    "visible_at_bar_index": element.visible_at_bar_index,
                    "source_stroke_logical_ids": tuple(
                        element.interval.source_stroke_logical_ids
                    ),
                }
                for element in feature_elements
            ),
            "endpoint_bar_index": endpoint.bar_index,
            "confirmation_bar": expected_confirmation_bar,
        }

    @staticmethod
    def _primary_feature_elements(
        result: SegmentEngineResult,
        evidence: PrimaryDestructionEvidence,
    ) -> tuple[
        FeatureElementRuleInput,
        FeatureElementRuleInput,
        FeatureElementRuleInput,
    ]:
        elements = result.feature_elements
        if type(elements) is not tuple or any(
            type(element) is not FeatureElementRuleInput for element in elements
        ):
            raise SegmentLifecycleEmissionError(
                "SEGMENT_FEATURE_ELEMENTS_INVALID"
            )
        primary_ids = evidence.primary_element_logical_ids
        if (
            type(primary_ids) is not tuple
            or len(primary_ids) != 3
            or len(set(primary_ids)) != 3
        ):
            raise SegmentLifecycleEmissionError(
                "SEGMENT_PRIMARY_ELEMENT_IDS_INVALID"
            )
        selected: list[FeatureElementRuleInput] = []
        locations: list[int] = []
        for logical_id in primary_ids:
            matches = tuple(
                (index, element)
                for index, element in enumerate(elements)
                if element.logical_id == logical_id
            )
            if len(matches) != 1:
                raise SegmentLifecycleEmissionError(
                    f"SEGMENT_PRIMARY_ELEMENT_MATCH_COUNT:{logical_id}:{len(matches)}"
                )
            index, element = matches[0]
            locations.append(index)
            selected.append(element)
        if locations != list(range(locations[0], locations[0] + 3)):
            raise SegmentLifecycleEmissionError(
                "SEGMENT_PRIMARY_ELEMENT_ORDER_MISMATCH"
            )
        expected_feature_direction = (
            SegmentDirection.DOWN
            if evidence.candidate_direction == SegmentDirection.UP
            else SegmentDirection.UP
        )
        for element in selected:
            if element.sequence_id != evidence.primary_sequence_id:
                raise SegmentLifecycleEmissionError(
                    "SEGMENT_PRIMARY_SEQUENCE_MISMATCH"
                )
            if element.direction != expected_feature_direction:
                raise SegmentLifecycleEmissionError(
                    "SEGMENT_PRIMARY_FEATURE_DIRECTION_MISMATCH"
                )
            provenance = element.interval.source_stroke_logical_ids
            if (
                type(provenance) is not tuple
                or not provenance
                or any(type(value) is not str or not value for value in provenance)
            ):
                raise SegmentLifecycleEmissionError(
                    "SEGMENT_PRIMARY_FEATURE_PROVENANCE_INVALID"
                )
            if (
                type(element.visible_at_bar_index) is not int
                or element.visible_at_bar_index < 0
            ):
                raise SegmentLifecycleEmissionError(
                    "SEGMENT_PRIMARY_FEATURE_VISIBILITY_INVALID"
                )
        return tuple(selected)  # type: ignore[return-value]

    def _reconcile_history(
        self,
        *,
        event_log: EventLog,
        logical_id: str,
        object_id: str,
        intents: tuple[SegmentLifecycleEventIntent, ...],
        binding_key: str,
        emission_binding: Mapping[str, Any],
    ) -> set[str]:
        canonical_by_key = {intent.intent_key: intent for intent in intents}
        relevant: list[Mapping[str, Any]] = []
        for event in event_log.to_list():
            if not isinstance(event, Mapping):
                raise SegmentLifecycleEmissionError(
                    "SEGMENT_LIFECYCLE_HISTORY_EVENT_INVALID"
                )
            detail = event.get("detail")
            intent_key = (
                detail.get("segment_lifecycle_intent_key")
                if isinstance(detail, Mapping)
                else None
            )
            identity_match = (
                event.get("logical_id") == logical_id
                or event.get("object_id") == object_id
            )
            canonical_intent_match = (
                type(intent_key) is str and intent_key in canonical_by_key
            )
            reason_code = event.get("reason_code")
            stage_b_marker = (
                type(reason_code) is str
                and reason_code in self._STAGE_B_REASON_CODES
            ) or (
                isinstance(detail, Mapping)
                and any(
                    marker in detail
                    for marker in (
                        "segment_lifecycle_intent_key",
                        "segment_lifecycle_binding_key",
                        "emission_binding",
                    )
                )
            )
            if canonical_intent_match or (identity_match and stage_b_marker):
                relevant.append(event)

        seen: list[str] = []
        for event in relevant:
            detail = event.get("detail")
            if not isinstance(detail, Mapping):
                raise SegmentLifecycleEmissionError(
                    "SEGMENT_LIFECYCLE_HISTORY_DETAIL_INVALID"
                )
            intent_key = detail.get("segment_lifecycle_intent_key")
            if type(intent_key) is not str or not intent_key:
                raise SegmentLifecycleEmissionError(
                    "SEGMENT_LIFECYCLE_HISTORY_INTENT_KEY_MISSING"
                )
            if intent_key not in canonical_by_key:
                raise SegmentLifecycleEmissionError(
                    "SEGMENT_LIFECYCLE_HISTORY_INTENT_KEY_UNKNOWN"
                )
            if intent_key in seen:
                raise SegmentLifecycleEmissionError(
                    "SEGMENT_LIFECYCLE_HISTORY_INTENT_KEY_DUPLICATE"
                )
            intent = canonical_by_key[intent_key]
            expected_fields = {
                "event_type": intent.event_type,
                "object_type": intent.object_type,
                "object_id": intent.object_id,
                "logical_id": intent.logical_id,
                "occurred_at_bar_id": intent.occurred_at_bar_id,
                "reason_code": intent.reason_code,
                "rule_profile": intent.rule_profile,
                "rule_version": intent.rule_version,
            }
            for name, expected in expected_fields.items():
                if event.get(name) != expected:
                    raise SegmentLifecycleEmissionError(
                        f"SEGMENT_LIFECYCLE_HISTORY_CONFLICT:{name}"
                    )
            expected_detail = self._event_detail(
                intent,
                binding_key=binding_key,
                emission_binding=emission_binding,
            )
            if detail != expected_detail:
                raise SegmentLifecycleEmissionError(
                    "SEGMENT_LIFECYCLE_HISTORY_CONFLICT:detail"
                )
            seen.append(intent_key)

        canonical_keys = tuple(intent.intent_key for intent in intents)
        if tuple(seen) != canonical_keys[: len(seen)]:
            raise SegmentLifecycleEmissionError(
                "SEGMENT_LIFECYCLE_HISTORY_NOT_CANONICAL_PREFIX"
            )
        return set(seen)

    @classmethod
    def _event_from_intent(
        cls,
        intent: SegmentLifecycleEventIntent,
        *,
        binding_key: str,
        emission_binding: Mapping[str, Any],
    ) -> LifecycleEvent:
        return LifecycleEvent(
            event_id="",
            event_type=intent.event_type,
            object_type=intent.object_type,
            object_id=intent.object_id,
            logical_id=intent.logical_id,
            occurred_at_bar_id=intent.occurred_at_bar_id,
            reason_code=intent.reason_code,
            rule_profile=intent.rule_profile,
            rule_version=intent.rule_version,
            detail=cls._event_detail(
                intent,
                binding_key=binding_key,
                emission_binding=emission_binding,
            ),
        )

    @classmethod
    def _event_detail(
        cls,
        intent: SegmentLifecycleEventIntent,
        *,
        binding_key: str,
        emission_binding: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            **cls._plain_value(intent.detail),
            "segment_lifecycle_intent_key": intent.intent_key,
            "segment_lifecycle_binding_key": binding_key,
            "emission_binding": cls._plain_value(emission_binding),
        }

    @staticmethod
    def _validate_source_strokes(
        source_strokes: Sequence[Stroke],
    ) -> tuple[Stroke, ...]:
        if (
            not isinstance(source_strokes, Sequence)
            or isinstance(source_strokes, (str, bytes, bytearray))
        ):
            raise SegmentLifecycleEmissionError(
                "SEGMENT_EMISSION_SOURCE_STROKES_REQUIRED"
            )
        source = tuple(source_strokes)
        if not source or any(type(stroke) is not Stroke for stroke in source):
            raise SegmentLifecycleEmissionError(
                "SEGMENT_EMISSION_SOURCE_STROKES_REQUIRED"
            )
        logical_ids: list[str] = []
        stroke_ids: list[str] = []
        for stroke in source:
            identifiers = (
                stroke.logical_id,
                stroke.stroke_id,
                stroke.object_id,
                stroke.start_fractal_id,
                stroke.end_fractal_id,
            )
            if any(type(value) is not str or not value for value in identifiers):
                raise SegmentLifecycleEmissionError(
                    "SEGMENT_EMISSION_SOURCE_ID_INVALID"
                )
            if type(stroke.direction) is not StrokeDirection:
                raise SegmentLifecycleEmissionError(
                    "SEGMENT_EMISSION_SOURCE_DIRECTION_INVALID"
                )
            if stroke.status != StructureStatus.CONFIRMED:
                raise SegmentLifecycleEmissionError(
                    "SEGMENT_EMISSION_SOURCE_NOT_CONFIRMED"
                )
            if (
                type(stroke.start_bar_index) is not int
                or type(stroke.end_bar_index) is not int
                or stroke.start_bar_index < 0
                or stroke.end_bar_index <= stroke.start_bar_index
                or type(stroke.confirmed_at_bar) is not int
                or stroke.confirmed_at_bar < stroke.end_bar_index
            ):
                raise SegmentLifecycleEmissionError(
                    "SEGMENT_EMISSION_SOURCE_BAR_INVALID"
                )
            if not all(
                cls_value in {int, float}
                and math.isfinite(value)
                for cls_value, value in (
                    (type(stroke.start_price), stroke.start_price),
                    (type(stroke.end_price), stroke.end_price),
                )
            ):
                raise SegmentLifecycleEmissionError(
                    "SEGMENT_EMISSION_SOURCE_PRICE_INVALID"
                )
            if (
                stroke.direction == StrokeDirection.UP
                and stroke.start_price >= stroke.end_price
            ) or (
                stroke.direction == StrokeDirection.DOWN
                and stroke.start_price <= stroke.end_price
            ):
                raise SegmentLifecycleEmissionError(
                    "SEGMENT_EMISSION_SOURCE_DIRECTION_PRICE_MISMATCH"
                )
            logical_ids.append(stroke.logical_id)
            stroke_ids.append(stroke.stroke_id)
        if len(logical_ids) != len(set(logical_ids)):
            raise SegmentLifecycleEmissionError(
                "SEGMENT_EMISSION_SOURCE_LOGICAL_ID_DUPLICATE"
            )
        if len(stroke_ids) != len(set(stroke_ids)):
            raise SegmentLifecycleEmissionError(
                "SEGMENT_EMISSION_SOURCE_STROKE_ID_DUPLICATE"
            )
        for previous, current in pairwise(source):
            if previous.direction == current.direction:
                raise SegmentLifecycleEmissionError(
                    "SEGMENT_EMISSION_SOURCE_NOT_ALTERNATING"
                )
            if (
                previous.end_fractal_id != current.start_fractal_id
                or previous.end_bar_index != current.start_bar_index
            ):
                raise SegmentLifecycleEmissionError(
                    "SEGMENT_EMISSION_SOURCE_ENDPOINT_NOT_CONTIGUOUS"
                )
            if previous.end_price != current.start_price:
                raise SegmentLifecycleEmissionError(
                    "SEGMENT_EMISSION_SOURCE_PRICE_NOT_CONTIGUOUS"
                )
        return source

    @classmethod
    def _plain_value(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {key: cls._plain_value(value[key]) for key in sorted(value)}
        if isinstance(value, tuple):
            return tuple(cls._plain_value(item) for item in value)
        if isinstance(value, list):
            return [cls._plain_value(item) for item in value]
        return value

    @classmethod
    def _canonical_json(cls, value: Any) -> str:
        return json.dumps(
            cls._plain_value(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @classmethod
    def _validate_exact_mapping(
        cls,
        actual: Mapping[str, Any],
        expected: Mapping[str, Any],
        path: str,
    ) -> None:
        if not isinstance(actual, Mapping):
            raise SegmentLifecycleEmissionError(f"{path} must be a mapping")
        missing = set(expected) - set(actual)
        unknown = set(actual) - set(expected)
        if missing or unknown:
            raise SegmentLifecycleEmissionError(
                f"{path} keys invalid: missing={sorted(missing)}, "
                f"unknown={sorted(unknown)}"
            )
        for key, expected_value in expected.items():
            value = actual[key]
            child = f"{path}.{key}"
            if isinstance(expected_value, Mapping):
                cls._validate_exact_mapping(value, expected_value, child)
            elif type(value) is not type(expected_value) or value != expected_value:
                raise SegmentLifecycleEmissionError(
                    f"{child}={value!r} is unsupported; "
                    f"expected {expected_value!r}"
                )
