"""独立增量路径：追加式事件、受限尾部协调、检查点恢复。"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from ..audit.event_log import EventLog
from ..domain.lifecycle import EventType, LifecycleEvent, StructureStatus
from ..domain.raw_bar import RawBar
from .fractal import FractalEngine
from .inclusion import InclusionEngine
from .stroke import StrokeEngine


class RebuildBoundaryExceeded(RuntimeError):
    pass


@dataclass
class Checkpoint:
    raw_bars: list
    merged_bars: list
    fractals: list
    strokes: list
    event_snapshot: tuple
    historical_snapshots: dict
    rebuild_count: int
    last_rebuild: dict
    sha256: str


class IncrementalEngine:
    def __init__(self, profile: dict):
        self.profile = profile
        self.inclusion_engine = InclusionEngine(profile.get("inclusion", {}))
        self.fractal_engine = FractalEngine(profile.get("fractal", {}))
        self.stroke_engine = StrokeEngine(profile.get("stroke", {}))
        runtime = profile.get("runtime", {})
        self.max_rebuild_distance = runtime.get("max_rebuild_distance", 200)
        self.checkpoint_interval = runtime.get("checkpoint_interval", 50)
        self.engine_version = "0.2.0"
        self._raw_bars: list[RawBar] = []
        self._merged_bars = []
        self._fractals = []
        self._strokes = []
        self._event_log = EventLog()
        self._checkpoints: list[Checkpoint] = []
        self._historical_snapshots: dict[int, dict] = {}
        self._rebuild_count = 0
        self._last_rebuild = {"from": None, "to": None, "affected_objects": []}

    def append_one(self, raw_bar: RawBar) -> dict[str, Any]:
        return self.append_batch([raw_bar])

    def append_batch(self, new_bars: list[RawBar]) -> dict[str, Any]:
        if not new_bars:
            return self.get_current_state()
        self._validate_append(new_bars)
        combined = self._raw_bars + list(new_bars)
        valid = [b for b in combined if b.is_valid]
        candidate_merged, inc_events = self.inclusion_engine.process(valid)
        candidate_fractals, fx_events = self.fractal_engine.process(candidate_merged, len(combined))
        candidate_strokes, st_events = self.stroke_engine.process(candidate_fractals, candidate_merged, len(combined))

        if not self._raw_bars:
            self._raw_bars = combined
            self._merged_bars, self._fractals, self._strokes = candidate_merged, candidate_fractals, candidate_strokes
            self._event_log.record_many(inc_events + fx_events + st_events)
            self._last_rebuild = {"from": 0, "to": len(combined) - 1,
                                  "affected_objects": self._active_ids()}
        else:
            boundary = max(0, len(self._raw_bars) - self.max_rebuild_distance)
            first_changed = self._earliest_changed_raw(candidate_merged, candidate_fractals, candidate_strokes, combined)
            if first_changed < boundary:
                raise RebuildBoundaryExceeded(
                    f"required rebuild from raw index {first_changed}, allowed boundary is {boundary}"
                )
            old_state = self._object_maps()
            self._event_log.record(LifecycleEvent(
                event_type=EventType.REBUILD_START, object_type="engine", object_id="incremental_engine",
                occurred_at_bar_id=combined[first_changed].bar_id,
                reason_code="BOUNDED_TAIL_RECONCILIATION",
                detail={"rebuild_from_bar": first_changed, "rebuild_to_bar": len(combined) - 1,
                        "max_rebuild_distance": self.max_rebuild_distance},
            ))
            self._raw_bars = combined
            self._merged_bars, self._fractals, self._strokes = candidate_merged, candidate_fractals, candidate_strokes
            affected = self._record_transitions(old_state, self._object_maps(), combined[-1].bar_id)
            self._event_log.record(LifecycleEvent(
                event_type=EventType.REBUILD_END, object_type="engine", object_id="incremental_engine",
                occurred_at_bar_id=combined[-1].bar_id, reason_code="REBUILD_COMPLETE",
                detail={"rebuild_from_bar": first_changed, "rebuild_to_bar": len(combined) - 1,
                        "affected_objects": affected},
            ))
            self._rebuild_count += 1
            self._last_rebuild = {"from": first_changed, "to": len(combined) - 1,
                                  "affected_objects": affected}

        self._historical_snapshots[len(self._raw_bars)] = self._snapshot_payload()
        if self.checkpoint_interval and len(self._raw_bars) % self.checkpoint_interval == 0:
            self.create_checkpoint()
        return self.get_current_state()

    def create_checkpoint(self) -> int:
        payload = self._snapshot_payload()
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
        cp = Checkpoint(copy.deepcopy(self._raw_bars), copy.deepcopy(self._merged_bars),
                        copy.deepcopy(self._fractals), copy.deepcopy(self._strokes),
                        self._event_log.snapshot(), copy.deepcopy(self._historical_snapshots),
                        self._rebuild_count, copy.deepcopy(self._last_rebuild), digest)
        self._checkpoints.append(cp)
        self._event_log.record(LifecycleEvent(
            event_type=EventType.CHECKPOINT_CREATED, object_type="engine", object_id="incremental_engine",
            occurred_at_bar_id=self._raw_bars[-1].bar_id if self._raw_bars else "",
            reason_code="CHECKPOINT_INTERVAL", detail={"checkpoint_id": len(self._checkpoints)-1, "sha256": digest},
        ))
        return len(self._checkpoints) - 1

    def resume_from_checkpoint(self, checkpoint_id: int) -> dict[str, Any]:
        if checkpoint_id < 0 or checkpoint_id >= len(self._checkpoints):
            raise ValueError(f"Invalid checkpoint_id: {checkpoint_id}")
        cp = self._checkpoints[checkpoint_id]
        self._raw_bars = copy.deepcopy(cp.raw_bars)
        self._merged_bars = copy.deepcopy(cp.merged_bars)
        self._fractals = copy.deepcopy(cp.fractals)
        self._strokes = copy.deepcopy(cp.strokes)
        self._event_log.restore(cp.event_snapshot)
        self._historical_snapshots = copy.deepcopy(cp.historical_snapshots)
        self._rebuild_count = cp.rebuild_count
        self._last_rebuild = copy.deepcopy(cp.last_rebuild)
        self._checkpoints = self._checkpoints[:checkpoint_id + 1]
        self._event_log.record(LifecycleEvent(
            event_type=EventType.CHECKPOINT_RESTORED, object_type="engine", object_id="incremental_engine",
            occurred_at_bar_id=self._raw_bars[-1].bar_id if self._raw_bars else "",
            reason_code="EXPLICIT_RESTORE", detail={"checkpoint_id": checkpoint_id, "sha256": cp.sha256},
        ))
        return self.get_current_state()

    def get_historical_snapshot(self, raw_bar_count: int) -> dict:
        return copy.deepcopy(self._historical_snapshots[raw_bar_count])

    def get_current_state(self) -> dict[str, Any]:
        return {
            "meta": {"symbol": "", "bar_frequency": "", "adjustment": "qfq",
                     "profile_id": self.profile.get("profile_id", "minimal_strict_v1"),
                     "engine_version": self.engine_version, "analysis_mode": "close_only",
                     "calculation_mode": "incremental_bounded_reconciliation"},
            "data_quality": {"raw_bar_count": len(self._raw_bars),
                             "valid_bar_count": sum(b.is_valid for b in self._raw_bars),
                             "duplicate_count": 0, "missing_interval_count": 0,
                             "monotonic_timestamp": True, "status": "OK"},
            "structures": self._snapshot_payload()["structures"],
            "runtime_state": {"last_processed_bar_id": self._raw_bars[-1].bar_id if self._raw_bars else "",
                              "local_rebuild_from": self._last_rebuild["from"],
                              "local_rebuild_to": self._last_rebuild["to"],
                              "affected_objects": list(self._last_rebuild["affected_objects"]),
                              "rebuild_count": self._rebuild_count,
                              "unfinished_fractal_count": sum(x.status != StructureStatus.CONFIRMED for x in self._fractals),
                              "unfinished_stroke_count": sum(x.status != StructureStatus.CONFIRMED for x in self._strokes)},
            "audit": {"event_log_sha256": self._event_log.compute_sha256(),
                      "output_sha256": self._snapshot_payload()["output_sha256"],
                      "event_count": len(self._event_log),
                      "historical_snapshot_count": len(self._historical_snapshots)},
            "events": self._event_log.to_list(),
        }

    def _validate_append(self, bars: list[RawBar]) -> None:
        all_ts = ([self._raw_bars[-1].timestamp] if self._raw_bars else []) + [b.timestamp for b in bars]
        if any(a >= b for a, b in zip(all_ts, all_ts[1:])):
            raise ValueError("incremental input timestamps must be strictly increasing")

    def _snapshot_payload(self) -> dict:
        structures = {"merged_bars": [x.to_dict() for x in self._merged_bars],
                      "fractals": [x.to_dict() for x in self._fractals],
                      "strokes": [x.to_dict() for x in self._strokes]}
        digest = hashlib.sha256(json.dumps(structures, sort_keys=True, default=str).encode()).hexdigest()[:16]
        return {"structures": structures, "output_sha256": digest}

    def _active_ids(self) -> list[str]:
        return [x.object_id for x in self._merged_bars + self._fractals + self._strokes]

    def _object_maps(self) -> dict[str, dict]:
        result = {}
        for kind, objects in (("merged_bar", self._merged_bars), ("fractal", self._fractals), ("stroke", self._strokes)):
            for obj in objects:
                result[f"{kind}:{obj.logical_id}"] = {"kind": kind, "object": obj, "dict": obj.to_dict()}
        return result

    def _record_transitions(self, old: dict, new: dict, bar_id: str) -> list[str]:
        affected = []
        for key in sorted(old.keys() - new.keys()):
            item = old[key]; obj = item["object"]; affected.append(obj.object_id)
            self._event_log.record(LifecycleEvent(
                event_type=EventType.INVALIDATED, object_type=item["kind"], object_id=obj.object_id,
                logical_id=obj.logical_id, occurred_at_bar_id=bar_id, reason_code="TAIL_REBUILD_REMOVED",
            ))
        for key in sorted(new.keys() - old.keys()):
            item = new[key]; obj = item["object"]; affected.append(obj.object_id)
            self._event_log.record(LifecycleEvent(
                event_type=EventType.CREATED, object_type=item["kind"], object_id=obj.object_id,
                logical_id=obj.logical_id, occurred_at_bar_id=bar_id, reason_code="TAIL_REBUILD_CREATED",
            ))
        for key in sorted(old.keys() & new.keys()):
            before, after = old[key], new[key]
            if before["dict"] != after["dict"]:
                obj = after["object"]; affected.append(obj.object_id)
                event_type = EventType.CONFIRMED if after["dict"].get("status") == "CONFIRMED" and before["dict"].get("status") != "CONFIRMED" else EventType.STATUS_CHANGED
                self._event_log.record(LifecycleEvent(
                    event_type=event_type, object_type=after["kind"], object_id=obj.object_id,
                    logical_id=obj.logical_id, occurred_at_bar_id=bar_id,
                    reason_code="TAIL_REBUILD_STATE_TRANSITION",
                    detail={"before_status": before["dict"].get("status"), "after_status": after["dict"].get("status")},
                ))
        return sorted(set(affected))

    def _earliest_changed_raw(self, merged, fractals, strokes, combined: list[RawBar]) -> int:
        old = self._merged_bars
        first_m = self._first_difference([x.to_dict() for x in old], [x.to_dict() for x in merged])
        if first_m is None:
            return max(0, len(self._raw_bars) - 2)
        if first_m >= len(merged):
            return max(0, len(self._raw_bars) - 2)
        source_ids = merged[first_m].source_raw_bar_ids
        index_by_id = {b.bar_id: i for i, b in enumerate(combined)}
        indices = [index_by_id[s] for s in source_ids if s in index_by_id]
        return min(indices) if indices else max(0, len(self._raw_bars) - 2)

    @staticmethod
    def _first_difference(old: list[dict], new: list[dict]):
        for i, (a, b) in enumerate(zip(old, new)):
            comparable_a = {k: v for k, v in a.items() if k not in {"object_id", "revision", "status", "confirmed_at_bar"}}
            comparable_b = {k: v for k, v in b.items() if k not in {"object_id", "revision", "status", "confirmed_at_bar"}}
            if comparable_a != comparable_b:
                return i
        if len(old) != len(new):
            return min(len(old), len(new))
        return None
