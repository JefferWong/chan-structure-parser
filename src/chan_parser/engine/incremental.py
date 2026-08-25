"""独立增量路径：冻结前缀、真实有界尾部重算、检查点恢复。"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from ..audit.event_log import EventLog
from ..domain.lifecycle import EventType, LifecycleEvent, StructureStatus, TrendDirection
from ..domain.raw_bar import RawBar
from .fractal import FractalEngine
from .inclusion import InclusionEngine
from .segment import SegmentEngine, SegmentEngineResult
from .stroke import StrokeEngine


class RebuildBoundaryExceeded(RuntimeError):
    """安全重启点早于允许的有界窗口时 fail-closed。"""


@dataclass
class Checkpoint:
    raw_bars: list
    merged_bars: list
    fractals: list
    strokes: list
    event_snapshot: tuple
    raw_bar_count: int
    historical_snapshot: dict
    rebuild_count: int
    last_rebuild: dict
    last_engine_inputs: dict
    max_engine_inputs: dict
    sha256: str


class IncrementalEngine:
    def __init__(self, profile: dict, *, segment_reference_enabled: bool = False):
        self.profile = profile
        if type(segment_reference_enabled) is not bool:
            raise TypeError("segment_reference_enabled must be a bool")
        self.segment_reference_enabled = segment_reference_enabled
        self.inclusion_engine = InclusionEngine(profile.get("inclusion", {}))
        self.fractal_engine = FractalEngine(profile.get("fractal", {}))
        self.stroke_engine = StrokeEngine(profile.get("stroke", {}))
        runtime = profile.get("runtime", {})
        self.max_rebuild_distance = runtime.get("max_rebuild_distance", 200)
        self.checkpoint_interval = runtime.get("checkpoint_interval", 50)
        self.snapshot_retention = max(1, int(runtime.get("snapshot_retention", 20)))
        self.checkpoint_retention = max(1, int(runtime.get("checkpoint_retention", 10)))
        self.engine_version = "0.4.0"
        self._raw_bars: list[RawBar] = []
        self._merged_bars = []
        self._fractals = []
        self._strokes = []
        self._event_log = EventLog()
        self._checkpoints: dict[int, Checkpoint] = {}
        self._next_checkpoint_id = 0
        self._historical_snapshots: dict[int, dict] = {}
        self._rebuild_count = 0
        self._last_rebuild = {
            "from": None,
            "to": None,
            "affected_objects": [],
            "frozen_prefix": {"merged_bars": 0, "fractals": 0, "strokes": 0},
        }
        self._last_engine_inputs = self._empty_engine_metrics()
        self._max_engine_inputs = self._empty_engine_metrics()
        self._segment_reference_result: SegmentEngineResult | None = None
        self._segment_reference_source_strokes = ()

    def append_one(self, raw_bar: RawBar) -> dict[str, Any]:
        return self.append_batch([raw_bar])

    def append_batch(self, new_bars: list[RawBar]) -> dict[str, Any]:
        if not new_bars:
            return self.get_current_state()
        self._validate_append(new_bars)
        combined = self._raw_bars + list(new_bars)

        if not self._raw_bars or not self._merged_bars:
            self._bootstrap(combined)
        else:
            self._bounded_reconcile(combined, len(new_bars))

        if self.segment_reference_enabled:
            self._evaluate_segment_reference()
        self._store_historical_snapshot(len(self._raw_bars), self._snapshot_payload())
        if self.checkpoint_interval and len(self._raw_bars) % self.checkpoint_interval == 0:
            self.create_checkpoint()
        return self.get_current_state()

    def _bootstrap(self, combined: list[RawBar]) -> None:
        valid = [b for b in combined if b.is_valid]
        merged, inc_events = self.inclusion_engine.process(valid)
        fractals, fx_events = self.fractal_engine.process(merged, len(combined))
        strokes, st_events = self.stroke_engine.process(fractals, merged, len(combined))
        self._raw_bars = combined
        self._merged_bars, self._fractals, self._strokes = merged, fractals, strokes
        self._event_log.record_many(inc_events + fx_events + st_events)
        self._last_engine_inputs = {
            "inclusion_raw_bars": len(valid),
            "fractal_merged_bars": len(merged),
            "stroke_fractals": len(fractals),
            "stroke_merged_bars": len(merged),
        }
        self._update_max_engine_inputs()
        self._last_rebuild = {
            "from": 0,
            "to": len(combined) - 1,
            "affected_objects": self._active_ids(),
            "frozen_prefix": {"merged_bars": 0, "fractals": 0, "strokes": 0},
        }

    def _bounded_reconcile(self, combined: list[RawBar], appended_count: int) -> None:
        allowed_window = self.max_rebuild_distance + appended_count
        old_state = self._object_maps()

        # 1) 包含处理只重算旧的最后一根合并K线及新数据。
        raw_index_by_id = {bar.bar_id: idx for idx, bar in enumerate(combined)}
        mutable_last = self._merged_bars[-1]
        source_indices = [raw_index_by_id[x] for x in mutable_last.source_raw_bar_ids if x in raw_index_by_id]
        inclusion_raw_start = min(source_indices) if source_indices else len(self._raw_bars) - 1
        frozen_merged = copy.deepcopy(self._merged_bars[:-1])
        inclusion_tail = [b for b in combined[inclusion_raw_start:] if b.is_valid]
        self._guard_window("inclusion_raw_bars", len(inclusion_tail), allowed_window)
        initial_direction = self._parse_direction(mutable_last.merge_direction)
        tail_merged, inc_events = self.inclusion_engine.process(
            inclusion_tail,
            initial_direction=initial_direction,
            index_offset=len(frozen_merged),
        )
        candidate_merged = frozen_merged + tail_merged
        self._reuse_identities("merged_bar", candidate_merged, old_state)

        # 2) 分型从最后一个已确认分型的左窗口开始；更早分型冻结。
        last_confirmed_fx = self._last_confirmed(self._fractals)
        if last_confirmed_fx is None:
            fractal_merged_start = 0
            frozen_fractals = []
        else:
            fractal_merged_start = max(0, last_confirmed_fx.merged_bar_index - 1)
            frozen_fractals = copy.deepcopy([
                fx for fx in self._fractals
                if fx.merged_bar_index < fractal_merged_start + 1
            ])
        fractal_window = candidate_merged[fractal_merged_start:]
        self._guard_window("fractal_merged_bars", len(fractal_window), allowed_window)
        tail_fractals, fx_events = self.fractal_engine.process(
            fractal_window,
            len(combined),
            id_offset=self._max_numeric_id(frozen_fractals, "fractal_id"),
        )
        candidate_fractals = frozen_fractals + tail_fractals
        self._reuse_identities("fractal", candidate_fractals, old_state)

        # 3) 笔从最后一根已确认笔的起点重算；更早笔冻结。
        last_confirmed_stroke = self._last_confirmed(self._strokes)
        if last_confirmed_stroke is None:
            frozen_strokes = []
            stroke_merged_start = (
                candidate_fractals[0].merged_bar_index if candidate_fractals else len(candidate_merged)
            )
        else:
            last_idx = self._strokes.index(last_confirmed_stroke)
            frozen_strokes = copy.deepcopy(self._strokes[:last_idx])
            stroke_merged_start = last_confirmed_stroke.start_bar_index
        stroke_fractals = [
            fx for fx in candidate_fractals if fx.merged_bar_index >= stroke_merged_start
        ]
        stroke_merged_window = candidate_merged[stroke_merged_start:]
        self._guard_window("stroke_fractals", len(stroke_fractals), allowed_window)
        self._guard_window("stroke_merged_bars", len(stroke_merged_window), allowed_window)
        tail_strokes, st_events = self.stroke_engine.process(
            stroke_fractals,
            stroke_merged_window,
            len(combined),
            bar_index_offset=stroke_merged_start,
            id_offset=self._max_numeric_id(frozen_strokes, "stroke_id"),
        )
        candidate_strokes = frozen_strokes + tail_strokes
        self._reuse_identities("stroke", candidate_strokes, old_state)

        earliest_merged_index = min(
            len(frozen_merged),
            fractal_merged_start,
            stroke_merged_start if stroke_merged_start < len(candidate_merged) else len(frozen_merged),
        )
        first_changed_raw = self._raw_start_for_merged(
            candidate_merged, earliest_merged_index, raw_index_by_id, inclusion_raw_start
        )
        self._event_log.record(LifecycleEvent(
            event_type=EventType.REBUILD_START,
            object_type="engine",
            object_id="incremental_engine",
            occurred_at_bar_id=combined[first_changed_raw].bar_id,
            reason_code="BOUNDED_TAIL_RECONCILIATION",
            detail={
                "rebuild_from_bar": first_changed_raw,
                "rebuild_to_bar": len(combined) - 1,
                "max_rebuild_distance": self.max_rebuild_distance,
                "engine_input_sizes": {
                    "inclusion_raw_bars": len(inclusion_tail),
                    "fractal_merged_bars": len(fractal_window),
                    "stroke_fractals": len(stroke_fractals),
                    "stroke_merged_bars": len(stroke_merged_window),
                },
            },
        ))

        self._raw_bars = combined
        self._merged_bars = candidate_merged
        self._fractals = candidate_fractals
        self._strokes = candidate_strokes
        affected = self._record_transitions(old_state, self._object_maps(), combined[-1].bar_id)
        new_raw_ids = {bar.bar_id for bar in combined[-appended_count:]}
        new_trigger_ids = new_raw_ids | {
            merged.bar_id
            for merged in candidate_merged
            if new_raw_ids.intersection(merged.source_raw_bar_ids)
        }
        self._record_new_diagnostic_events(
            fx_events + st_events,
            new_trigger_ids,
        )
        self._last_engine_inputs = {
            "inclusion_raw_bars": len(inclusion_tail),
            "fractal_merged_bars": len(fractal_window),
            "stroke_fractals": len(stroke_fractals),
            "stroke_merged_bars": len(stroke_merged_window),
        }
        self._update_max_engine_inputs()
        self._event_log.record(LifecycleEvent(
            event_type=EventType.REBUILD_END,
            object_type="engine",
            object_id="incremental_engine",
            occurred_at_bar_id=combined[-1].bar_id,
            reason_code="REBUILD_COMPLETE",
            detail={
                "rebuild_from_bar": first_changed_raw,
                "rebuild_to_bar": len(combined) - 1,
                "affected_objects": affected,
                "frozen_prefix": {
                    "merged_bars": len(frozen_merged),
                    "fractals": len(frozen_fractals),
                    "strokes": len(frozen_strokes),
                },
            },
        ))
        self._rebuild_count += 1
        self._last_rebuild = {
            "from": first_changed_raw,
            "to": len(combined) - 1,
            "affected_objects": affected,
            "frozen_prefix": {
                "merged_bars": len(frozen_merged),
                "fractals": len(frozen_fractals),
                "strokes": len(frozen_strokes),
            },
        }

    def create_checkpoint(self) -> int:
        payload = self._snapshot_payload()
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
        checkpoint_id = self._next_checkpoint_id
        self._next_checkpoint_id += 1
        cp = Checkpoint(
            copy.deepcopy(self._raw_bars),
            copy.deepcopy(self._merged_bars),
            copy.deepcopy(self._fractals),
            copy.deepcopy(self._strokes),
            self._event_log.snapshot(),
            len(self._raw_bars),
            copy.deepcopy(payload),
            self._rebuild_count,
            copy.deepcopy(self._last_rebuild),
            copy.deepcopy(self._last_engine_inputs),
            copy.deepcopy(self._max_engine_inputs),
            digest,
        )
        self._checkpoints[checkpoint_id] = cp
        while len(self._checkpoints) > self.checkpoint_retention:
            oldest_id = min(self._checkpoints)
            del self._checkpoints[oldest_id]
        self._event_log.record(LifecycleEvent(
            event_type=EventType.CHECKPOINT_CREATED,
            object_type="engine",
            object_id="incremental_engine",
            occurred_at_bar_id=self._raw_bars[-1].bar_id if self._raw_bars else "",
            reason_code="CHECKPOINT_INTERVAL",
            detail={"checkpoint_id": checkpoint_id, "sha256": digest},
        ))
        return checkpoint_id

    def resume_from_checkpoint(self, checkpoint_id: int) -> dict[str, Any]:
        if checkpoint_id not in self._checkpoints:
            raise ValueError(
                f"Invalid or evicted checkpoint_id: {checkpoint_id}; "
                f"retained={sorted(self._checkpoints)}"
            )
        cp = self._checkpoints[checkpoint_id]
        self._raw_bars = copy.deepcopy(cp.raw_bars)
        self._merged_bars = copy.deepcopy(cp.merged_bars)
        self._fractals = copy.deepcopy(cp.fractals)
        self._strokes = copy.deepcopy(cp.strokes)
        self._event_log.restore(cp.event_snapshot)
        self._historical_snapshots = {
            count: snapshot
            for count, snapshot in self._historical_snapshots.items()
            if count <= cp.raw_bar_count
        }
        self._store_historical_snapshot(cp.raw_bar_count, cp.historical_snapshot)
        self._rebuild_count = cp.rebuild_count
        self._last_rebuild = copy.deepcopy(cp.last_rebuild)
        self._last_engine_inputs = copy.deepcopy(cp.last_engine_inputs)
        self._max_engine_inputs = copy.deepcopy(cp.max_engine_inputs)
        self._checkpoints = {
            retained_id: retained
            for retained_id, retained in self._checkpoints.items()
            if retained_id <= checkpoint_id
        }
        self._segment_reference_result = None
        self._segment_reference_source_strokes = ()
        if self.segment_reference_enabled:
            self._evaluate_segment_reference()
        self._event_log.record(LifecycleEvent(
            event_type=EventType.CHECKPOINT_RESTORED,
            object_type="engine",
            object_id="incremental_engine",
            occurred_at_bar_id=self._raw_bars[-1].bar_id if self._raw_bars else "",
            reason_code="EXPLICIT_RESTORE",
            detail={"checkpoint_id": checkpoint_id, "sha256": cp.sha256},
        ))
        return self.get_current_state()

    def get_historical_snapshot(self, raw_bar_count: int) -> dict:
        if raw_bar_count not in self._historical_snapshots:
            raise KeyError(
                f"snapshot {raw_bar_count} is not retained; "
                f"retained={sorted(self._historical_snapshots)}"
            )
        return copy.deepcopy(self._historical_snapshots[raw_bar_count])

    def _store_historical_snapshot(self, raw_bar_count: int, snapshot: dict) -> None:
        self._historical_snapshots[raw_bar_count] = copy.deepcopy(snapshot)
        while len(self._historical_snapshots) > self.snapshot_retention:
            oldest_count = min(self._historical_snapshots)
            del self._historical_snapshots[oldest_count]

    def get_current_state(self) -> dict[str, Any]:
        snapshot = self._snapshot_payload()
        audit = {
            "event_log_sha256": self._event_log.compute_sha256(),
            "output_sha256": snapshot["output_sha256"],
            "event_count": len(self._event_log),
            "historical_snapshot_count": len(self._historical_snapshots),
        }
        if self.segment_reference_enabled:
            audit["segment_reference"] = self.get_segment_reference_result()
        return {
            "meta": {
                "symbol": "",
                "bar_frequency": "",
                "adjustment": "qfq",
                "profile_id": self.profile.get("profile_id", "minimal_strict_v1"),
                "engine_version": self.engine_version,
                "analysis_mode": "close_only",
                "calculation_mode": "incremental_frozen_prefix_bounded_tail",
            },
            "data_quality": self._data_quality(),
            "structures": snapshot["structures"],
            "runtime_state": {
                "last_processed_bar_id": self._raw_bars[-1].bar_id if self._raw_bars else "",
                "local_rebuild_from": self._last_rebuild["from"],
                "local_rebuild_to": self._last_rebuild["to"],
                "affected_objects": list(self._last_rebuild["affected_objects"]),
                "frozen_prefix": copy.deepcopy(self._last_rebuild["frozen_prefix"]),
                "rebuild_count": self._rebuild_count,
                "engine_input_sizes": copy.deepcopy(self._last_engine_inputs),
                "max_engine_input_sizes": copy.deepcopy(self._max_engine_inputs),
                "historical_snapshot_count": len(self._historical_snapshots),
                "snapshot_retention": self.snapshot_retention,
                "checkpoint_count": len(self._checkpoints),
                "checkpoint_retention": self.checkpoint_retention,
                "retained_checkpoint_ids": sorted(self._checkpoints),
                "unfinished_fractal_count": sum(
                    x.status != StructureStatus.CONFIRMED for x in self._fractals
                ),
                "unfinished_stroke_count": sum(
                    x.status != StructureStatus.CONFIRMED for x in self._strokes
                ),
            },
            "audit": audit,
            "events": self._event_log.to_list(),
        }

    def _evaluate_segment_reference(self) -> None:
        self._segment_reference_result = None
        self._segment_reference_source_strokes = ()
        source = tuple(
            stroke
            for stroke in self._strokes
            if stroke.status == StructureStatus.CONFIRMED
        )
        self._segment_reference_source_strokes = source
        if not source:
            self._segment_reference_result = None
            return
        self._segment_reference_result = SegmentEngine(
            SegmentEngine.reference_profile()
        ).process_primary(source, sequence_id="incremental:primary")

    def get_segment_reference_result(self) -> dict[str, Any] | None:
        """Return opt-in Segment reference evidence without making it output authority."""
        if not self.segment_reference_enabled:
            return None
        result = self._segment_reference_result
        if result is None:
            return {
                "reason_code": None,
                "completed": False,
                "segment": None,
                "pending_second_case": False,
                "source_stroke_ids": [
                    stroke.stroke_id for stroke in self._segment_reference_source_strokes
                ],
            }
        return {
            "reason_code": result.reason_code,
            "completed": result.completed,
            "segment": result.segment.to_dict() if result.segment is not None else None,
            "pending_second_case": result.pending_second_case is not None,
            "source_stroke_ids": [
                stroke.stroke_id for stroke in self._segment_reference_source_strokes
            ],
        }

    def _data_quality(self) -> dict:
        timestamps = [bar.timestamp for bar in self._raw_bars]
        duplicate_count = len(timestamps) - len(set(timestamps))
        monotonic = all(a < b for a, b in zip(timestamps, timestamps[1:]))
        invalid = sum(not bar.is_valid for bar in self._raw_bars)
        status = "OK" if duplicate_count == 0 and monotonic and invalid == 0 else "WARNING"
        return {
            "raw_bar_count": len(self._raw_bars),
            "valid_bar_count": len(self._raw_bars) - invalid,
            "duplicate_count": duplicate_count,
            "missing_interval_count": 0,
            "monotonic_timestamp": monotonic,
            "status": status,
        }

    def _validate_append(self, bars: list[RawBar]) -> None:
        all_ts = ([self._raw_bars[-1].timestamp] if self._raw_bars else []) + [b.timestamp for b in bars]
        if any(a >= b for a, b in zip(all_ts, all_ts[1:])):
            raise ValueError("incremental input timestamps must be strictly increasing")

    def _snapshot_payload(self) -> dict:
        structures = {
            "merged_bars": [x.to_dict() for x in self._merged_bars],
            "fractals": [x.to_dict() for x in self._fractals],
            "strokes": [x.to_dict() for x in self._strokes],
        }
        digest = hashlib.sha256(
            json.dumps(structures, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        return {"structures": structures, "output_sha256": digest}

    def _active_ids(self) -> list[str]:
        return [x.object_id for x in self._merged_bars + self._fractals + self._strokes]

    def _object_maps(self) -> dict[str, dict]:
        result = {}
        for kind, objects in (
            ("merged_bar", self._merged_bars),
            ("fractal", self._fractals),
            ("stroke", self._strokes),
        ):
            for obj in objects:
                result[f"{kind}:{obj.logical_id}"] = {
                    "kind": kind,
                    "object": obj,
                    "dict": obj.to_dict(),
                }
        return result

    def _reuse_identities(self, kind: str, objects: list, old_state: dict[str, dict]) -> None:
        id_attr = {"merged_bar": "bar_id", "fractal": "fractal_id", "stroke": "stroke_id"}[kind]
        for obj in objects:
            old_item = old_state.get(f"{kind}:{obj.logical_id}")
            if old_item is None:
                continue
            old_obj = old_item["object"]
            setattr(obj, id_attr, getattr(old_obj, id_attr))
            if self._structural_dict(obj.to_dict()) == self._structural_dict(old_item["dict"]):
                obj.object_id = old_obj.object_id
                obj.revision = old_obj.revision
            else:
                obj.revision = old_obj.revision + 1
                obj.object_id = self._revision_object_id(old_obj.object_id, obj.revision)

    @staticmethod
    def _structural_dict(data: dict) -> dict:
        ignored = {
            "object_id", "revision", "status", "created_at_bar", "confirmed_at_bar",
            "repaint_risk", "confirmation_requirements", "rule_profile", "rule_version",
        }
        return {k: v for k, v in data.items() if k not in ignored}

    @staticmethod
    def _revision_object_id(object_id: str, revision: int) -> str:
        return re.sub(r"_r\d+$", "", object_id) + f"_r{revision}"

    def _record_transitions(self, old: dict, new: dict, bar_id: str) -> list[str]:
        affected = []
        for key in sorted(old.keys() - new.keys()):
            item = old[key]
            obj = item["object"]
            affected.append(obj.object_id)
            self._event_log.record(LifecycleEvent(
                event_type=EventType.INVALIDATED,
                object_type=item["kind"],
                object_id=obj.object_id,
                logical_id=obj.logical_id,
                occurred_at_bar_id=bar_id,
                reason_code="TAIL_REBUILD_REMOVED",
            ))
        for key in sorted(new.keys() - old.keys()):
            item = new[key]
            obj = item["object"]
            affected.append(obj.object_id)
            self._event_log.record(LifecycleEvent(
                event_type=EventType.CREATED,
                object_type=item["kind"],
                object_id=obj.object_id,
                logical_id=obj.logical_id,
                occurred_at_bar_id=bar_id,
                reason_code="TAIL_REBUILD_CREATED",
            ))
        for key in sorted(old.keys() & new.keys()):
            before, after = old[key], new[key]
            if before["dict"] == after["dict"]:
                continue
            old_obj, new_obj = before["object"], after["object"]
            affected.append(new_obj.object_id)
            if new_obj.revision > old_obj.revision:
                self._event_log.record(LifecycleEvent(
                    event_type=EventType.STRUCTURE_REPLACED,
                    object_type=after["kind"],
                    object_id=old_obj.object_id,
                    logical_id=new_obj.logical_id,
                    occurred_at_bar_id=bar_id,
                    reason_code="TAIL_REBUILD_REVISION",
                    replaced_by=new_obj.object_id,
                    detail={"old_revision": old_obj.revision, "new_revision": new_obj.revision},
                ))
                continue
            event_type = (
                EventType.CONFIRMED
                if after["dict"].get("status") == "CONFIRMED"
                and before["dict"].get("status") != "CONFIRMED"
                else EventType.STATUS_CHANGED
            )
            self._event_log.record(LifecycleEvent(
                event_type=event_type,
                object_type=after["kind"],
                object_id=new_obj.object_id,
                logical_id=new_obj.logical_id,
                occurred_at_bar_id=bar_id,
                reason_code="TAIL_REBUILD_STATE_TRANSITION",
                detail={
                    "before_status": before["dict"].get("status"),
                    "after_status": after["dict"].get("status"),
                },
            ))
        return sorted(set(affected))

    def _record_new_diagnostic_events(
        self, events: list[LifecycleEvent], new_bar_ids: set[str]
    ) -> None:
        """Record non-persistent tail diagnostics triggered by newly appended bars only."""
        diagnostic_types = {
            EventType.CANDIDATE_REJECTED,
            EventType.STRUCTURE_REPLACED,
        }
        for event in events:
            if (
                event.event_type in diagnostic_types
                and event.occurred_at_bar_id in new_bar_ids
            ):
                self._event_log.record(event)

    def _guard_window(self, name: str, actual: int, allowed: int) -> None:
        if actual > allowed:
            raise RebuildBoundaryExceeded(
                f"{name} requires {actual} items, bounded limit is {allowed}"
            )

    @staticmethod
    def _last_confirmed(objects: list):
        for obj in reversed(objects):
            if obj.status == StructureStatus.CONFIRMED:
                return obj
        return None

    @staticmethod
    def _max_numeric_id(objects: list, attr: str) -> int:
        maximum = 0
        for obj in objects:
            match = re.search(r"(\d+)$", getattr(obj, attr, ""))
            if match:
                maximum = max(maximum, int(match.group(1)))
        return maximum

    @staticmethod
    def _parse_direction(value: str) -> TrendDirection:
        try:
            return TrendDirection(value)
        except ValueError:
            return TrendDirection.UP

    @staticmethod
    def _raw_start_for_merged(
        merged_bars: list,
        merged_index: int,
        raw_index_by_id: dict[str, int],
        fallback: int,
    ) -> int:
        if merged_index < 0 or merged_index >= len(merged_bars):
            return fallback
        indices = [
            raw_index_by_id[x]
            for x in merged_bars[merged_index].source_raw_bar_ids
            if x in raw_index_by_id
        ]
        return min(indices) if indices else fallback

    @staticmethod
    def _empty_engine_metrics() -> dict[str, int]:
        return {
            "inclusion_raw_bars": 0,
            "fractal_merged_bars": 0,
            "stroke_fractals": 0,
            "stroke_merged_bars": 0,
        }

    def _update_max_engine_inputs(self) -> None:
        for key, value in self._last_engine_inputs.items():
            self._max_engine_inputs[key] = max(self._max_engine_inputs[key], value)
