"""Stage B emitter is the sole narrow Segment lifecycle runtime boundary."""
from __future__ import annotations

import ast
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/chan_parser"
EMITTER = SOURCE / "engine/segment_lifecycle_emitter.py"
SEGMENT_ENGINE = SOURCE / "engine/segment.py"
FULL = SOURCE / "engine/full_rebuild.py"
INCREMENTAL = SOURCE / "engine/incremental.py"
ENGINE_INIT = SOURCE / "engine/__init__.py"
PROFILE = ROOT / "configs/profiles/minimal_segment_lifecycle_emission_v1.yaml"
ORACLE_CALLS = {
    "build_feature_sequence",
    "classify_primary_destruction_case",
    "confirmation_bar",
    "validate_segment_boundaries",
    "merge_included_intervals",
    "derive_inclusion_seed",
    "classify_interval_relation",
    "build_pending_second_case_context",
}


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _import_paths(tree: ast.AST) -> set[str]:
    paths: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            paths.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            paths.add(f'{"." * node.level}{node.module or ""}')
    return paths


def _imported_names(tree: ast.AST) -> set[str]:
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }


def _terminal_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def test_emitter_imports_exact_stage_b_authorities_without_segment_rules():
    tree = _tree(EMITTER)
    imports = _import_paths(tree)
    names = _imported_names(tree)
    assert "..audit.event_log" in imports
    assert "EventLog" in names
    assert "..contracts.segment_lifecycle" in imports
    assert "derive_segment_lifecycle_intents" in names
    assert "filter_new_segment_lifecycle_intents" in names
    assert ".segment" in imports
    assert "SegmentEngineResult" in names
    assert not any(path.endswith("segment_rules") for path in imports)
    assert "segment_rules" not in EMITTER.read_text(encoding="utf-8")


def test_emitter_makes_zero_canonical_oracle_calls():
    tree = _tree(EMITTER)
    calls = {
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        if (name := _terminal_name(node)) is not None
    }
    assert not ORACLE_CALLS.intersection(calls)


def test_no_engine_or_parser_path_imports_or_exports_emitter():
    for path in (SEGMENT_ENGINE, ENGINE_INIT):
        text = path.read_text(encoding="utf-8")
        assert "SegmentLifecycleEmitter" not in text
        assert "segment_lifecycle_emitter" not in text
    assert "SegmentLifecycleEmitter" in INCREMENTAL.read_text(encoding="utf-8")
    full_text = FULL.read_text(encoding="utf-8")
    assert "SegmentLifecycleEmitter" in full_text
    assert "segment_lifecycle_emitter" in full_text
    assert "SegmentLifecycleEmitter" not in ENGINE_INIT.read_text(encoding="utf-8")


def test_only_emitter_imports_lifecycle_contract_in_production():
    contract = SOURCE / "contracts/segment_lifecycle.py"
    importers = {
        path
        for path in SOURCE.rglob("*.py")
        if path != contract
        and "..contracts.segment_lifecycle" in _import_paths(_tree(path))
    }
    assert importers == {
        EMITTER,
        SOURCE / "contracts/segment_incremental_replacement.py",
    }


def test_parser_checkpoint_and_bounded_tail_remain_unintegrated():
    emitter_text = EMITTER.read_text(encoding="utf-8")
    imports = _import_paths(_tree(EMITTER))
    assert not any(
        forbidden in path
        for path in imports
        for forbidden in ("full_rebuild", "incremental", "checkpoint")
    )
    for token in (
        "FullRebuildEngine",
        "IncrementalEngine",
        "Checkpoint",
        "append_batch",
        "process_primary(",
    ):
        assert token not in emitter_text
    for path in SOURCE.rglob("*.py"):
        if path in {EMITTER, INCREMENTAL}:
            continue
        if path == FULL:
            continue
        assert "segment_lifecycle_emitter" not in path.read_text(encoding="utf-8")


def test_emission_profile_is_exactly_emitter_only_and_fail_closed():
    loaded = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
    assert loaded == {
        "profile_id": "minimal_segment_lifecycle_emission_v1",
        "profile_version": "0.1.0",
        "status": "EMITTER_ONLY",
        "source_lifecycle_contract_profile_id": (
            "minimal_segment_lifecycle_contract_v1"
        ),
        "source_lifecycle_contract_profile_version": "0.1.0",
        "source_lifecycle_contract_baseline_commit": (
            "f0b795f4487ec4713bed7a2a3abca14c7ae63f58"
        ),
        "source_segment_profile_id": "minimal_segment_engine_core_v1",
        "source_segment_profile_version": "0.1.0",
        "emission": {
            "event_emission_enabled": True,
            "event_id_authority": "EventLog",
            "intent_key_field": "segment_lifecycle_intent_key",
            "binding_key_field": "segment_lifecycle_binding_key",
            "canonical_prefix_history_required": True,
            "partial_created_recovery_enabled": True,
        },
        "binding": {
            "source_strokes_required_for_first_case": True,
            "exact_primary_feature_elements_required": True,
            "full_feature_visibility_revalidation_required": True,
            "ordered_feature_provenance_required": True,
            "caller_supplied_intents_allowed": False,
            "canonical_oracle_calls_allowed": False,
        },
        "integration": {
            "full_rebuild_reference_integration_enabled": True,
            "parser_integration_enabled": False,
            "checkpoint_integration_enabled": False,
            "bounded_tail_integration_enabled": False,
            "full_incremental_integration_enabled": False,
            "second_case_confirmation_enabled": False,
            "center_or_zhongshu_enabled": False,
        },
    }
