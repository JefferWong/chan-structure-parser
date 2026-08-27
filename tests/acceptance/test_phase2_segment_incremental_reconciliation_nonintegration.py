"""PR14 reconciliation authority remains pure and runtime-disconnected."""
from __future__ import annotations

import ast
from pathlib import Path

from chan_parser.domain.lifecycle import StrokeDirection
from chan_parser.engine.incremental import IncrementalEngine
from chan_parser.engine.segment import SegmentEngineResult


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/chan_parser"
CONTRACT = SOURCE / "contracts/segment_incremental_reconciliation.py"
MATERIALIZER = SOURCE / "contracts/segment_incremental_materialization.py"
REPLACEMENT = SOURCE / "contracts/segment_incremental_replacement.py"
FROZEN_RUNTIME_FILES = (
    SOURCE / "engine/incremental.py",
    SOURCE / "engine/full_rebuild.py",
    SOURCE / "engine/segment.py",
    SOURCE / "engine/segment_lifecycle_emitter.py",
    SOURCE / "contracts/segment_checkpoint.py",
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _import_paths(tree: ast.AST) -> set[str]:
    paths: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            paths.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            paths.add(f'{"." * node.level}{node.module or ""}')
    return paths


def _terminal_calls(tree: ast.AST) -> set[str]:
    calls = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            calls.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            calls.add(node.func.attr)
    return calls


def _assigned_names(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            for target in targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                elif isinstance(target, ast.Attribute):
                    names.add(target.attr)
    return names


def test_contract_has_only_existing_record_authority_dependencies():
    imports = _import_paths(_tree(CONTRACT))
    assert imports == {
        "__future__",
        "collections.abc",
        "dataclasses",
        "enum",
        "..domain.lifecycle",
        "..domain.segment",
        "..domain.stroke",
        "..engine.segment",
        ".segment_incremental_source_continuity",
    }
    text = CONTRACT.read_text(encoding="utf-8")
    for forbidden in (
        "segment_rules",
        "event_log",
        "segment_lifecycle_emitter",
        "segment_checkpoint",
        "engine.incremental",
    ):
        assert forbidden not in text
    assert "process_primary" not in _terminal_calls(_tree(CONTRACT))


def test_contract_does_not_construct_segments_or_duplicate_identity_algorithms():
    tree = _tree(CONTRACT)
    calls = _terminal_calls(tree)
    assert "Segment" not in calls
    text = CONTRACT.read_text(encoding="utf-8")
    assert not {"segment_id", "logical_id", "object_id"}.intersection(
        _assigned_names(tree)
    )
    assert "hashlib" not in text
    assert ".content_hash()" in text
    string_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    for identity_literal in ("segment:", "_r1", "_r2", "sha256"):
        assert not any(identity_literal in value for value in string_literals)


def test_runtime_checkpoint_and_parser_sources_do_not_import_contract():
    for path in SOURCE.rglob("*.py"):
        if path in {CONTRACT, MATERIALIZER, REPLACEMENT}:
            continue
        assert "segment_incremental_reconciliation" not in path.read_text(encoding="utf-8")
    for path in FROZEN_RUNTIME_FILES:
        assert "segment_incremental_reconciliation" not in path.read_text(encoding="utf-8")


def test_incremental_output_and_lifecycle_remain_segment_free():
    state = IncrementalEngine({}).append_batch([])
    assert set(state["structures"]) == {"merged_bars", "fractals", "strokes"}
    assert "segments" not in state["structures"]
    assert not any(event["object_type"] == "segment" for event in state["events"])


def test_second_case_remains_pending_data_and_cannot_materialize_here():
    result = SegmentEngineResult(
        reason_code="SEGMENT_SECOND_CASE_PENDING",
        candidate_direction=StrokeDirection.UP,
        feature_elements=(),
        pending_second_case=object(),
    )
    assert result.segment is None
    assert result.completed is False
    assert result.pending_second_case is not None
    assert "build_pending_second_case_context" not in _terminal_calls(_tree(CONTRACT))
