"""确定性、追加式生命周期事件日志。"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Iterable

from ..domain.lifecycle import LifecycleEvent, EventType


class EventLog:
    def __init__(self):
        self._events: list[LifecycleEvent] = []
        self._event_index: dict[str, list[int]] = {}
        self._next_sequence = 1

    def record(self, event: LifecycleEvent) -> LifecycleEvent:
        if not event.event_id:
            event.event_id = f"evt_{self._next_sequence:08d}"
        self._next_sequence += 1
        self._events.append(copy.deepcopy(event))
        self._event_index.setdefault(event.object_id, []).append(len(self._events) - 1)
        return event

    def record_many(self, events: Iterable[LifecycleEvent]) -> None:
        for event in events:
            self.record(event)

    def record_structure_created(self, object_type: str, object_id: str, logical_id: str,
                                 at_bar_id: str, rule_profile: str = "minimal_strict_v1") -> LifecycleEvent:
        return self.record(LifecycleEvent(
            event_type=EventType.CREATED, object_type=object_type, object_id=object_id,
            logical_id=logical_id, occurred_at_bar_id=at_bar_id,
            reason_code="INITIAL_CREATION", rule_profile=rule_profile,
        ))

    def record_local_rebuild(self, from_bar: int, to_bar: int, affected_objects: list[str]) -> None:
        self.record(LifecycleEvent(
            event_type=EventType.REBUILD_START, object_type="engine", object_id="incremental_engine",
            occurred_at_bar_id=f"bar_{from_bar + 1:06d}", reason_code="DEPENDENCY_INVALIDATION",
            detail={"rebuild_from_bar": from_bar, "rebuild_to_bar": to_bar,
                    "affected_objects": affected_objects},
        ))
        self.record(LifecycleEvent(
            event_type=EventType.REBUILD_END, object_type="engine", object_id="incremental_engine",
            occurred_at_bar_id=f"bar_{to_bar + 1:06d}", reason_code="REBUILD_COMPLETE",
            detail={"rebuild_from_bar": from_bar, "rebuild_to_bar": to_bar},
        ))

    def snapshot(self) -> tuple[list[LifecycleEvent], int]:
        return copy.deepcopy(self._events), self._next_sequence

    def restore(self, snapshot: tuple[list[LifecycleEvent], int]) -> None:
        self._events, self._next_sequence = copy.deepcopy(snapshot)
        self._event_index = {}
        for idx, event in enumerate(self._events):
            self._event_index.setdefault(event.object_id, []).append(idx)

    def get_object_history(self, object_id: str) -> list[LifecycleEvent]:
        return [copy.deepcopy(self._events[i]) for i in self._event_index.get(object_id, [])]

    def get_object_lifecycle(self, logical_id: str) -> list[LifecycleEvent]:
        return [copy.deepcopy(e) for e in self._events if e.logical_id == logical_id]

    def get_events_by_type(self, event_type: str) -> list[LifecycleEvent]:
        return [copy.deepcopy(e) for e in self._events if e.event_type == event_type]

    def compute_sha256(self) -> str:
        payload = json.dumps(self.to_list(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def to_list(self) -> list[dict]:
        return [{
            "event_id": e.event_id, "event_type": e.event_type, "object_type": e.object_type,
            "object_id": e.object_id, "logical_id": e.logical_id,
            "occurred_at_bar_id": e.occurred_at_bar_id, "reason_code": e.reason_code,
            "replaced_by": e.replaced_by, "rule_profile": e.rule_profile,
            "rule_version": e.rule_version, "detail": copy.deepcopy(e.detail),
        } for e in self._events]

    def __len__(self) -> int:
        return len(self._events)
