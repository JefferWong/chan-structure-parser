from __future__ import annotations

import ast
from pathlib import Path

from chan_parser.domain.lifecycle import StrokeDirection
from chan_parser.engine.incremental import IncrementalEngine
from chan_parser.engine.segment import SegmentEngineResult


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/chan_parser"
CONTRACT = SOURCE / "contracts/segment_incremental_replacement.py"
FROZEN_RUNTIME_FILES = (
    SOURCE / "engine/incremental.py",
    SOURCE / "engine/full_rebuild.py",
    SOURCE / "engine/segment_lifecycle_emitter.py",
    SOURCE / "contracts/segment_checkpoint.py",
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _import_paths(tree: ast.AST) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(f'{"." * node.level}{node.module or ""}')
    return imports


def test_replacement_contract_is_pure_and_runtime_disconnected():
    text = CONTRACT.read_text(encoding="utf-8")
    for forbidden in (
        "IncrementalEngine",
        "FullRebuild",
        "EventLog",
        "segment_lifecycle_emitter",
        "segment_checkpoint",
        "engine.incremental",
        "hashlib",
        "uuid",
        "SECOND_CASE",
        "REVISE",
    ):
        assert forbidden not in text
    assert "derive_segment_lifecycle_intents" in text
    assert "mark_replaced" in text
    for path in SOURCE.rglob("*.py"):
        if path not in {CONTRACT, SOURCE / "engine/incremental.py"}:
            assert "segment_incremental_replacement" not in path.read_text(
                encoding="utf-8"
            )


def test_replacement_contract_imports_only_existing_authorities():
    assert _import_paths(_tree(CONTRACT)) == {
        "__future__",
        "collections.abc",
        "copy",
        "dataclasses",
        "..contracts.segment_lifecycle",
        "..domain.lifecycle",
        "..domain.segment",
        "..domain.stroke",
        "..engine.segment",
        ".segment_incremental_reconciliation",
    }


def test_runtime_surfaces_remain_segment_free_and_unmodified_by_replacement_contract():
    state = IncrementalEngine({}).append_batch([])
    assert set(state["structures"]) == {"merged_bars", "fractals", "strokes"}
    assert "segments" not in state["structures"]
    assert not any(event["object_type"] == "segment" for event in state["events"])
    for path in FROZEN_RUNTIME_FILES:
        if path == SOURCE / "engine/incremental.py":
            continue
        assert "segment_incremental_replacement" not in path.read_text(
            encoding="utf-8"
        )


def test_second_case_remains_unmaterialized_and_has_no_replacement_path():
    result = SegmentEngineResult(
        reason_code="SEGMENT_SECOND_CASE_PENDING",
        candidate_direction=StrokeDirection.UP,
        feature_elements=(),
        pending_second_case=object(),
    )
    assert result.segment is None
    assert result.completed is False
    assert result.pending_second_case is not None
