"""
DataFrame 数据适配器。

从 pandas DataFrame 或 Python 列表加载 OHLCV 数据。
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Optional

from ..domain.raw_bar import RawBar


class DataFrameAdapter:
    """通用数据适配器，支持 dict 列表和 pandas DataFrame。"""

    def __init__(self, data: list[dict[str, Any]]):
        """
        Args:
            data: dict 列表，每个 dict 包含 timestamp, open, high, low, close, volume(可选)
        """
        self._data = data
        self._input_checksum: Optional[str] = None

    def load(self) -> tuple[list[RawBar], dict[str, Any]]:
        """加载数据，返回 RawBar 列表和数据质量报告。"""
        raw_bars: list[RawBar] = []
        quality_report = {
            "raw_bar_count": len(self._data),
            "valid_bar_count": 0,
            "duplicate_count": 0,
            "missing_interval_count": 0,
            "parse_errors": 0,
            "status": "OK",
        }

        seen_timestamps: set[str] = set()
        prev_timestamp: Optional[datetime] = None

        for i, row in enumerate(self._data):
            try:
                ts = row.get("timestamp") or row.get("datetime") or row.get("date")
                if ts is None:
                    quality_report["parse_errors"] += 1
                    continue

                if isinstance(ts, str):
                    timestamp = datetime.fromisoformat(ts)
                elif isinstance(ts, datetime):
                    timestamp = ts
                else:
                    quality_report["parse_errors"] += 1
                    continue

                bar = RawBar(
                    bar_id=f"bar_{i + 1:06d}",
                    bar_index=i,
                    timestamp=timestamp,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0) or 0),
                )

                # 检查重复时间戳
                ts_key = timestamp.isoformat()
                if ts_key in seen_timestamps:
                    quality_report["duplicate_count"] += 1
                    bar.is_valid = False
                    bar.validation_errors.append("duplicate timestamp")
                seen_timestamps.add(ts_key)

                # 检查时间单调性
                if prev_timestamp is not None and timestamp <= prev_timestamp:
                    bar.is_valid = False
                    bar.validation_errors.append("non-monotonic timestamp")

                prev_timestamp = timestamp

                if bar.is_valid:
                    quality_report["valid_bar_count"] += 1

                raw_bars.append(bar)

            except (ValueError, KeyError, TypeError):
                quality_report["parse_errors"] += 1

        # 检查缺失区间
        valid_bars = [b for b in raw_bars if b.is_valid]
        if len(valid_bars) >= 2:
            for i in range(1, len(valid_bars)):
                gap = valid_bars[i].bar_index - valid_bars[i - 1].bar_index
                if gap > 1:
                    quality_report["missing_interval_count"] += gap - 1

        # 计算校验和
        self._input_checksum = hashlib.sha256(
            "|".join(b.content_hash() for b in raw_bars if b.is_valid).encode()
        ).hexdigest()[:16]

        if quality_report["parse_errors"] > 0 or quality_report["duplicate_count"] > 0:
            quality_report["status"] = "WARNING"
        if quality_report["valid_bar_count"] == 0:
            quality_report["status"] = "ERROR"

        return raw_bars, quality_report

    @property
    def input_checksum(self) -> Optional[str]:
        return self._input_checksum
