"""
输出序列化器。

将结构分析结果序列化为 JSON，支持确定性哈希计算。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any


class Serializer:
    """JSON 序列化器。

    支持：
    - 标准 JSON 输出
    - 确定性内容哈希（排除时间戳等非确定性字段）
    - 紧凑/美化两种格式
    """

    def __init__(self, exclude_hash_fields: list[str] = None):
        self.exclude_hash_fields = exclude_hash_fields or [
            "generated_at",
            "engine_version",
            "profile_version",
        ]

    def serialize(self, output: dict[str, Any], pretty: bool = True) -> str:
        """序列化为 JSON 字符串。

        Args:
            output: 结构分析输出字典
            pretty: 是否美化输出

        Returns:
            JSON 字符串
        """
        # 添加生成时间戳
        output_with_meta = dict(output)
        if "meta" in output_with_meta:
            output_with_meta["meta"]["generated_at"] = datetime.now().isoformat()

        indent = 2 if pretty else None
        return json.dumps(output_with_meta, indent=indent, ensure_ascii=False, default=str)

    def save(self, output: dict[str, Any], filepath: str, pretty: bool = True) -> str:
        """序列化并保存到文件。

        Returns:
            文件内容的 SHA256 哈希
        """
        json_str = self.serialize(output, pretty=pretty)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(json_str)

        return hashlib.sha256(json_str.encode()).hexdigest()

    def compute_content_hash(self, output: dict[str, Any]) -> str:
        """计算内容的确定性哈希（排除非确定字段）。

        只对已确认结构计算哈希，未确认的尾部结构不参与。
        """
        # 深拷贝并清理非确定字段
        cleaned = self._clean_for_hashing(output)
        payload = json.dumps(cleaned, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def _clean_for_hashing(self, obj: Any) -> Any:
        """递归清理对象，排除非确定字段。"""
        if isinstance(obj, dict):
            result = {}
            for k, v in obj.items():
                if k in self.exclude_hash_fields:
                    continue
                # 只保留已确认的结构
                if k == "structures":
                    result[k] = self._filter_confirmed(v)
                else:
                    result[k] = self._clean_for_hashing(v)
            return result
        elif isinstance(obj, list):
            return [self._clean_for_hashing(item) for item in obj]
        else:
            return obj

    def _filter_confirmed(self, structures: dict) -> dict:
        """只保留已确认的结构用于哈希。"""
        result = {}
        for key, items in structures.items():
            if isinstance(items, list):
                result[key] = [
                    item for item in items
                    if isinstance(item, dict) and item.get("status") == "CONFIRMED"
                ]
            else:
                result[key] = items
        return result
