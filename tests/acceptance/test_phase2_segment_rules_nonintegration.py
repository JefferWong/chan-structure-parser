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
        if path in {ORACLE, SEGMENT_ENGINE}:
            continue
        text = path.read_text(encoding="utf-8")
        assert "segment_rules" not in text, path
        assert "minimal_segment_canonical_rules_v1" not in text, path
        tree = ast.parse(text)
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


def test_phase1_parser_sources_have_no_segment_output_or_rule_profile_hook():
    for relative in (
        "src/chan_parser/engine/full_rebuild.py",
        "src/chan_parser/engine/incremental.py",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert '"segments"' not in text
        assert "'segments'" not in text
        assert "minimal_segment_canonical_rules_v1" not in text
        assert "minimal_segment_engine_core_v1" not in text
