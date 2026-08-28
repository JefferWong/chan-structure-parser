"""
CSV 数据适配器。

从 CSV 文件加载 OHLCV 数据，转换为 RawBar 列表。
"""

from __future__ import annotations

import csv
import hashlib
from datetime import datetime
from typing import Any, Optional

from ..domain.raw_bar import RawBar


class CSVAdapter:
    """CSV 文件数据适配器。

    支持标准 OHLCV 格式的 CSV 文件。
    自动检测列映射。
    """

    # 常见列名映射
    COLUMN_ALIASES = {
        "timestamp": ["timestamp", "datetime", "date", "time", "trade_date"],
        "open": ["open", "o"],
        "high": ["high", "h"],
        "low": ["low", "l"],
        "close": ["close", "c"],
        "volume": ["volume", "vol", "v"],
    }

    def __init__(self, filepath: str):
        self.filepath = filepath
        self._column_map: dict[str, str] = {}
        self._input_checksum: Optional[str] = None

    def load(self) -> tuple[list[RawBar], dict[str, Any]]:
        """加载 CSV 文件，返回 RawBar 列表和数据质量报告。

        Returns:
            (raw_bars, quality_report)
        """
        raw_bars: list[RawBar] = []
        quality_report = {
            "raw_bar_count": 0,
            "valid_bar_count": 0,
            "duplicate_count": 0,
            "missing_interval_count": 0,
            "parse_errors": 0,
            "status": "OK",
        }

        with open(self.filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                quality_report["status"] = "ERROR"
                quality_report["error"] = "Empty CSV or no header"
                return [], quality_report

            # 自动检测列映射
            self._detect_columns(reader.fieldnames)

            seen_timestamps: set[str] = set()
            prev_timestamp: Optional[datetime] = None

            for line_num, row in enumerate(reader, start=2):  # line 1 = header
                quality_report["raw_bar_count"] += 1

                try:
                    bar = self._parse_row(row)
                    if bar is None:
                        quality_report["parse_errors"] += 1
                        continue

                    # Identity is assigned from successfully materialized rows,
                    # not source line numbers.  Parse-error rows therefore do
                    # not create gaps in the runtime sequence.
                    materialized_index = len(raw_bars)
                    bar.bar_id = f"bar_{materialized_index + 1:06d}"
                    bar.bar_index = materialized_index

                    # 检查重复时间戳
                    ts_key = bar.timestamp.isoformat()
                    if ts_key in seen_timestamps:
                        quality_report["duplicate_count"] += 1
                        bar.is_valid = False
                        bar.validation_errors.append("duplicate timestamp")
                    seen_timestamps.add(ts_key)

                    # 检查时间单调性
                    if prev_timestamp is not None and bar.timestamp <= prev_timestamp:
                        bar.is_valid = False
                        bar.validation_errors.append("non-monotonic timestamp")

                    prev_timestamp = bar.timestamp
                    bar.source_line = line_num

                    if bar.is_valid:
                        quality_report["valid_bar_count"] += 1

                    raw_bars.append(bar)

                except Exception as e:
                    quality_report["parse_errors"] += 1

        # 计算输入校验和
        self._input_checksum = self._compute_checksum(raw_bars)

        # 更新状态
        if quality_report["parse_errors"] > 0 or quality_report["duplicate_count"] > 0:
            quality_report["status"] = "WARNING"
        if quality_report["valid_bar_count"] == 0:
            quality_report["status"] = "ERROR"

        return raw_bars, quality_report

    def _detect_columns(self, headers: list[str]) -> None:
        """自动检测列名映射。"""
        headers_lower = [h.lower().strip() for h in headers]
        for target, aliases in self.COLUMN_ALIASES.items():
            for alias in aliases:
                if alias in headers_lower:
                    idx = headers_lower.index(alias)
                    self._column_map[target] = headers[idx]
                    break

        required = ["timestamp", "open", "high", "low", "close"]
        missing = [r for r in required if r not in self._column_map]
        if missing:
            raise ValueError(
                f"Missing required columns: {missing}. "
                f"Available headers: {headers}"
            )

    def _parse_row(self, row: dict[str, str]) -> Optional[RawBar]:
        """解析一行CSV数据。"""
        try:
            ts_col = self._column_map["timestamp"]
            timestamp = self._parse_timestamp(row[ts_col])

            bar = RawBar(
                bar_id="",  # 稍后分配
                bar_index=0,  # 稍后分配
                timestamp=timestamp,
                open=float(row[self._column_map["open"]]),
                high=float(row[self._column_map["high"]]),
                low=float(row[self._column_map["low"]]),
                close=float(row[self._column_map["close"]]),
                volume=float(row.get(self._column_map.get("volume", ""), 0) or 0),
            )
            return bar
        except (ValueError, KeyError) as e:
            return None

    def _parse_timestamp(self, value: str) -> datetime:
        """解析时间戳字符串，支持多种格式。"""
        value = value.strip()

        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d",
            "%Y%m%d",
            "%Y%m%d%H%M%S",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue

        # 尝试 ISO 格式
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass

        raise ValueError(f"Cannot parse timestamp: {value}")

    def _compute_checksum(self, bars: list[RawBar]) -> str:
        """计算输入数据的校验和。"""
        payload = "|".join(b.content_hash() for b in bars if b.is_valid)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    @property
    def input_checksum(self) -> Optional[str]:
        return self._input_checksum
