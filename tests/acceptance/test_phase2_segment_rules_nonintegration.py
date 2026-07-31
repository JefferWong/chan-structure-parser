"""Canonical-rule artifacts must remain disconnected from both parser paths."""
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
ORACLE = ROOT / "src/chan_parser/contracts/segment_rules.py"


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


def test_reference_oracle_has_no_engine_parser_segment_or_event_dependencies():
    tree = ast.parse(ORACLE.read_text(encoding="utf-8"))
    imported_modules = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
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
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    assert "Segment" not in class_names
    assert not any(name.endswith("Engine") for name in class_names)
    assert not {"append", "append_one", "append_batch", "process"} & function_names


def test_reference_oracle_exposes_no_production_package_export():
    contracts_init = (ROOT / "src/chan_parser/contracts/__init__.py").read_text(
        encoding="utf-8"
    )
    assert "segment_rules" not in contracts_init


def test_no_existing_source_module_imports_or_calls_reference_oracle():
    source_root = ROOT / "src/chan_parser"
    for path in source_root.rglob("*.py"):
        if path == ORACLE:
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


def test_phase1_parser_sources_have_no_segment_output_or_rule_profile_hook():
    for relative in (
        "src/chan_parser/engine/full_rebuild.py",
        "src/chan_parser/engine/incremental.py",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert '"segments"' not in text
        assert "'segments'" not in text
        assert "minimal_segment_canonical_rules_v1" not in text
