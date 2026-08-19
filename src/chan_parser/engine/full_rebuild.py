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
from .segment import SegmentEngine
from .segment_lifecycle_emitter import SegmentLifecycleEmitter
from .stroke import StrokeEngine


class FullRebuildSegmentReferenceError(ValueError):
    """Raised when the opt-in Segment reference replay cannot be trusted."""


class FullRebuildEngine:
    _SEGMENT_REASONS = frozenset({
        "SEGMENT_FEATURE_WINDOW_INCOMPLETE",
        "SEGMENT_PRIMARY_FRACTAL_NOT_FOUND",
        "SEGMENT_SECOND_CASE_PENDING",
        "SEGMENT_FIRST_CASE_CONFIRMED",
    })

    def __init__(self, profile: dict, *, segment_engine_profile=None,
                 segment_lifecycle_profile=None):
        if (segment_engine_profile is None) != (segment_lifecycle_profile is None):
            raise FullRebuildSegmentReferenceError(
                "segment_engine_profile and segment_lifecycle_profile must be provided together"
            )
        self.profile = profile
        self.inclusion_engine = InclusionEngine(profile.get("inclusion", {}))
        self.fractal_engine = FractalEngine(profile.get("fractal", {}))
        self.stroke_engine = StrokeEngine(profile.get("stroke", {}))
        self.segment_reference_enabled = segment_engine_profile is not None
        self.segment_engine = (
            SegmentEngine(segment_engine_profile) if self.segment_reference_enabled else None
        )
        self.segment_lifecycle_emitter = (
            SegmentLifecycleEmitter(segment_lifecycle_profile)
            if self.segment_reference_enabled else None
        )
        self.engine_version = "0.3.0" if self.segment_reference_enabled else "0.2.0"

    def process(self, raw_bars: list[RawBar]) -> dict[str, Any]:
        valid = [b for b in raw_bars if b.is_valid]
        merged, inc_events = self.inclusion_engine.process(valid)
        fractals, fx_events = self.fractal_engine.process(merged, len(raw_bars))
        strokes, st_events = self.stroke_engine.process(fractals, merged, len(raw_bars))
        log = EventLog(); log.record_many(inc_events + fx_events + st_events)
        quality = self._data_quality(raw_bars)
        segments = []
        segment_event_count = 0
        segment_tail_reason = ""
        segment_tail_start = ""
        segment_source_confirmed_count = 0
        segment_source_consumed_count = 0
        if self.segment_reference_enabled:
            segments, segment_event_count, segment_tail_reason, segment_tail_start, \
                segment_source_confirmed_count, segment_source_consumed_count = (
                    self._replay_segments(strokes, log)
                )
        meta = {"symbol": "", "bar_frequency": "", "adjustment": "qfq",
                     "profile_id": self.profile.get("profile_id", "minimal_strict_v1"),
                     "engine_version": self.engine_version, "analysis_mode": "close_only",
                     "calculation_mode": "full_rebuild"}
        structures = {"merged_bars": [x.to_dict() for x in merged],
                      "fractals": [x.to_dict() for x in fractals],
                      "strokes": [x.to_dict() for x in strokes]}
        runtime_state = {"last_processed_bar_id": valid[-1].bar_id if valid else "",
                         "unfinished_fractal_count": sum(x.status != StructureStatus.CONFIRMED for x in fractals),
                         "unfinished_stroke_count": sum(x.status != StructureStatus.CONFIRMED for x in strokes)}
        audit = {"input_sha256": self._input_hash(raw_bars),
                 "event_log_sha256": log.compute_sha256(),
                 "output_sha256": self._structure_hash(merged, fractals, strokes),
                 "event_count": len(log)}
        if self.segment_reference_enabled:
            segment_structure_key = "".join(("seg", "ments"))
            structures[segment_structure_key] = [x.to_dict() for x in segments]
            runtime_state.update({
                "confirmed_segment_count": len(segments),
                "unfinished_segment_count": 1 if segment_tail_reason else 0,
                "segment_tail_reason_code": segment_tail_reason,
                "segment_tail_start_stroke_id": segment_tail_start,
                "segment_source_confirmed_stroke_count": segment_source_confirmed_count,
                "segment_source_consumed_stroke_count": segment_source_consumed_count,
            })
            meta.update({
                "segment_reference_mode": "R1_FIRST_CASE_VISIBILITY_REPLAY",
                "segment_engine_profile_id": self.segment_engine.profile_id,
                "segment_lifecycle_profile_id": self.segment_lifecycle_emitter.profile_id,
            })
            audit.update({
                "segment_event_count": segment_event_count,
                "output_sha256": self._structure_hash(merged, fractals, strokes, segments),
            })
        return {
            "meta": meta,
            "data_quality": quality,
            "structures": structures,
            "runtime_state": runtime_state,
            "audit": audit,
            "events": log.to_list(),
        }

    @classmethod
    def _confirmed_prefix(cls, strokes):
        confirmed = []
        tail_started = False
        previous_bar = None
        for stroke in strokes:
            if type(stroke.status) is not StructureStatus:
                raise FullRebuildSegmentReferenceError("stroke status must be StructureStatus")
            is_confirmed = stroke.status == StructureStatus.CONFIRMED
            if not is_confirmed:
                tail_started = True
                continue
            if tail_started:
                raise FullRebuildSegmentReferenceError(
                    "confirmed strokes must form an ordered prefix"
                )
            if type(stroke.confirmed_at_bar) is not int or stroke.confirmed_at_bar < stroke.end_bar_index:
                raise FullRebuildSegmentReferenceError("invalid confirmed stroke visibility")
            if previous_bar is not None and stroke.confirmed_at_bar < previous_bar:
                raise FullRebuildSegmentReferenceError("stroke confirmation visibility regressed")
            previous_bar = stroke.confirmed_at_bar
            confirmed.append(stroke)
        return confirmed

    def _replay_segments(self, strokes, event_log):
        confirmed = self._confirmed_prefix(strokes)
        if not confirmed:
            return [], 0, "", "", 0, 0
        segments = []
        cursor = 0
        tail_reason = ""
        tail_start = ""
        segment_event_count = 0
        watermarks = []
        for stroke in confirmed:
            if not watermarks or watermarks[-1] != stroke.confirmed_at_bar:
                watermarks.append(stroke.confirmed_at_bar)
        for watermark in watermarks:
            available_end = sum(stroke.confirmed_at_bar <= watermark for stroke in confirmed)
            if available_end <= cursor:
                continue
            while cursor < available_end:
                source = confirmed[cursor:available_end]
                sequence_id = f"segment-primary:{source[0].logical_id}"
                result = self.segment_engine.process_primary(source, sequence_id=sequence_id)
                if result.reason_code not in self._SEGMENT_REASONS:
                    raise FullRebuildSegmentReferenceError(
                        f"unknown Segment outcome: {result.reason_code}"
                    )
                if result.reason_code != "SEGMENT_FIRST_CASE_CONFIRMED":
                    tail_reason = result.reason_code
                    tail_start = source[0].stroke_id
                    break
                segment = result.segment
                if segment is None or segment.confirmed_at_bar != watermark:
                    raise FullRebuildSegmentReferenceError(
                        "FULL_REBUILD_SEGMENT_BACKFILL_FORBIDDEN"
                    )
                matches = [i for i, stroke in enumerate(confirmed)
                           if stroke.stroke_id == segment.end_stroke_id]
                if len(matches) != 1 or matches[0] < cursor or matches[0] >= available_end:
                    raise FullRebuildSegmentReferenceError("segment boundary is not uniquely visible")
                if segments:
                    previous = segments[-1]
                    if previous.direction == segment.direction:
                        raise FullRebuildSegmentReferenceError("segment directions do not alternate")
                    if (previous.end_bar_index != segment.start_bar_index or
                            previous.end_price != segment.start_price):
                        raise FullRebuildSegmentReferenceError("segment chain is not continuous")
                emitted = self.segment_lifecycle_emitter.emit(
                    result=result, source_strokes=source, event_log=event_log
                )
                if len(emitted) != 2:
                    raise FullRebuildSegmentReferenceError("first-case emission must contain two events")
                segments.append(segment)
                segment_event_count += len(emitted)
                cursor = matches[0] + 1
                tail_reason = ""
                tail_start = ""
            if tail_reason == "SEGMENT_SECOND_CASE_PENDING":
                break
        if tail_reason and cursor >= len(confirmed):
            tail_reason = ""
            tail_start = ""
        return segments, segment_event_count, tail_reason, tail_start, len(confirmed), cursor

    @staticmethod
    def _input_hash(bars: list[RawBar]) -> str:
        return hashlib.sha256("|".join(b.content_hash() for b in bars).encode()).hexdigest()[:16]

    @staticmethod
    def _structure_hash(merged, fractals, strokes, segments=None) -> str:
        payload = [x.content_hash() for x in merged]
        payload += [x.content_hash() for x in fractals]
        payload += [x.content_hash() for x in strokes]
        if segments is not None:
            payload += [x.content_hash() for x in segments]
        return hashlib.sha256("|".join(payload).encode()).hexdigest()[:16]

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
