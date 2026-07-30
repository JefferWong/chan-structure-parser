"""
领域对象基类与通用类型定义。

每个核心结构对象都携带三维标识：
- object_id:   当前实体实例的唯一标识
- logical_id:  结构的逻辑身份（跨修订版不变）
- revision:    同一逻辑结构经历的修订次数
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ============================================================
# 结构状态枚举
# ============================================================
class StructureStatus(str, Enum):
    """结构对象的状态枚举，按确认程度递增。"""
    CANDIDATE    = "CANDIDATE"      # 候选：条件初判满足，待后续确认
    PROVISIONAL  = "PROVISIONAL"    # 暂定：当前可用但可能被后续数据推翻
    CONFIRMED    = "CONFIRMED"      # 已确认：被后续结构锁定，不可再变
    INVALIDATED  = "INVALIDATED"    # 已失效：被新数据推翻
    REPLACED     = "REPLACED"       # 已替换：被同逻辑身份的新修订版替代


# ============================================================
# 分型类型
# ============================================================
class FractalType(str, Enum):
    TOP    = "TOP"
    BOTTOM = "BOTTOM"


# ============================================================
# 笔方向
# ============================================================
class StrokeDirection(str, Enum):
    UP   = "UP"
    DOWN = "DOWN"


# ============================================================
# 趋势方向（用于包含处理）
# ============================================================
class TrendDirection(str, Enum):
    UP   = "UP"
    DOWN = "DOWN"


# ============================================================
# 基类：带生命周期的结构对象
# ============================================================
@dataclass
class StructureObject:
    """所有结构对象的基类，携带三维标识和生命周期信息。"""

    object_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    logical_id: Optional[str] = None
    revision: int = 1
    status: StructureStatus = StructureStatus.CANDIDATE

    # 生命周期时间戳（基于K线索引，不是真实时间）
    created_at_bar: int = -1          # 首次被创建的K线索引
    confirmed_at_bar: Optional[int] = None  # 被确认的K线索引
    invalidated_at_bar: Optional[int] = None
    replaced_by: Optional[str] = None  # 替换本对象的 object_id

    # 配置来源
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
        return self.status in (
            StructureStatus.CANDIDATE,
            StructureStatus.PROVISIONAL,
            StructureStatus.CONFIRMED,
        )

    def is_confirmed(self) -> bool:
        return self.status == StructureStatus.CONFIRMED


# ============================================================
# 生命周期事件
# ============================================================
@dataclass
class LifecycleEvent:
    """追加式生命周期事件，不覆盖旧结果。"""

    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    event_type: str = ""                          # 事件类型
    object_type: str = ""                         # 对象类型: raw_bar, merged_bar, fractal, stroke
    object_id: str = ""                           # 受影响对象
    logical_id: Optional[str] = None
    occurred_at_bar_id: str = ""                  # 触发事件的K线ID
    reason_code: str = ""                         # 原因代码
    replaced_by: Optional[str] = None
    rule_profile: str = "minimal_strict_v1"
    rule_version: str = "1.0.0"
    detail: dict = field(default_factory=dict)    # 附加详情


# ============================================================
# 事件类型常量
# ============================================================
class EventType:
    CREATED            = "OBJECT_CREATED"
    CONFIRMED          = "OBJECT_CONFIRMED"
    STATUS_CHANGED     = "STATUS_CHANGED"
    STRUCTURE_REPLACED = "STRUCTURE_REPLACED"
    INVALIDATED        = "OBJECT_INVALIDATED"
    REBUILD_START      = "LOCAL_REBUILD_START"
    REBUILD_END        = "LOCAL_REBUILD_END"
    DATA_ERROR         = "DATA_ERROR"
    DATA_WARNING       = "DATA_WARNING"
