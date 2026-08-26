"""Producer-bound SegmentEngine evaluation context.

This adapter coordinates the existing source-evidence binder and canonical
engine for one normalized source.  The envelope is self-describing evidence,
not a security capability or temporal replay mechanism.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..contracts.segment_incremental_source_continuity import (
    SegmentIncrementalSourceContinuityError,
    SegmentIncrementalSourceStrokeBinding,
    bind_incremental_segment_source_strokes,
)
from ..domain.stroke import Stroke
from ..engine.segment import SegmentEngine, SegmentEngineResult


__all__ = (
    "SegmentEngineEvaluationEnvelope",
    "evaluate_segment_engine_with_source_context",
)


@dataclass(frozen=True)
class SegmentEngineEvaluationEnvelope:
    """The result and canonical context produced by one evaluation call."""

    result: SegmentEngineResult
    current_source_binding: tuple[SegmentIncrementalSourceStrokeBinding, ...]
    sequence_id: str
    engine_profile_id: str
    engine_profile_version: str
    canonical_rules_profile_id: str
    canonical_rules_profile_version: str
    canonical_rules_baseline_commit: str

    def __post_init__(self) -> None:
        if type(self.result) is not SegmentEngineResult:
            raise ValueError("result must be SegmentEngineResult")
        if type(self.current_source_binding) is not tuple:
            raise ValueError("current_source_binding must be tuple")
        if not self.current_source_binding:
            raise ValueError("current_source_binding must not be empty")
        if any(
            type(binding) is not SegmentIncrementalSourceStrokeBinding
            for binding in self.current_source_binding
        ):
            raise ValueError("current_source_binding contains invalid binding")
        if type(self.sequence_id) is not str or not self.sequence_id:
            raise ValueError("sequence_id must be a nonempty str")

        profile_values = (
            self.engine_profile_id,
            self.engine_profile_version,
            self.canonical_rules_profile_id,
            self.canonical_rules_profile_version,
            self.canonical_rules_baseline_commit,
        )
        if any(type(value) is not str or not value for value in profile_values):
            raise ValueError("profile fields must be nonempty str values")
        expected_profile = (
            SegmentEngine.PROFILE_ID,
            SegmentEngine.PROFILE_VERSION,
            SegmentEngine.CANONICAL_PROFILE_ID,
            SegmentEngine.CANONICAL_PROFILE_VERSION,
            SegmentEngine.CANONICAL_BASELINE,
        )
        if profile_values != expected_profile:
            raise ValueError("profile fields do not match canonical engine")


def evaluate_segment_engine_with_source_context(
    strokes: Sequence[Stroke],
    *,
    sequence_id: str,
) -> SegmentEngineEvaluationEnvelope:
    """Evaluate one source and bind its result to the exact source context."""

    if isinstance(strokes, (str, bytes, bytearray)) or not isinstance(
        strokes, Sequence
    ):
        raise SegmentIncrementalSourceContinuityError(
            "SEGMENT_SOURCE_BINDING_REQUIRED"
        )

    source = tuple(strokes)
    current_source_binding = bind_incremental_segment_source_strokes(source)
    engine = SegmentEngine(SegmentEngine.reference_profile())
    result = engine.process_primary(source, sequence_id=sequence_id)
    return SegmentEngineEvaluationEnvelope(
        result=result,
        current_source_binding=current_source_binding,
        sequence_id=sequence_id,
        engine_profile_id=SegmentEngine.PROFILE_ID,
        engine_profile_version=SegmentEngine.PROFILE_VERSION,
        canonical_rules_profile_id=SegmentEngine.CANONICAL_PROFILE_ID,
        canonical_rules_profile_version=SegmentEngine.CANONICAL_PROFILE_VERSION,
        canonical_rules_baseline_commit=SegmentEngine.CANONICAL_BASELINE,
    )
