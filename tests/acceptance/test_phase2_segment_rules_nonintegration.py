"""Canonical rules stay isolated; only the explicit SegmentEngine core may bind them."""
from __future__ import annotations

import ast
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from chan_parser.domain.raw_bar import RawBar
from chan_parser.engine.full_rebuild import FullRebuildEngine
from chan_parser.engine.incremental import IncrementalEngine


ROOT = Path(__file__).resolve().parents[2]
STRICT_PROFILE = ROOT / "configs/profiles/minimal_strict_v1.yaml"
RULE_PROFILE = ROOT / "configs/profiles/minimal_segment_canonical_rules_v1.yaml"
ENGINE_PROFILE = ROOT / "configs/profiles/minimal_segment_engine_core_v1.yaml"
ORACLE = ROOT / "src/chan_parser/contracts/segment_rules.py"
SEGMENT_ENGINE = ROOT / "src/chan_parser/engine/segment.py"
FULL = ROOT / "src/chan_parser/engine/full_rebuild.py"
SEGMENT_LIFECYCLE = (
    ROOT / "src/chan_parser/contracts/segment_lifecycle.py"
)
ALLOWED_LIFECYCLE_ORACLE_TYPES = {
    "DestructionCase",
    "PrimaryDestructionEvidence",
    "SegmentDirection",
}
CANONICAL_SEGMENT_RULES_MODULES = {
    "chan_parser.contracts.segment_rules",
    "..contracts.segment_rules",
}
FORBIDDEN_LIFECYCLE_ORACLE_CALLS = {
    "build_feature_sequence",
    "build_pending_second_case_context",
    "classify_interval_relation",
    "classify_primary_destruction_case",
    "classify_secondary_confirmation",
    "choose_deterministic_candidate",
    "confirmation_bar",
    "derive_inclusion_seed",
    "merge_included_intervals",
    "resolve_lifecycle",
    "resolve_second_case_evidence_sequence",
    "resolve_second_case_outcome",
    "validate_frozen_prefix_transition",
    "validate_segment_boundaries",
}


def _segment_rules_module_object_imports(tree: ast.AST) -> list[ast.AST]:
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            alias.name == "segment_rules"
            or alias.name.endswith(".segment_rules")
            for alias in node.names
        ):
            violations.append(node)
        elif isinstance(node, ast.ImportFrom) and any(
            alias.name == "segment_rules" for alias in node.names
        ):
            violations.append(node)
    return violations


def _is_direct_segment_rules_import(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.ImportFrom)
        and bool(node.module)
        and f'{"." * node.level}{node.module}'
        in CANONICAL_SEGMENT_RULES_MODULES
    )


def _is_noncanonical_segment_rules_import(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.ImportFrom)
        and bool(node.module)
        and node.module.split(".")[-1] == "segment_rules"
        and f'{"." * node.level}{node.module}'
        not in CANONICAL_SEGMENT_RULES_MODULES
    )


def _direct_segment_rules_imports(tree: ast.AST) -> list[ast.ImportFrom]:
    return [
        node
        for node in ast.walk(tree)
        if _is_direct_segment_rules_import(node)
    ]


def _call_terminal_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _lifecycle_oracle_authority_calls(tree: ast.AST) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _call_terminal_name(node) in FORBIDDEN_LIFECYCLE_ORACLE_CALLS
    ]


def _lifecycle_oracle_import_violations(tree: ast.AST) -> list[str]:
    violations = [
        "SEGMENT_RULES_MODULE_OBJECT_IMPORT"
        for _ in _segment_rules_module_object_imports(tree)
    ]
    direct_imports = _direct_segment_rules_imports(tree)
    violations.extend(
        "SEGMENT_RULES_NONCANONICAL_MODULE_PATH"
        for node in ast.walk(tree)
        if _is_noncanonical_segment_rules_import(node)
    )
    imported_names = {
        alias.name
        for node in direct_imports
        for alias in node.names
    }
    for node in direct_imports:
        if any(
            alias.name == "*"
            or alias.name not in ALLOWED_LIFECYCLE_ORACLE_TYPES
            or alias.asname is not None
            for alias in node.names
        ):
            violations.append("SEGMENT_RULES_DIRECT_IMPORT_NOT_TYPE_ONLY")
    if imported_names != ALLOWED_LIFECYCLE_ORACLE_TYPES:
        violations.append("SEGMENT_RULES_TYPE_WHITELIST_NOT_EXACT")
    violations.extend(
        "SEGMENT_RULES_ORACLE_AUTHORITY_CALL"
        for _ in _lifecycle_oracle_authority_calls(tree)
    )
    return violations


def bars() -> list[RawBar]:
    start = datetime(2024, 1, 2, 9, 30)
    return [
        RawBar(
            f"bar_{index + 1:06d}",
            index,
            start + timedelta(minutes=30 * index),
            100 + index,
            102 + index,
            99 + index,
            101 + index,
        )
        for index in range(12)
    ]


