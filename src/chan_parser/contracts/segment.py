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
    direction: StrokeDirection | None = None
    feature_sequence_stroke_ids: tuple[str, ...] = ()
    earliest_confirmation_bar: int | None = None
    detail: dict = field(default_factory=dict)


class SegmentContractValidator:
    """Validate evidence prerequisites without constructing a segment."""

    SUPPORTED_MODE = "canonical_feature_sequence"
    SUPPORTED_IDENTITY_SCHEME = "content_evidence_v1"
    SUPPORTED_FEATURE_SOURCE = "opposite_direction_strokes"

    def __init__(self, profile: dict):
        segment = profile.get("segment", profile)
        confirmation = segment.get("confirmation", {})
        identity = segment.get("identity", {})

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
        if identity.get("scheme") != self.SUPPORTED_IDENTITY_SCHEME:
            raise SegmentContractError(
                "segment identity must use content_evidence_v1"
            )

        self.profile_id = profile.get(
            "profile_id", "minimal_segment_contract_v1"
        )
        self.profile_version = profile.get("profile_version", "0.1.0")
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

        ids = [stroke.object_id or stroke.stroke_id for stroke in window]
        if len(ids) != len(set(ids)):
            return self._reject("SEGMENT_DUPLICATE_STROKE_ID")

        for stroke in window:
            if stroke.start_bar_index < 0 or stroke.end_bar_index <= stroke.start_bar_index:
                return self._reject(
                    "SEGMENT_STROKE_BAR_RANGE_INVALID",
                    stroke_id=stroke.stroke_id,
                    start=stroke.start_bar_index,
                    end=stroke.end_bar_index,
                )

        for previous, current in zip(window, window[1:]):
            if previous.direction == current.direction:
                return self._reject(
                    "SEGMENT_STROKE_DIRECTION_NOT_ALTERNATING",
                    previous=previous.stroke_id,
                    current=current.stroke_id,
                )
            if (
                previous.end_fractal_id != current.start_fractal_id
                or previous.end_bar_index != current.start_bar_index
            ):
                return self._reject(
                    "SEGMENT_ENDPOINT_NOT_CONTIGUOUS",
                    previous=previous.stroke_id,
                    current=current.stroke_id,
                )
            if current.end_bar_index <= previous.end_bar_index:
                return self._reject(
                    "SEGMENT_BAR_ORDER_INVALID",
                    previous=previous.stroke_id,
                    current=current.stroke_id,
                )

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

        earliest_confirmation_bar = None
        if target_status == StructureStatus.CONFIRMED:
            if not evidence:
                return self._reject(
                    "SEGMENT_DESTRUCTION_EVIDENCE_REQUIRED"
                )
            window_ids = {stroke.object_id or stroke.stroke_id for stroke in window}
            for stroke in evidence:
                evidence_id = stroke.object_id or stroke.stroke_id
                if evidence_id in window_ids:
                    return self._reject(
                        "SEGMENT_DESTRUCTION_EVIDENCE_REUSES_WINDOW",
                        stroke_id=stroke.stroke_id,
                    )
                if stroke.status != StructureStatus.CONFIRMED:
                    return self._reject(
                        "SEGMENT_DESTRUCTION_EVIDENCE_UNCONFIRMED",
                        stroke_id=stroke.stroke_id,
                    )
                if stroke.start_bar_index < window[-1].end_bar_index:
                    return self._reject(
                        "SEGMENT_DESTRUCTION_EVIDENCE_TOO_EARLY",
                        stroke_id=stroke.stroke_id,
                        candidate_end=window[-1].end_bar_index,
                        evidence_start=stroke.start_bar_index,
                    )
            earliest_confirmation_bar = max(
                stroke.end_bar_index for stroke in evidence
            )

        key = self._candidate_key(window, evidence)
        return SegmentContractResult(
            accepted=True,
            reason_code="SEGMENT_CONTRACT_ELIGIBLE",
            candidate_key=key,
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
            },
        )

    def _candidate_key(
        self,
        window: tuple[Stroke, ...],
        evidence: tuple[Stroke, ...],
    ) -> str:
        parts = [
            self.profile_id,
            self.profile_version,
            self.SUPPORTED_MODE,
        ]
        parts.extend(self._stroke_identity(stroke) for stroke in window)
        parts.append("destruction-evidence")
        parts.extend(self._stroke_identity(stroke) for stroke in evidence)
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]

    @staticmethod
    def _stroke_identity(stroke: Stroke) -> str:
        logical = stroke.logical_id or stroke.stroke_id
        return f"{logical}:{stroke.content_hash()}"

    @staticmethod
    def _reject(reason_code: str, **detail) -> SegmentContractResult:
        return SegmentContractResult(
            accepted=False,
            reason_code=reason_code,
            detail=detail,
        )
