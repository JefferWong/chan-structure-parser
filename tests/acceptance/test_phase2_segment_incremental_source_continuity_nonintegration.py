"""PR15 source continuity remains pure and runtime-disconnected."""
from __future__ import annotations

import ast
from pathlib import Path

from chan_parser.engine.incremental import IncrementalEngine


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/chan_parser"
CONTRACT = SOURCE / "contracts/segment_incremental_source_continuity.py"
PR14_CONTRACT = SOURCE / "contracts/segment_incremental_reconciliation.py"
PR14_SHA256 = "91ccab73a14dfdb33b42d5775c117a353be3cc9ac6d6eb48ea7b268f21082659"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _import_paths(tree: ast.AST) -> set[str]:
    paths = set()
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


def test_contract_imports_only_domain_records_and_standard_library():
    assert _import_paths(_tree(CONTRACT)) == {
        "__future__",
        "collections.abc",
        "dataclasses",
        "enum",
        "..domain.lifecycle",
        "..domain.segment",
        "..domain.stroke",
    }


def test_contract_has_no_second_authority_or_runtime_calls():
    text = CONTRACT.read_text(encoding="utf-8")
    calls = _terminal_calls(_tree(CONTRACT))
    for forbidden in (
        "hashlib",
        "SegmentEngineResult",
        "SegmentEngine",
        "segment_rules",
        "EventLog",
        "SegmentLifecycleEmitter",
        "segment_checkpoint",
        "RETAIN_PREVIOUS",
        "INVALIDATE_PREVIOUS",
        "REVISE",
        "REPLACE",
        "DELETE",
        "PUBLISH",
        "SEGMENT_FEATURE_WINDOW_INCOMPLETE",
        "SEGMENT_PRIMARY_FRACTAL_NOT_FOUND",
        "SEGMENT_SECOND_CASE_PENDING",
    ):
        assert forbidden not in text
    imports = _import_paths(_tree(CONTRACT))
    assert not any(
        name.endswith("engine.incremental") or name.endswith("engine.full_rebuild")
        for name in imports
    )
    assert "content_hash" in calls
    assert not {"process_primary", "append_batch", "record", "emit", "restore"}.intersection(calls)


def test_no_other_production_module_imports_continuity_contract():
    importers = {
        path
        for path in SOURCE.rglob("*.py")
        if path != CONTRACT
        and "segment_incremental_source_continuity" in path.read_text(encoding="utf-8")
    }
    assert importers == set()


def test_incremental_output_remains_segment_free_and_policy_free():
    state = IncrementalEngine({}).append_batch([])
    assert set(state["structures"]) == {"merged_bars", "fractals", "strokes"}
    assert "segments" not in state["structures"]
    assert not any(event["object_type"] == "segment" for event in state["events"])


def test_pr14_contract_is_byte_exact_frozen():
    import hashlib

    assert hashlib.sha256(PR14_CONTRACT.read_bytes()).hexdigest() == PR14_SHA256