def test_phase1_parser_outputs_remain_segment_free():
    strict = yaml.safe_load(STRICT_PROFILE.read_text(encoding="utf-8"))
    full = FullRebuildEngine(strict).process(bars())
    incremental = IncrementalEngine(strict).append_batch(bars())
    assert "segments" not in full["structures"]
    assert "segments" not in incremental["structures"]


def test_rule_profile_explicitly_disables_implementation_and_integration():
    loaded = yaml.safe_load(RULE_PROFILE.read_text(encoding="utf-8"))
    assert loaded["status"] == "CANONICAL_RULES_ONLY"
    assert loaded["implementation_enabled"] is False
    assert loaded["parser_integration_enabled"] is False
    assert loaded["prohibited"] == {
        "segment_engine": True,
        "parser_integration": True,
        "center_or_zhongshu": True,
        "czsc_or_chanpy": True,
        "trading_signal": True,
        "position_or_execution": True,
    }


def test_engine_profile_enables_only_isolated_core_surface():
    loaded = yaml.safe_load(ENGINE_PROFILE.read_text(encoding="utf-8"))
    assert loaded["status"] == "ENGINE_CORE_ONLY"
    assert loaded["canonical_rules_profile_id"] == (
        "minimal_segment_canonical_rules_v1"
    )
    assert loaded["canonical_rules_profile_version"] == "1.0.1"
    assert loaded["implementation"] == {
        "primary_feature_adapter_enabled": True,
        "first_case_materialization_enabled": True,
        "second_case_orchestration_enabled": False,
        "lifecycle_events_enabled": False,
        "parser_integration_enabled": False,
        "checkpoint_integration_enabled": False,
        "full_incremental_integration_enabled": False,
    }
    assert all(loaded["prohibited"].values())


def test_reference_oracle_has_no_engine_parser_segment_or_event_dependencies():
    tree = ast.parse(ORACLE.read_text(encoding="utf-8"))
    imported_modules = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = f'{"." * node.level}{node.module or ""}'
        imported_modules.add(module)
        imported_modules.update(alias.name for alias in node.names)
        imported_modules.update(
            f"{module}.{alias.name}" if module else alias.name
            for alias in node.names
        )
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    forbidden_fragments = {
        "domain.segment", "engine.", "audit.event_log", "checkpoint",
        "czsc", "chan.py", "zhongshu", "trading",
    }
    assert not any(
        fragment in module.lower()
        for module in imported_modules
        for fragment in forbidden_fragments
    )
    class_names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
    }
    function_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "Segment" not in class_names
    assert not any(name.endswith("Engine") for name in class_names)
    assert not {"append", "append_one", "append_batch", "process"} & function_names


def test_reference_oracle_exposes_no_production_package_export():
    contracts_init = (ROOT / "src/chan_parser/contracts/__init__.py").read_text(
        encoding="utf-8"
    )
    assert "segment_rules" not in contracts_init


