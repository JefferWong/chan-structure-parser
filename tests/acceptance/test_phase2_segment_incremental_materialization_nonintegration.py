from __future__ import annotations

import ast
from pathlib import Path

from chan_parser.domain.lifecycle import StrokeDirection
from chan_parser.engine.incremental import IncrementalEngine
from chan_parser.engine.segment import SegmentEngineResult


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/chan_parser"
MATERIALIZER = SOURCE / "contracts/segment_incremental_materialization.py"


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
        if path != MATERIALIZER:
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


def test_materializer_imports_only_contract_and_domain_authorities():
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
