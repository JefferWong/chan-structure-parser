"""
结构谱系追踪。

追踪每个结构对象的完整血缘关系：
- 由哪些输入对象派生
- 经历了哪些修订
- 被什么对象替换
- 影响了哪些下游对象
"""

from __future__ import annotations

from typing import Any, Optional


class LineageTracker:
    """结构谱系追踪器。

    维护一个有向无环图，记录结构对象之间的派生关系。
    """

    def __init__(self):
        # object_id -> lineage node
        self._nodes: dict[str, dict[str, Any]] = {}
        # logical_id -> list of object_ids (按revision排序)
        self._logical_groups: dict[str, list[str]] = {}

    def register(
        self,
        object_id: str,
        logical_id: str,
        object_type: str,
        revision: int,
        parent_ids: list[str] = None,
        created_at_bar: int = -1,
    ) -> None:
        """注册一个结构对象。"""
        self._nodes[object_id] = {
            "object_id": object_id,
            "logical_id": logical_id,
            "object_type": object_type,
            "revision": revision,
            "parent_ids": parent_ids or [],
            "child_ids": [],
            "created_at_bar": created_at_bar,
            "replaced_by": None,
            "invalidated_at_bar": None,
        }

        if logical_id not in self._logical_groups:
            self._logical_groups[logical_id] = []
        self._logical_groups[logical_id].append(object_id)

        # 更新父节点的child_ids
        for pid in (parent_ids or []):
            if pid in self._nodes:
                self._nodes[pid]["child_ids"].append(object_id)

    def mark_replaced(self, old_id: str, new_id: str) -> None:
        """标记一个对象被另一个对象替换。"""
        if old_id in self._nodes:
            self._nodes[old_id]["replaced_by"] = new_id

    def mark_invalidated(self, object_id: str, at_bar: int) -> None:
        """标记一个对象失效。"""
        if object_id in self._nodes:
            self._nodes[object_id]["invalidated_at_bar"] = at_bar

    def get_ancestors(self, object_id: str) -> list[str]:
        """获取某对象的所有祖先（递归）。"""
        result = []
        visited = set()

        def _walk(oid: str):
            if oid in visited:
                return
            visited.add(oid)
            if oid in self._nodes:
                for pid in self._nodes[oid]["parent_ids"]:
                    result.append(pid)
                    _walk(pid)

        _walk(object_id)
        return result

    def get_descendants(self, object_id: str) -> list[str]:
        """获取某对象的所有后代（递归）。"""
        result = []
        visited = set()

        def _walk(oid: str):
            if oid in visited:
                return
            visited.add(oid)
            if oid in self._nodes:
                for cid in self._nodes[oid]["child_ids"]:
                    result.append(cid)
                    _walk(cid)

        _walk(object_id)
        return result

    def get_revision_chain(self, logical_id: str) -> list[str]:
        """获取某个逻辑身份的所有修订版（按revision排序）。"""
        obj_ids = self._logical_groups.get(logical_id, [])
        # 按revision排序
        return sorted(
            obj_ids,
            key=lambda oid: self._nodes.get(oid, {}).get("revision", 0),
        )

    def get_lineage_report(self, object_id: str) -> dict[str, Any]:
        """获取某个对象的完整谱系报告。"""
        node = self._nodes.get(object_id, {})
        return {
            "object_id": object_id,
            "logical_id": node.get("logical_id"),
            "object_type": node.get("object_type"),
            "revision": node.get("revision"),
            "parents": node.get("parent_ids", []),
            "children": node.get("child_ids", []),
            "ancestors": self.get_ancestors(object_id),
            "descendants": self.get_descendants(object_id),
            "revision_chain": self.get_revision_chain(
                node.get("logical_id", "")
            ),
            "replaced_by": node.get("replaced_by"),
            "created_at_bar": node.get("created_at_bar"),
            "invalidated_at_bar": node.get("invalidated_at_bar"),
        }

    def to_dict(self) -> dict[str, Any]:
        """导出完整谱系图。"""
        return {
            "nodes": self._nodes,
            "logical_groups": {
                lid: ids for lid, ids in self._logical_groups.items()
            },
        }