def test_only_segment_engine_core_may_import_or_call_reference_oracle():
    source_root = ROOT / "src/chan_parser"
    for path in source_root.rglob("*.py"):
        if path in {ORACLE, SEGMENT_ENGINE, SEGMENT_LIFECYCLE}:
            continue
        text = path.read_text(encoding="utf-8")
        assert "segment_rules" not in text, path
        tree = ast.parse(text)
        assert _segment_rules_module_object_imports(tree) == [], path
        assert _direct_segment_rules_imports(tree) == [], path
        assert _lifecycle_oracle_authority_calls(tree) == [], path
        # FullRebuild may obtain the isolated reference profile from the
        # SegmentEngine factory; it may not bind to the oracle itself.
        if path != FULL:
            assert "minimal_segment_canonical_rules_v1" not in text, path
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {
                "build_feature_sequence",
                "classify_primary_destruction_case",
                "classify_secondary_confirmation",
                "choose_deterministic_candidate",
                "resolve_lifecycle",
                "validate_frozen_prefix_transition",
            }
            for node in ast.walk(tree)
        ), path

    lifecycle_tree = ast.parse(SEGMENT_LIFECYCLE.read_text(encoding="utf-8"))
    lifecycle_oracle_imports = _direct_segment_rules_imports(lifecycle_tree)
    assert lifecycle_oracle_imports
    imported_lifecycle_names = {
        alias.name
        for node in lifecycle_oracle_imports
        for alias in node.names
    }
    assert imported_lifecycle_names == ALLOWED_LIFECYCLE_ORACLE_TYPES
    assert all(
        alias.asname is None
        for node in lifecycle_oracle_imports
        for alias in node.names
    )
    assert _lifecycle_oracle_import_violations(lifecycle_tree) == []
    lifecycle_oracle_call_count = sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in ALLOWED_LIFECYCLE_ORACLE_TYPES
        for node in ast.walk(lifecycle_tree)
    )
    assert lifecycle_oracle_call_count == 0
    assert _lifecycle_oracle_authority_calls(lifecycle_tree) == []

    engine_text = SEGMENT_ENGINE.read_text(encoding="utf-8")
    engine_tree = ast.parse(engine_text)
    imported_modules = {
        f'{"." * node.level}{node.module or ""}'
        for node in ast.walk(engine_tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert "..contracts.segment_rules" in imported_modules
    assert "minimal_segment_canonical_rules_v1" in engine_text
    assert "FullRebuildEngine" not in engine_text
    assert "IncrementalEngine" not in engine_text
    assert "LifecycleEvent" not in engine_text
    assert "EventLog" not in engine_text

    engine_init = (ROOT / "src/chan_parser/engine/__init__.py").read_text(
        encoding="utf-8"
    )
    assert "SegmentEngine" not in engine_init
    assert "segment" not in engine_init.lower()


def test_full_rebuild_does_not_bypass_oracle_authority_gate():
    source = FULL.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "segment_rules" not in source
    assert _segment_rules_module_object_imports(tree) == []
    assert _direct_segment_rules_imports(tree) == []
    assert _lifecycle_oracle_authority_calls(tree) == []


def test_lifecycle_oracle_gate_rejects_module_alias_attribute_call_bypass():
    bypass = ast.parse(
        "from ..contracts import segment_rules as rules\n"
        "rules.classify_primary_destruction_case(left, center, right)\n"
    )
    assert len(_segment_rules_module_object_imports(bypass)) == 1
    calls = _lifecycle_oracle_authority_calls(bypass)
    assert len(calls) == 1
    assert _call_terminal_name(calls[0]) == "classify_primary_destruction_case"


def test_lifecycle_oracle_direct_import_paths_share_exact_type_whitelist():
    allowed_names = (
        "DestructionCase, PrimaryDestructionEvidence, SegmentDirection"
    )
    absolute = ast.parse(
        "from chan_parser.contracts.segment_rules import " + allowed_names
    )
    relative = ast.parse(
        "from ..contracts.segment_rules import " + allowed_names
    )
    assert _lifecycle_oracle_import_violations(absolute) == []
    assert _lifecycle_oracle_import_violations(relative) == []


def test_lifecycle_oracle_canonical_path_gate_rejects_foreign_suffix_matches():
    allowed_names = (
        "DestructionCase, PrimaryDestructionEvidence, SegmentDirection"
    )
    sources = (
        "from foreign.segment_rules import " + allowed_names,
        "from ..foreign.segment_rules import " + allowed_names,
    )
    for source in sources:
        tree = ast.parse(source)
        suffix_only_imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.split(".")[-1] == "segment_rules"
        ]
        assert suffix_only_imports
        assert _direct_segment_rules_imports(tree) == []
        assert "SEGMENT_RULES_NONCANONICAL_MODULE_PATH" in (
            _lifecycle_oracle_import_violations(tree)
        )


def test_lifecycle_oracle_gate_rejects_extra_function_and_wildcard_imports():
    forbidden_sources = (
        "from chan_parser.contracts.segment_rules import PriceInterval",
        "from chan_parser.contracts.segment_rules import "
        "DestructionCase, PrimaryDestructionEvidence, SegmentDirection, PriceInterval",
        "from ..contracts.segment_rules import classify_primary_destruction_case",
        "from chan_parser.contracts.segment_rules import *",
    )
    for source in forbidden_sources:
        assert _lifecycle_oracle_import_violations(ast.parse(source))


def test_absolute_extra_type_was_missed_by_old_gate_and_is_now_blocked():
    bypass = ast.parse(
        "from chan_parser.contracts.segment_rules import PriceInterval"
    )
    old_exact_path_imports = [
        node
        for node in ast.walk(bypass)
        if isinstance(node, ast.ImportFrom)
        and f'{"." * node.level}{node.module or ""}'
        == "..contracts.segment_rules"
    ]
    assert old_exact_path_imports == []
    assert _segment_rules_module_object_imports(bypass) == []
    assert _lifecycle_oracle_authority_calls(bypass) == []
    assert _lifecycle_oracle_import_violations(bypass)


def test_lifecycle_oracle_gate_rejects_absolute_module_alias_attribute_call():
    bypass = ast.parse(
        "import chan_parser.contracts.segment_rules as rules\n"
        "rules.classify_secondary_confirmation(left, center, right)\n"
    )
    violations = _lifecycle_oracle_import_violations(bypass)
    assert "SEGMENT_RULES_MODULE_OBJECT_IMPORT" in violations
    assert "SEGMENT_RULES_ORACLE_AUTHORITY_CALL" in violations
