from __future__ import annotations

import ast
from pathlib import Path

import pytest

from chan_parser.contracts.segment_incremental_materialization import (
    SegmentIncrementalMaterializationError,
    materialize_incremental_segment,
)
from chan_parser.domain.lifecycle import StructureStatus, StrokeDirection
from chan_parser.domain.segment import Segment
from chan_parser.domain.stroke import Stroke
from chan_parser.engine.incremental import IncrementalEngine
from chan_parser.engine.segment import SegmentEngineResult


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/chan_parser"
MATERIALIZER = SOURCE / "contracts/segment_incremental_materialization.py"


def _source_strokes() -> tuple[Stroke, ...]:
    return tuple(
        Stroke(
            object_id=f"stroke-object-{index}",
            logical_id=f"stroke:logical:{index}",
            revision=1,
            status=StructureStatus.CONFIRMED,
            stroke_id=f"stroke_{index + 1:06d}",
            direction=(
                StrokeDirection.UP if index % 2 == 0 else StrokeDirection.DOWN
            ),
            start_fractal_id=f"fractal_{index:06d}",
            end_fractal_id=f"fractal_{index + 1:06d}",
            start_price=float(index),
            end_price=float(index + 1),
            start_bar_index=index,
            end_bar_index=index + 1,
        )
        for index in range(5)
    )


def _previous_segment() -> Segment:
    return Segment(
        object_id="segment_000001_000004_U_r1",
        logical_id="segment:logical:1",
        revision=1,
        status=StructureStatus.CONFIRMED,
        created_at_bar=4,
        confirmed_at_bar=4,
        rule_profile="minimal_segment_engine_core_v1",
        rule_version="0.1.0",
        segment_id="segment_000001_000004_U",
        direction=StrokeDirection.UP,
        start_stroke_id="stroke_000001",
        end_stroke_id="stroke_000003",
        stroke_ids=["stroke_000001", "stroke_000002", "stroke_000003"],
        feature_sequence_stroke_ids=["stroke_000002"],
        destruction_evidence_stroke_ids=["stroke_000002"],
        start_price=0.0,
        end_price=3.0,
        start_bar_index=0,
        end_bar_index=3,
        confirmation_requirements=[],
        repaint_risk="NONE",
    )


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_materializer_is_pure_and_runtime_disconnected():
    text = MATERIALIZER.read_text(encoding="utf-8")
    for forbidden in (
        "IncrementalEngine",
        "EventLog",
        "SegmentLifecycleEmitter",
        "segment_lifecycle",
        "segment_checkpoint",
        "engine.incremental",
        "process_primary",
        "SECOND_CASE",
        "REPLACE_REQUIRED",
        "hashlib",
        "uuid",
    ):
        assert forbidden not in text
    for path in SOURCE.rglob("*.py"):
        if path not in {MATERIALIZER, SOURCE / "engine/incremental.py"}:
            assert "segment_incremental_materialization" not in path.read_text(
                encoding="utf-8"
            )


def test_materializer_has_only_contract_and_record_authority_imports():
    imports = set()
    for node in ast.walk(_tree(MATERIALIZER)):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(f'{"." * node.level}{node.module or ""}')
    assert imports == {
        "__future__",
        "copy",
        "dataclasses",
        "typing",
        "..domain.lifecycle",
        "..domain.segment",
        "..domain.stroke",
        "..engine.segment",
        ".segment_incremental_reconciliation",
    }


def test_incremental_output_remains_segment_free():
    state = IncrementalEngine({}).append_batch([])
    assert set(state["structures"]) == {"merged_bars", "fractals", "strokes"}
    assert "segments" not in state["structures"]
    assert not any(event["object_type"] == "segment" for event in state["events"])


def test_second_case_is_not_materialized_by_this_contract():
    result = SegmentEngineResult(
        reason_code="SEGMENT_SECOND_CASE_PENDING",
        candidate_direction=StrokeDirection.UP,
        feature_elements=(),
        pending_second_case=object(),
    )
    assert result.segment is None
    assert result.completed is False
    assert result.pending_second_case is not None
    with pytest.raises(SegmentIncrementalMaterializationError) as raised:
        materialize_incremental_segment(
            previous=_previous_segment(),
            current=result,
            source_strokes=_source_strokes(),
        )
    assert raised.value.reason_code == (
        "SEGMENT_RECONCILIATION_PREVIOUS_WITH_NONMATERIALIZED_CURRENT_UNSUPPORTED"
    )
