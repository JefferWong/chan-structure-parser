"""
追加式事件日志。

所有结构变化以追加事件形式记录，不覆盖旧结果。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..domain.lifecycle import LifecycleEvent, EventType, StructureStatus


class EventLog:
    """追加式事件日志。

    每个结构变化记录为一个不可变事件。
    支持按对象、时间范围、事件类型查询。
    """

    def __init__(self):
        self._events: list[LifecycleEvent] = []
        self._event_index: dict[str, list[int]] = {}  # object_id -> event indices

    def record(self, event: LifecycleEvent) -> None:
        """记录一个事件（追加模式，不修改已有事件）。"""
        self._events.append(event)
        idx = len(self._events) - 1
        if event.object_id not in self._event_index:
            self._event_index[event.object_id] = []
        self._event_index[event.object_id].append(idx)

    def record_structure_created(
        self,
        object_type: str,
        object_id: str,
        logical_id: str,
        at_bar_id: str,
        rule_profile: str = "minimal_strict_v1",
    ) -> LifecycleEvent:
        """记录对象创建事件。"""
        event = LifecycleEvent(
            event_type=EventType.CREATED,
            object_type=object_type,
            object_id=object_id,
            logical_id=logical_id,
            occurred_at_bar_id=at_bar_id,
            reason_code="INITIAL_CREATION",
            rule_profile=rule_profile,
        )
        self.record(event)
        return event

    def record_structure_replaced(
        self,
        object_type: str,
        old_object_id: str,
        new_object_id: str,
        logical_id: str,
        at_bar_id: str,
        reason_code: str,
    ) -> LifecycleEvent:
        """记录结构替换事件。"""
        event = LifecycleEvent(
            event_type=EventType.STRUCTURE_REPLACED,
            object_type=object_type,
            object_id=old_object_id,
            logical_id=logical_id,
            occurred_at_bar_id=at_bar_id,
            reason_code=reason_code,
            replaced_by=new_object_id,
        )
        self.record(event)
        return event

    def record_local_rebuild(
        self, from_bar: int, to_bar: int, affected_objects: list[str]
    ) -> None:
        """记录局部重建事件。"""
        start_event = LifecycleEvent(
            event_type=EventType.REBUILD_START,
            object_type="engine",
            object_id="incremental_engine",
            occurred_at_bar_id=f"bar_{from_bar:06d}",
            reason_code="DEPENDENCY_INVALIDATION",
            detail={
                "rebuild_from_bar": from_bar,
                "rebuild_to_bar": to_bar,
                "affected_objects": affected_objects,
            },
        )
        self.record(start_event)

        end_event = LifecycleEvent(
            event_type=EventType.REBUILD_END,
            object_type="engine",
            object_id="incremental_engine",
            occurred_at_bar_id=f"bar_{to_bar:06d}",
            reason_code="REBUILD_COMPLETE",
            detail={
                "rebuild_from_bar": from_bar,
                "rebuild_to_bar": to_bar,
            },
        )
        self.record(end_event)

    def get_object_history(self, object_id: str) -> list[LifecycleEvent]:
        """获取某个对象的所有历史事件。"""
        indices = self._event_index.get(object_id, [])
        return [self._events[i] for i in indices]

    def get_object_lifecycle(self, logical_id: str) -> list[LifecycleEvent]:
        """获取某个逻辑身份的所有历史事件（跨修订版）。"""
        return [e for e in self._events if e.logical_id == logical_id]

    def get_events_by_type(self, event_type: str) -> list[LifecycleEvent]:
        return [e for e in self._events if e.event_type == event_type]

    def compute_sha256(self) -> str:
        """计算事件日志的确定性哈希。"""
        payload = "|".join(
            f"{e.event_id}|{e.event_type}|{e.object_type}|{e.object_id}|"
            f"{e.occurred_at_bar_id}|{e.reason_code}"
            for e in self._events
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def to_list(self) -> list[dict]:
        return [
            {
                "event_id": e.event_id,
                "event_type": e.event_type,
                "object_type": e.object_type,
                "object_id": e.object_id,
                "logical_id": e.logical_id,
                "occurred_at_bar_id": e.occurred_at_bar_id,
                "reason_code": e.reason_code,
                "replaced_by": e.replaced_by,
                "rule_profile": e.rule_profile,
                "rule_version": e.rule_version,
                "detail": e.detail,
            }
            for e in self._events
        ]

    def __len__(self) -> int:
        return len(self._events)
