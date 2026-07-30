"""Phase 2 segment domain schema.

This module defines the serializable lifecycle object only. It does not
construct segments and is intentionally not wired into the Phase 1 engines.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .lifecycle import StructureObject, StrokeDirection


@dataclass
class Segment(StructureObject):
    """A segment record backed by explicit stroke evidence."""

    segment_id: str = ""
    direction: StrokeDirection = StrokeDirection.UP
    start_stroke_id: str = ""
    end_stroke_id: str = ""
    stroke_ids: list[str] = field(default_factory=list)
    feature_sequence_stroke_ids: list[str] = field(default_factory=list)
    destruction_evidence_stroke_ids: list[str] = field(default_factory=list)
    start_price: float = 0.0
    end_price: float = 0.0
    start_bar_index: int = -1
    end_bar_index: int = -1
    confirmation_requirements: list[str] = field(default_factory=list)
    repaint_risk: str = "HIGH"

    def to_dict(self) -> dict:
        return {
            "object_id": self.object_id,
            "logical_id": self.logical_id,
            "revision": self.revision,
            "status": self.status.value,
            "segment_id": self.segment_id,
            "direction": self.direction.value,
            "start_stroke_id": self.start_stroke_id,
            "end_stroke_id": self.end_stroke_id,
            "stroke_ids": list(self.stroke_ids),
            "feature_sequence_stroke_ids": list(self.feature_sequence_stroke_ids),
            "destruction_evidence_stroke_ids": list(
                self.destruction_evidence_stroke_ids
            ),
            "start_price": self.start_price,
            "end_price": self.end_price,
            "start_bar_index": self.start_bar_index,
            "end_bar_index": self.end_bar_index,
            "confirmation_requirements": list(self.confirmation_requirements),
            "repaint_risk": self.repaint_risk,
            "created_at_bar": self.created_at_bar,
            "confirmed_at_bar": self.confirmed_at_bar,
            "invalidated_at_bar": self.invalidated_at_bar,
            "replaced_by": self.replaced_by,
            "rule_profile": self.rule_profile,
            "rule_version": self.rule_version,
        }

    def content_hash(self) -> str:
        payload = "|".join(
            [
                self.direction.value,
                self.start_stroke_id,
                self.end_stroke_id,
                ",".join(self.stroke_ids),
                ",".join(self.feature_sequence_stroke_ids),
                ",".join(self.destruction_evidence_stroke_ids),
                str(self.start_price),
                str(self.end_price),
                str(self.start_bar_index),
                str(self.end_bar_index),
                self.rule_profile,
                self.rule_version,
            ]
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]
