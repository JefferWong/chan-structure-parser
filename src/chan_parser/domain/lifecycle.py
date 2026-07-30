"""领域对象生命周期与确定性事件模型。"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class StructureStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    PROVISIONAL = "PROVISIONAL"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    REPLACED = "REPLACED"


class FractalType(str, Enum):
    TOP = "TOP"
    BOTTOM = "BOTTOM"


class StrokeDirection(str, Enum):
    UP = "UP"
    DOWN = "DOWN"


class TrendDirection(str, Enum):
    UP = "UP"
    DOWN = "DOWN"


@dataclass
class StructureObject:
    object_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    logical_id: Optional[str] = None
    revision: int = 1
    status: StructureStatus = StructureStatus.CANDIDATE
    created_at_bar: int = -1
    confirmed_at_bar: Optional[int] = None
    invalidated_at_bar: Optional[int] = None
    replaced_by: Optional[str] = None
    rule_profile: str = "minimal_strict_v1"
    rule_version: str = "1.0.0"

    def mark_confirmed(self, at_bar: int) -> None:
        self.status = StructureStatus.CONFIRMED
        self.confirmed_at_bar = at_bar

    def mark_invalidated(self, at_bar: int, reason_code: str = "") -> None:
        self.status = StructureStatus.INVALIDATED
        self.invalidated_at_bar = at_bar

    def mark_replaced(self, by_object_id: str) -> None:
        self.status = StructureStatus.REPLACED
        self.replaced_by = by_object_id

    def is_active(self) -> bool:
        return self.status in {StructureStatus.CANDIDATE, StructureStatus.PROVISIONAL, StructureStatus.CONFIRMED}

    def is_confirmed(self) -> bool:
        return self.status == StructureStatus.CONFIRMED


@dataclass
class LifecycleEvent:
    """追加式事件。event_id 留空时由 EventLog 按顺序确定性分配。"""
    event_id: str = ""
    event_type: str = ""
    object_type: str = ""
    object_id: str = ""
    logical_id: Optional[str] = None
    occurred_at_bar_id: str = ""
    reason_code: str = ""
    replaced_by: Optional[str] = None
    rule_profile: str = "minimal_strict_v1"
    rule_version: str = "1.0.0"
    detail: dict = field(default_factory=dict)


class EventType:
    CREATED = "OBJECT_CREATED"
    CONFIRMED = "OBJECT_CONFIRMED"
    STATUS_CHANGED = "STATUS_CHANGED"
    STRUCTURE_REPLACED = "STRUCTURE_REPLACED"
    INVALIDATED = "OBJECT_INVALIDATED"
    CANDIDATE_REJECTED = "CANDIDATE_REJECTED"
    REBUILD_START = "LOCAL_REBUILD_START"
    REBUILD_END = "LOCAL_REBUILD_END"
    CHECKPOINT_CREATED = "CHECKPOINT_CREATED"
    CHECKPOINT_RESTORED = "CHECKPOINT_RESTORED"
    DATA_ERROR = "DATA_ERROR"
    DATA_WARNING = "DATA_WARNING"
