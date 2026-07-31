"""Executable Phase 2 contract for segment evidence windows.

The validator proves only that a stroke window is eligible for a future
canonical feature-sequence algorithm. Acceptance does not mean that a segment
has been constructed or confirmed.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Iterable

from ..domain.lifecycle import StructureStatus, StrokeDirection
from ..domain.stroke import Stroke


class SegmentContractError(ValueError):
    """Raised when a profile exposes unsupported Phase 2 behavior."""


@dataclass(frozen=True)
class SegmentContractResult:
    accepted: bool
    reason_code: str
    candidate_key: str = ""
    confirmation_evidence_key: str = ""
    direction: StrokeDirection | None = None
    feature_sequence_stroke_ids: tuple[str, ...] = ()
    earliest_confirmation_bar: int | None = None
    detail: dict = field(default_factory=dict)


class SegmentContractValidator:
    """Validate evidence prerequisites without constructing a segment."""

    SUPPORTED_PROFILE_ID = "minimal_segment_contract_v1"
    SUPPORTED_PROFILE_VERSION = "0.2.0"
    SUPPORTED_STATUS = "CONTRACT_ONLY"
    SUPPORTED_PHASE1_PROFILE_ID = "minimal_strict_v1"
    SUPPORTED_PHASE1_BASELINE = "de1b7f589ebe3c2a41fa6501d793200a7b595426"
    SUPPORTED_MODE = "canonical_feature_sequence"
    SUPPORTED_CANDIDATE_IDENTITY_SCHEME = "content_window_v1"
    SUPPORTED_EVIDENCE_IDENTITY_SCHEME = "content_evidence_v1"
    SUPPORTED_FEATURE_SOURCE = "opposite_direction_strokes"
    REQUIRED_PROHIBITIONS = {
        "segment_engine_integration": True,
        "center_or_zhongshu": True,
        "czsc_or_chanpy_adapter": True,
        "trading_signal": True,
        "position_or_execution": True,
    }

    def __init__(self, profile: dict):
        if "segment" not in profile:
            raise SegmentContractError("segment contract profile wrapper is required")

        allowed_top_level = {
            "profile_id",
            "profile_version",
            "status",
            "phase1_profile_id",
            "phase1_baseline_commit",
            "segment",
            "prohibited_in_this_profile",
        }
        unexpected_top_level = set(profile) - allowed_top_level
        if unexpected_top_level:
            raise SegmentContractError(
                "unsupported top-level segment profile keys: "
                f"{sorted(unexpected_top_level)}"
            )

        top_level_required = {
            "profile_id": self.SUPPORTED_PROFILE_ID,
            "profile_version": self.SUPPORTED_PROFILE_VERSION,
            "status": self.SUPPORTED_STATUS,
            "phase1_profile_id": self.SUPPORTED_PHASE1_PROFILE_ID,
            "phase1_baseline_commit": self.SUPPORTED_PHASE1_BASELINE,
        }
        for key, expected in top_level_required.items():
            actual = profile.get(key)
            if actual != expected:
                raise SegmentContractError(
                    f"unsupported segment profile value: {key}={actual!r}; "
                    f"expected {expected!r}"
                )

        prohibited = profile.get("prohibited_in_this_profile")
        if not isinstance(prohibited, dict):
            raise SegmentContractError("prohibited_in_this_profile is required")
        for key, expected in self.REQUIRED_PROHIBITIONS.items():
            actual = prohibited.get(key)
            if actual is not expected:
                raise SegmentContractError(
                    f"Phase 2 prohibition must remain enabled: {key}={actual!r}"
                )

        segment = profile["segment"]
        if not isinstance(segment, dict):
            raise SegmentContractError(
                "segment contract section must be a mapping"
            )
        allowed_segment_keys = {
            "mode",
            "contract_only",
            "implementation_enabled",
            "minimum_stroke_count",
            "require_odd_stroke_count",
            "require_alternating_directions",
            "require_contiguous_endpoints",
            "require_strict_bar_order",
            "require_confirmed_for_confirmation",
            "allow_provisional_tail_for_candidate",
            "feature_sequence_source",
            "confirmation",
            "identity",
        }
        unexpected_segment_keys = set(segment) - allowed_segment_keys
        if unexpected_segment_keys:
            raise SegmentContractError(
                "unsupported segment contract keys: "
                f"{sorted(unexpected_segment_keys)}"
            )

        confirmation = segment.get("confirmation", {})
        if not isinstance(confirmation, dict):
            raise SegmentContractError(
                "segment confirmation contract section must be a mapping"
            )
        if set(confirmation) != {
            "require_explicit_destruction_evidence",
            "allow_implicit_confirmation",
        }:
            raise SegmentContractError(
                "unsupported segment confirmation contract keys"
            )

        identity = segment.get("identity", {})
        if not isinstance(identity, dict):
            raise SegmentContractError(
                "segment identity contract section must be a mapping"
            )
        if set(identity) != {"candidate_scheme", "evidence_scheme"}:
            raise SegmentContractError(
                "unsupported segment identity contract keys"
            )

        required = {
            "mode": self.SUPPORTED_MODE,
            "contract_only": True,
            "implementation_enabled": False,
            "minimum_stroke_count": 3,
            "require_odd_stroke_count": True,
            "require_alternating_directions": True,
            "require_contiguous_endpoints": True,
            "require_strict_bar_order": True,
            "require_confirmed_for_confirmation": True,
            "allow_provisional_tail_for_candidate": True,
            "feature_sequence_source": self.SUPPORTED_FEATURE_SOURCE,
        }
        for key, expected in required.items():
            actual = segment.get(key)
            if actual != expected:
                raise SegmentContractError(
                    f"unsupported segment contract value: {key}={actual!r}; "
                    f"expected {expected!r}"
                )

        if confirmation.get("require_explicit_destruction_evidence") is not True:
            raise SegmentContractError(
                "confirmed segments require explicit destruction evidence"
            )
        if confirmation.get("allow_implicit_confirmation") is not False:
            raise SegmentContractError("implicit segment confirmation is forbidden")
        if (
            identity.get("candidate_scheme")
            != self.SUPPORTED_CANDIDATE_IDENTITY_SCHEME
        ):
            raise SegmentContractError(
                "segment candidate identity must use content_window_v1"
            )
        if (
            identity.get("evidence_scheme")
            != self.SUPPORTED_EVIDENCE_IDENTITY_SCHEME
        ):
            raise SegmentContractError(
                "segment evidence identity must use content_evidence_v1"
            )

        self.profile_id = profile["profile_id"]
        self.profile_version = profile["profile_version"]
        self.minimum_stroke_count = 3

    def validate_candidate_window(
        self,
        strokes: Iterable[Stroke],
        *,
        target_status: StructureStatus = StructureStatus.PROVISIONAL,
        destruction_evidence: Iterable[Stroke] = (),
    ) -> SegmentContractResult:
        window = tuple(strokes)
        evidence = tuple(destruction_evidence)

        if target_status not in {
            StructureStatus.CANDIDATE,
            StructureStatus.PROVISIONAL,
            StructureStatus.CONFIRMED,
        }:
            return self._reject(
                "SEGMENT_TARGET_STATUS_UNSUPPORTED",
                target_status=str(target_status),
            )

        if target_status != StructureStatus.CONFIRMED and evidence:
            return self._reject(
                "SEGMENT_DESTRUCTION_EVIDENCE_NOT_ALLOWED_FOR_TARGET",
                target_status=target_status.value,
            )

        if len(window) < self.minimum_stroke_count:
            return self._reject(
                "SEGMENT_MINIMUM_STROKES",
                actual=len(window),
                required=self.minimum_stroke_count,
            )
        if len(window) % 2 == 0:
            return self._reject(
                "SEGMENT_ODD_STROKE_COUNT_REQUIRED",
                actual=len(window),
            )

        invalid = self._validate_records(window, evidence=False)
        if invalid is not None:
            return invalid
        invalid = self._validate_sequence(window, evidence=False)
        if invalid is not None:
            return invalid

        if window[0].direction != window[-1].direction:
            return self._reject("SEGMENT_FIRST_LAST_DIRECTION_MISMATCH")

        if target_status == StructureStatus.CONFIRMED:
            unconfirmed = [
                stroke.stroke_id
                for stroke in window
                if stroke.status != StructureStatus.CONFIRMED
            ]
            if unconfirmed:
                return self._reject(
                    "SEGMENT_UNCONFIRMED_WINDOW_STROKE",
                    stroke_ids=unconfirmed,
                )
        else:
            unconfirmed_positions = [
                index
                for index, stroke in enumerate(window)
                if stroke.status != StructureStatus.CONFIRMED
            ]
            if unconfirmed_positions and unconfirmed_positions != [len(window) - 1]:
                return self._reject(
                    "SEGMENT_ONLY_TAIL_MAY_BE_PROVISIONAL",
                    positions=unconfirmed_positions,
                )
            if (
                unconfirmed_positions
                and window[-1].status != StructureStatus.PROVISIONAL
            ):
                return self._reject(
                    "SEGMENT_TAIL_STATUS_INVALID",
                    status=window[-1].status.value,
                )

        feature_strokes = tuple(window[1::2])
        direction = window[0].direction
        candidate_key = self._candidate_key(window)

        earliest_confirmation_bar = None
        confirmation_evidence_key = ""
        if target_status == StructureStatus.CONFIRMED:
            if not evidence:
                return self._reject("SEGMENT_DESTRUCTION_EVIDENCE_REQUIRED")

            invalid = self._validate_records(evidence, evidence=True)
            if invalid is not None:
                return invalid

            window_logical_ids = {
                self._logical_identity(stroke) for stroke in window
            }
            for stroke in evidence:
                logical_id = self._logical_identity(stroke)
                if logical_id in window_logical_ids:
                    return self._reject(
                        "SEGMENT_DESTRUCTION_EVIDENCE_REUSES_WINDOW",
                        stroke_id=stroke.stroke_id,
                        logical_id=logical_id,
                    )
                if stroke.status != StructureStatus.CONFIRMED:
                    return self._reject(
                        "SEGMENT_DESTRUCTION_EVIDENCE_UNCONFIRMED",
                        stroke_id=stroke.stroke_id,
                    )

            if evidence[0].start_bar_index < window[-1].end_bar_index:
                return self._reject(
                    "SEGMENT_DESTRUCTION_EVIDENCE_TOO_EARLY",
                    stroke_id=evidence[0].stroke_id,
                    candidate_end=window[-1].end_bar_index,
                    evidence_start=evidence[0].start_bar_index,
                )

            invalid = self._validate_sequence(
                (window[-1],) + evidence,
                evidence=True,
            )
            if invalid is not None:
                return invalid

            earliest_confirmation_bar = evidence[-1].end_bar_index
            confirmation_evidence_key = self._confirmation_evidence_key(
                candidate_key,
                evidence,
            )

        return SegmentContractResult(
            accepted=True,
            reason_code="SEGMENT_CONTRACT_ELIGIBLE",
            candidate_key=candidate_key,
            confirmation_evidence_key=confirmation_evidence_key,
            direction=direction,
            feature_sequence_stroke_ids=tuple(
                stroke.stroke_id for stroke in feature_strokes
            ),
            earliest_confirmation_bar=earliest_confirmation_bar,
            detail={
                "stroke_count": len(window),
                "contract_only": True,
                "segment_constructed": False,
                "segment_confirmed": False,
                "target_status": target_status.value,
            },
        )

    def _validate_records(
        self,
        strokes: tuple[Stroke, ...],
        *,
        evidence: bool,
    ) -> SegmentContractResult | None:
        prefix = "SEGMENT_DESTRUCTION_EVIDENCE" if evidence else "SEGMENT"
        logical_ids: list[str] = []
        for stroke in strokes:
            if not stroke.stroke_id:
                return self._reject(
                    f"{prefix}_STROKE_ID_REQUIRED"
                )
            if not stroke.logical_id:
                return self._reject(
                    f"{prefix}_LOGICAL_ID_REQUIRED",
                    stroke_id=stroke.stroke_id,
                )
            logical_ids.append(stroke.logical_id)
            if stroke.start_bar_index < 0 or stroke.end_bar_index <= stroke.start_bar_index:
                return self._reject(
                    f"{prefix}_STROKE_BAR_RANGE_INVALID",
                    stroke_id=stroke.stroke_id,
                    start=stroke.start_bar_index,
                    end=stroke.end_bar_index,
                )

        if len(logical_ids) != len(set(logical_ids)):
            return self._reject(f"{prefix}_DUPLICATE_STROKE_ID")
        return None

    def _validate_sequence(
        self,
        strokes: tuple[Stroke, ...],
        *,
        evidence: bool,
    ) -> SegmentContractResult | None:
        prefix = "SEGMENT_DESTRUCTION_EVIDENCE" if evidence else "SEGMENT_STROKE"
        for previous, current in zip(strokes, strokes[1:]):
            if previous.direction == current.direction:
                return self._reject(
                    f"{prefix}_DIRECTION_NOT_ALTERNATING",
                    previous=previous.stroke_id,
                    current=current.stroke_id,
                )
            if (
                previous.end_fractal_id != current.start_fractal_id
                or previous.end_bar_index != current.start_bar_index
            ):
                return self._reject(
                    f"{prefix}_ENDPOINT_NOT_CONTIGUOUS",
                    previous=previous.stroke_id,
                    current=current.stroke_id,
                )
            if current.end_bar_index <= previous.end_bar_index:
                return self._reject(
                    f"{prefix}_BAR_ORDER_INVALID",
                    previous=previous.stroke_id,
                    current=current.stroke_id,
                )
        return None

    def _candidate_key(self, window: tuple[Stroke, ...]) -> str:
        parts = [
            self.profile_id,
            self.profile_version,
            self.SUPPORTED_MODE,
            self.SUPPORTED_CANDIDATE_IDENTITY_SCHEME,
        ]
        parts.extend(self._stroke_content_identity(stroke) for stroke in window)
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]

    def _confirmation_evidence_key(
        self,
        candidate_key: str,
        evidence: tuple[Stroke, ...],
    ) -> str:
        parts = [
            candidate_key,
            self.SUPPORTED_EVIDENCE_IDENTITY_SCHEME,
        ]
        parts.extend(self._stroke_content_identity(stroke) for stroke in evidence)
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]

    @staticmethod
    def _logical_identity(stroke: Stroke) -> str:
        return stroke.logical_id

    @classmethod
    def _stroke_content_identity(cls, stroke: Stroke) -> str:
        return f"{cls._logical_identity(stroke)}:{stroke.content_hash()}"

    @staticmethod
    def _reject(reason_code: str, **detail) -> SegmentContractResult:
        return SegmentContractResult(
            accepted=False,
            reason_code=reason_code,
            detail=detail,
        )
