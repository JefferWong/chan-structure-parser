"""Stage C-A Segment checkpoint contract remains pure and non-integrated."""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/chan_parser"
CONTRACT = SOURCE / "contracts/segment_checkpoint.py"
PROFILE = ROOT / "configs/profiles/minimal_segment_checkpoint_contract_v1.yaml"
STILL_FROZEN_SEGMENT_RUNTIME_SHA256 = {
    SOURCE / "engine/incremental.py": (
        "aef46f378607560c0c2fb0015f0041b8c7092a01292560c1a5fb6c1563cd9cd8"
    ),
    SOURCE / "engine/segment.py": (
        "26c3c48edcdd71f5856af0ffde90e670899ae8372be229dff3b2fef7d97ce9ea"
    ),
    SOURCE / "engine/segment_lifecycle_emitter.py": (
        "5049668739f6b71633e083d5ba14145964c536dd1e20e879e3ad7c4e39497f73"
    ),
}
ALLOWED_IMPORTS = {
    "__future__",
    "collections.abc",
    "dataclasses",
    "hashlib",
    "itertools",
    "json",
    "math",
    "re",
    "typing",
    "..domain.lifecycle",
    "..domain.segment",
    "..domain.stroke",
}
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


def _terminal_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _imports_are_allowlisted(tree: ast.AST) -> bool:
    return _import_paths(tree) <= ALLOWED_IMPORTS


def test_checkpoint_contract_imports_only_domain_records_and_standard_library():
    imports = _import_paths(_tree(CONTRACT))
    assert imports == ALLOWED_IMPORTS
    assert _imports_are_allowlisted(_tree(CONTRACT))


@pytest.mark.parametrize(
    "source",
    [
        "from ..engine import segment",
        "from .. import engine",
        "from chan_parser.engine import segment",
        "import chan_parser.engine.segment",
        "from ..contracts import segment_rules",
        "from ..audit import event_log",
    ],
)
def test_import_allowlist_rejects_module_path_bypasses(source):
    tree = ast.parse(source)
    assert not _imports_are_allowlisted(tree)


def test_checkpoint_contract_makes_zero_canonical_segment_rule_oracle_calls():
    calls = {
        name
        for node in ast.walk(_tree(CONTRACT))
        if isinstance(node, ast.Call)
        if (name := _terminal_name(node)) is not None
    }
    assert not ORACLE_CALLS.intersection(calls)


def test_checkpoint_contract_does_not_own_logs_events_engines_or_restoration():
    tree = _tree(CONTRACT)
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "EventLog" not in imported_names
    assert "LifecycleEvent" not in imported_names
    assert "SegmentEngine" not in imported_names
    assert "SegmentLifecycleEmitter" not in imported_names
    assert not {
        "emit",
        "record",
        "restore",
        "snapshot",
        "process_primary",
        "append_batch",
    }.intersection(
        {
            name
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            if (name := _terminal_name(node)) is not None
        }
    )


def test_public_contract_surface_and_all_are_exact():
    public_functions = {
        node.name
        for node in _tree(CONTRACT).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }
    assert public_functions == {
        "validate_segment_checkpoint_profile",
        "derive_segment_checkpoint_state",
        "validate_segment_checkpoint_state",
    }
    all_assignments = [
        node
        for node in _tree(CONTRACT).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
    ]
    assert len(all_assignments) == 1
    assert ast.literal_eval(all_assignments[0].value) == (
        "SegmentCheckpointContractError",
        "SegmentCheckpointState",
        "validate_segment_checkpoint_profile",
        "derive_segment_checkpoint_state",
        "validate_segment_checkpoint_state",
    )


def test_checkpoint_contract_is_not_imported_by_any_other_production_module():
    importers = {
        path
        for path in SOURCE.rglob("*.py")
        if path != CONTRACT
        and "segment_checkpoint" in path.read_text(encoding="utf-8")
    }
    assert importers == set()


def test_still_frozen_segment_runtime_production_files_equal_exact_base_bytes():
    actual = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in STILL_FROZEN_SEGMENT_RUNTIME_SHA256
    }
    assert actual == STILL_FROZEN_SEGMENT_RUNTIME_SHA256


def test_profile_is_exact_contract_only_semantic_state_and_nonintegration():
    loaded = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
    assert loaded == {
        "profile_id": "minimal_segment_checkpoint_contract_v1",
        "profile_version": "0.1.0",
        "status": "CONTRACT_ONLY",
        "source_segment_profile_id": "minimal_segment_engine_core_v1",
        "source_segment_profile_version": "0.1.0",
        "source_lifecycle_emission_profile_id": (
            "minimal_segment_lifecycle_emission_v1"
        ),
        "source_lifecycle_emission_profile_version": "0.1.0",
        "source_lifecycle_emission_baseline_commit": (
            "937ad3a3d805fd36527a8c295e04141232e53a1e"
        ),
        "checkpoint": {
            "semantic_state_only": True,
            "event_log_snapshot_owned_elsewhere": True,
            "partial_lifecycle_prefix_allowed": False,
            "second_case_state_capture_allowed": True,
            "second_case_orchestration_enabled": False,
        },
        "binding": {
            "source_strokes_required": True,
            "source_stroke_content_hashes_required": True,
            "source_stroke_semantic_hashes_required": True,
            "first_case_segment_binding_required": True,
            "segment_semantic_hash_required": True,
            "first_case_complete_lifecycle_pair_required": True,
            "canonical_intent_key_validation_required": True,
            "lifecycle_event_semantic_hashes_required": True,
            "zero_event_outcomes_require_empty_lifecycle_slice": True,
        },
        "integration": {
            "full_rebuild_integration_enabled": False,
            "incremental_integration_enabled": False,
            "checkpoint_runtime_integration_enabled": False,
            "bounded_tail_segment_recompute_enabled": False,
            "parser_integration_enabled": False,
            "center_or_zhongshu_enabled": False,
        },
    }


def test_checkpoint_contract_contains_no_runtime_or_future_stage_symbols():
    text = CONTRACT.read_text(encoding="utf-8")
    for forbidden in (
        "FullRebuildEngine",
        "IncrementalEngine",
        "SegmentEngine",
        "SegmentLifecycleEmitter",
        "PendingSecondCaseContext",
        "Center",
        "Zhongshu",
    ):
        assert forbidden not in text
