"""
原始K线（Raw Bar）领域对象。

代表从数据源接收的、未经任何处理的OHLCV数据行。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class RawBar:
    """一根原始K线。"""

    bar_id: str                              # 唯一标识，如 "bar_000001"
    bar_index: int                           # 序列索引，从0开始
    timestamp: datetime                      # K线时间戳
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    # 元数据
    is_valid: bool = True                    # 是否通过数据质量检查
    validation_errors: list[str] = field(default_factory=list)

    # 谱系追踪
    source_line: Optional[int] = None        # 数据源行号
    input_checksum: Optional[str] = None     # 输入数据校验和

    # 包含处理元数据
    source_raw_bar_ids: list[str] = field(default_factory=list)
    # 组成此K线的原始K线ID列表（经过包含合并的K线会携带多个原始ID）

    def __post_init__(self):
        if self.is_valid:
            self._validate()

    def _validate(self) -> None:
        """验证OHLC合法性。"""
        errors = []
        if self.high < self.low:
            errors.append(f"high({self.high}) < low({self.low})")
        if self.high < self.open:
            errors.append(f"high({self.high}) < open({self.open})")
        if self.high < self.close:
            errors.append(f"high({self.high}) < close({self.close})")
        if self.low > self.open:
            errors.append(f"low({self.low}) > open({self.open})")
        if self.low > self.close:
            errors.append(f"low({self.low}) > close({self.close})")
        if errors:
            self.is_valid = False
            self.validation_errors = errors

    def to_dict(self) -> dict:
        return {
            "bar_id": self.bar_id,
            "bar_index": self.bar_index,
            "timestamp": self.timestamp.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "is_valid": self.is_valid,
        }

    def content_hash(self) -> str:
        """仅对确定性内容计算哈希（排除元数据）。"""
        payload = f"{self.bar_index}|{self.timestamp.isoformat()}|{self.open}|{self.high}|{self.low}|{self.close}|{self.volume}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]
