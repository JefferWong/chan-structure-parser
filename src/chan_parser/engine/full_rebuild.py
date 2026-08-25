"""独立全量重建基准引擎。"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from ..audit.event_log import EventLog
from ..domain.lifecycle import StructureStatus
from ..domain.raw_bar import RawBar
from .fractal import FractalEngine
from .inclusion import InclusionEngine
from .stroke import StrokeEngine
from .segment import SegmentEngine, SegmentEngineCoreError
from .segment_lifecycle_emitter import SegmentLifecycleEmitter


_INCOMPLETE_REFERENCE_TAIL_REASONS = frozenset({
    "SEGMENT_FEATURE_INCLUSION_UNSEEDED",
})


class FullRebuildEngine:
    def __init__(
        self,
        profile: dict,
        *,
        segment_reference_enabled: bool = False,
        segment_lifecycle_emission_enabled: bool = False,
    ):
        self.profile = profile
        if type(segment_reference_enabled) is not bool:
            raise TypeError("segment_reference_enabled must be a bool")
        if type(segment_lifecycle_emission_enabled) is not bool:
            raise TypeError("segment_lifecycle_emission_enabled must be a bool")
        if segment_lifecycle_emission_enabled and not segment_reference_enabled:
            raise SegmentEngineCoreError(
                "SEGMENT_LIFECYCLE_REQUIRES_REFERENCE"
            )
        self.segment_reference_enabled = segment_reference_enabled
        self.segment_lifecycle_emission_enabled = segment_lifecycle_emission_enabled
        self.inclusion_engine = InclusionEngine(profile.get("inclusion", {}))
        self.fractal_engine = FractalEngine(profile.get("fractal", {}))
        self.stroke_engine = StrokeEngine(profile.get("stroke", {}))
        self.engine_version = "0.2.0"
        self._last_raw_replay_watermark: int | None = None
        self._raw_replay_segment_confirmations: dict[tuple[str, str], int] = {}

    def process(
        self,
        raw_bars: list[RawBar],
        *,
        raw_watermark: int | None = None,
    ) -> dict[str, Any]:
        self._validate_raw_watermark(raw_watermark)
        if raw_watermark is not None and not self.segment_reference_enabled:
            raise SegmentEngineCoreError(
                "SEGMENT_RAW_REPLAY_REQUIRES_REFERENCE"
            )
        if self.segment_lifecycle_emission_enabled and raw_watermark is not None:
            raise SegmentEngineCoreError(
                "SEGMENT_LIFECYCLE_RAW_REPLAY_NOT_INTEGRATED"
            )
        valid = [b for b in raw_bars if b.is_valid]
        merged, inc_events = self.inclusion_engine.process(valid)
        fractals, fx_events = self.fractal_engine.process(merged, len(raw_bars))
        strokes, st_events = self.stroke_engine.process(fractals, merged, len(raw_bars))
        log = EventLog()
        log.record_many(inc_events + fx_events + st_events)
        reference_segments = []
        reference_segment_objects = []
        reference_result = None
        reference_source = ()
        if self.segment_reference_enabled:
            reference_data = self._reference_segments(
                strokes,
                raw_watermark=raw_watermark,
                include_result=self.segment_lifecycle_emission_enabled,
            )
            if self.segment_lifecycle_emission_enabled:
                (
                    reference_segment_objects,
                    reference_segments,
                    reference_result,
                    reference_source,
                ) = reference_data
            else:
                reference_segment_objects, reference_segments = reference_data
        if self.segment_lifecycle_emission_enabled and reference_result is not None:
            SegmentLifecycleEmitter(
                SegmentLifecycleEmitter.reference_profile()
            ).emit(
                result=reference_result,
                source_strokes=reference_source,
                event_log=log,
            )
        quality = self._data_quality(raw_bars)
        structures = {"merged_bars": [x.to_dict() for x in merged],
                      "fractals": [x.to_dict() for x in fractals],
                      "strokes": [x.to_dict() for x in strokes]}
        if self.segment_reference_enabled:
            structures["segments"] = reference_segments
        structure_hash = self._structure_hash(
            merged,
            fractals,
            strokes,
            segments=(
                reference_segment_objects
                if self.segment_reference_enabled
                else ()
            ),
        )
        return {
            "meta": {"symbol": "", "bar_frequency": "", "adjustment": "qfq",
                     "profile_id": self.profile.get("profile_id", "minimal_strict_v1"),
                     "engine_version": self.engine_version, "analysis_mode": "close_only",
                     "calculation_mode": "full_rebuild"},
            "data_quality": quality,
            "structures": structures,
            "runtime_state": {"last_processed_bar_id": valid[-1].bar_id if valid else "",
                              "unfinished_fractal_count": sum(x.status != StructureStatus.CONFIRMED for x in fractals),
                              "unfinished_stroke_count": sum(x.status != StructureStatus.CONFIRMED for x in strokes)},
            "audit": {"input_sha256": self._input_hash(raw_bars),
                      "event_log_sha256": log.compute_sha256(),
                      "output_sha256": structure_hash,
                      "event_count": len(log)},
            "events": log.to_list(),
        }

    @staticmethod
    def _input_hash(bars: list[RawBar]) -> str:
        return hashlib.sha256("|".join(b.content_hash() for b in bars).encode()).hexdigest()[:16]

    @staticmethod
    def _structure_hash(merged, fractals, strokes, segments=()) -> str:
        payload = [x.content_hash() for x in merged]
        payload += [x.content_hash() for x in fractals]
        payload += [x.content_hash() for x in strokes]
        payload += [x.content_hash() for x in segments]
        return hashlib.sha256("|".join(payload).encode()).hexdigest()[:16]

    @staticmethod
    def _reference_segment_dict(segment) -> dict[str, Any]:
        """Serialize reference evidence without changing the legacy Segment schema."""
        payload = segment.to_dict()
        payload["created_at_raw_bar_index"] = segment.created_at_raw_bar_index
        payload["confirmed_at_raw_bar_index"] = segment.confirmed_at_raw_bar_index
        return payload

    @staticmethod
    def _validate_raw_watermark(raw_watermark: int | None) -> None:
        if raw_watermark is not None and (
            type(raw_watermark) is not int or raw_watermark < 0
        ):
            raise SegmentEngineCoreError(
                "SEGMENT_RAW_REPLAY_VISIBILITY_INVALID"
            )

    def _reference_segments(
        self,
        strokes,
        *,
        raw_watermark: int | None,
        include_result: bool = False,
    ):
        result = self._reference_evaluation(strokes, raw_watermark=raw_watermark)
        if include_result:
            return result
        return result[:2]

    def _reference_evaluation(
        self,
        strokes,
        *,
        raw_watermark: int | None,
    ) -> tuple[list, list[dict[str, Any]], Any, tuple]:
        confirmed = [
            stroke for stroke in strokes
            if stroke.status == StructureStatus.CONFIRMED
        ]
        if raw_watermark is not None:
            self._validate_raw_replay_strokes(confirmed)
            confirmed = [
                stroke for stroke in confirmed
                if stroke.confirmed_at_raw_bar_index <= raw_watermark
            ]
        if not confirmed:
            self._record_raw_replay_watermark(raw_watermark, ())
            return [], [], None, ()

        try:
            result = SegmentEngine(SegmentEngine.reference_profile()).process_primary(
                confirmed,
                sequence_id="full_rebuild:primary",
            )
        except SegmentEngineCoreError as error:
            # Unsupported reference evidence remains a non-materialized tail.
            if error.reason_code not in _INCOMPLETE_REFERENCE_TAIL_REASONS:
                raise
            self._record_raw_replay_watermark(raw_watermark, ())
            return [], [], None, ()
        if not result.completed or result.segment is None:
            self._record_raw_replay_watermark(raw_watermark, ())
            return [], [], result, tuple(confirmed)

        segment = result.segment
        if raw_watermark is not None:
            by_stroke_id = {stroke.stroke_id: stroke for stroke in confirmed}
            source = [by_stroke_id.get(stroke_id) for stroke_id in segment.stroke_ids]
            if (
                any(stroke is None for stroke in source)
                or type(segment.confirmed_at_raw_bar_index) is not int
                or segment.confirmed_at_raw_bar_index < 0
                or segment.confirmed_at_raw_bar_index > raw_watermark
                or segment.confirmed_at_raw_bar_index < max(
                    stroke.confirmed_at_raw_bar_index for stroke in source
                )
            ):
                raise SegmentEngineCoreError(
                    "SEGMENT_RAW_REPLAY_VISIBILITY_INVALID"
                )
        self._record_raw_replay_watermark(raw_watermark, (segment,))
        return [segment], [self._reference_segment_dict(segment)], result, tuple(confirmed)

    @staticmethod
    def _validate_raw_replay_strokes(strokes) -> None:
        previous = -1
        for stroke in strokes:
            created = stroke.created_at_raw_bar_index
            confirmed = stroke.confirmed_at_raw_bar_index
            if (
                type(created) is not int
                or type(confirmed) is not int
                or created < 0
                or confirmed < 0
                or confirmed < created
                or confirmed < previous
            ):
                raise SegmentEngineCoreError(
                    "SEGMENT_RAW_REPLAY_VISIBILITY_INVALID"
                )
            previous = confirmed

    def _record_raw_replay_watermark(self, raw_watermark, segments) -> None:
        if raw_watermark is None:
            return
        if (
            self._last_raw_replay_watermark is not None
            and raw_watermark < self._last_raw_replay_watermark
        ):
            raise SegmentEngineCoreError(
                "SEGMENT_RAW_REPLAY_VISIBILITY_INVALID"
            )
        for segment in segments:
            key = (segment.segment_id, segment.logical_id)
            previous = self._raw_replay_segment_confirmations.get(key)
            current = segment.confirmed_at_raw_bar_index
            if previous is not None and current < previous:
                raise SegmentEngineCoreError(
                    "SEGMENT_RAW_REPLAY_VISIBILITY_INVALID"
                )
            self._raw_replay_segment_confirmations[key] = current
        self._last_raw_replay_watermark = raw_watermark

    @staticmethod
    def _data_quality(bars: list[RawBar]) -> dict:
        timestamps = [b.timestamp for b in bars]
        duplicate_count = len(timestamps) - len(set(timestamps))
        monotonic = all(a < b for a, b in zip(timestamps, timestamps[1:]))
        invalid = sum(not b.is_valid for b in bars)
        status = "OK" if duplicate_count == 0 and monotonic and invalid == 0 else "WARNING"
        return {"raw_bar_count": len(bars), "valid_bar_count": len(bars) - invalid,
                "duplicate_count": duplicate_count, "missing_interval_count": 0,
                "monotonic_timestamp": monotonic, "status": status}
