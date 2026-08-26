"""Contract tests for the bounded Incremental reference-evaluation rollback."""
from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timedelta

import pytest
import yaml

from chan_parser.domain.raw_bar import RawBar
from chan_parser.engine.incremental import (
    IncrementalEngine,
    _IncrementalReferenceAppendRollbackState,
)


def profile() -> dict:
    return yaml.safe_load(
        """\
inclusion: {}
fractal: {}
stroke: {}
runtime:
  max_rebuild_distance: 200
  checkpoint_interval: 0
  snapshot_retention: 20
  checkpoint_retention: 10
profile_id: minimal_strict_v1
"""
    )


def bars(count: int, start_index: int = 0) -> list[RawBar]:
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
        for index in range(start_index, start_index + count)
    ]


def engine(*, checkpoint_interval: int = 0) -> IncrementalEngine:
    result = IncrementalEngine(profile(), segment_reference_enabled=True)
    result.checkpoint_interval = checkpoint_interval
    return result


def fail_with(error: Exception):
    def evaluator(*args, **kwargs) -> None:
        raise error

    return evaluator


def test_capsule_is_private_and_captures_only_allowlisted_state():
    names = {field.name for field in fields(_IncrementalReferenceAppendRollbackState)}
    assert _IncrementalReferenceAppendRollbackState.__name__.startswith("_")
    assert names == {
        "raw_bars",
        "merged_bars",
        "fractals",
        "strokes",
        "event_snapshot",
        "rebuild_count",
        "last_rebuild",
        "last_engine_inputs",
        "max_engine_inputs",
    }


def test_bootstrap_reference_failure_restores_core_eventlog_and_caches(monkeypatch):
    current = engine()
    raw_before = current._raw_bars
    merged_before = current._merged_bars
    fractals_before = current._fractals
    strokes_before = current._strokes
    event_before = current._event_log.compute_sha256()
    state_before = current.get_current_state()
    error = ValueError("forced reference failure")
    monkeypatch.setattr(current, "_evaluate_segment_reference", fail_with(error))

    with pytest.raises(ValueError) as exc:
        current.append_batch(bars(3))

    assert exc.value is error
    assert current._raw_bars is raw_before
    assert current._merged_bars is merged_before
    assert current._fractals is fractals_before
    assert current._strokes is strokes_before
    assert current._event_log.compute_sha256() == event_before
    assert current.get_current_state() == state_before
    assert current.get_segment_reference_result() == {
        "reason_code": None,
        "completed": False,
        "segment": None,
        "pending_second_case": False,
        "source_stroke_ids": [],
    }


def test_bounded_reference_failure_restores_identity_bookkeeping_and_events(monkeypatch):
    current = engine()
    current.append_batch(bars(3))
    raw_before = current._raw_bars
    merged_before = current._merged_bars
    fractals_before = current._fractals
    strokes_before = current._strokes
    rebuild_before = current._rebuild_count
    last_rebuild_before = current._last_rebuild.copy()
    last_inputs_before = current._last_engine_inputs.copy()
    max_inputs_before = current._max_engine_inputs.copy()
    event_before = current._event_log.compute_sha256()
    error = RuntimeError("bounded reference failure")
    monkeypatch.setattr(current, "_evaluate_segment_reference", fail_with(error))

    with pytest.raises(RuntimeError, match="bounded reference failure"):
        current.append_batch(bars(1, 3))

    assert current._raw_bars is raw_before
    assert current._merged_bars is merged_before
    assert current._fractals is fractals_before
    assert current._strokes is strokes_before
    assert current._rebuild_count == rebuild_before
    assert current._last_rebuild == last_rebuild_before
    assert current._last_engine_inputs == last_inputs_before
    assert current._max_engine_inputs == max_inputs_before
    assert current._event_log.compute_sha256() == event_before


def test_failure_clears_previous_reference_cache_and_preserves_exception_identity(monkeypatch):
    current = engine()
    current._segment_reference_result = object()
    current._segment_reference_source_strokes = (object(),)
    error = ValueError("exact evaluator error")
    monkeypatch.setattr(current, "_evaluate_segment_reference", fail_with(error))

    with pytest.raises(ValueError) as exc:
        current.append_batch(bars(1))

    assert exc.value is error
    assert current._segment_reference_result is None
    assert current._segment_reference_source_strokes == ()
    assert current.get_segment_reference_result()["source_stroke_ids"] == []


def test_retry_is_equivalent_to_clean_append(monkeypatch):
    control = engine()
    failing = engine()
    control_state = control.append_batch(bars(4))
    error = RuntimeError("one-shot reference failure")
    original = failing._evaluate_segment_reference
    calls = 0

    def fail_once() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error
        original()

    monkeypatch.setattr(failing, "_evaluate_segment_reference", fail_once)
    with pytest.raises(RuntimeError) as exc:
        failing.append_batch(bars(4))
    assert exc.value is error
    retry_state = failing.append_batch(bars(4))

    assert retry_state == control_state
    assert failing._event_log.compute_sha256() == control._event_log.compute_sha256()
    assert failing._rebuild_count == control._rebuild_count
    assert failing._last_rebuild == control._last_rebuild
    assert failing._last_engine_inputs == control._last_engine_inputs
    assert failing._max_engine_inputs == control._max_engine_inputs
    assert failing._historical_snapshots == control._historical_snapshots
    assert failing._checkpoints == control._checkpoints


def test_disabled_reference_does_not_capture(monkeypatch):
    current = IncrementalEngine(profile(), segment_reference_enabled=False)
    monkeypatch.setattr(
        current,
        "_capture_reference_append_rollback_state",
        lambda: (_ for _ in ()).throw(AssertionError("capture must be bypassed")),
    )
    current.append_batch(bars(1))


def test_validation_failure_happens_before_capture(monkeypatch):
    current = engine()
    current.append_batch(bars(1))
    calls = 0

    def capture():
        nonlocal calls
        calls += 1
        raise AssertionError("capture must follow validation")

    monkeypatch.setattr(current, "_capture_reference_append_rollback_state", capture)
    with pytest.raises(ValueError, match="strictly increasing"):
        current.append_batch(bars(1, 0))
    assert calls == 0


def test_core_failure_does_not_invoke_restore(monkeypatch):
    current = engine()
    monkeypatch.setattr(
        current,
        "_bootstrap",
        lambda combined: (_ for _ in ()).throw(RuntimeError("bootstrap failed")),
    )
    monkeypatch.setattr(
        current,
        "_restore_reference_append_rollback_state",
        lambda state: (_ for _ in ()).throw(AssertionError("restore must be reference-only")),
    )
    with pytest.raises(RuntimeError, match="bootstrap failed"):
        current.append_batch(bars(1))


def test_rollback_failure_is_fail_stop_but_cache_finally_clears(monkeypatch):
    current = engine()
    evaluator_error = RuntimeError("evaluator failed")
    rollback_error = RuntimeError("rollback failed")
    monkeypatch.setattr(current, "_evaluate_segment_reference", fail_with(evaluator_error))
    monkeypatch.setattr(
        current,
        "_restore_reference_append_rollback_state",
        fail_with(rollback_error),
    )

    with pytest.raises(RuntimeError, match="rollback failed"):
        current.append_batch(bars(1))

    assert current._segment_reference_result is None
    assert current._segment_reference_source_strokes == ()


def test_post_reference_failures_do_not_invoke_rollback(monkeypatch):
    for method_name in ("_store_historical_snapshot", "create_checkpoint"):
        current = engine(checkpoint_interval=1 if method_name == "create_checkpoint" else 0)
        monkeypatch.setattr(
            current,
            "_restore_reference_append_rollback_state",
            lambda state: (_ for _ in ()).throw(AssertionError("rollback scope expanded")),
        )
        monkeypatch.setattr(
            current,
            method_name,
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("post-reference failed")),
        )
        with pytest.raises(RuntimeError, match="post-reference failed"):
            current.append_batch(bars(1))


def test_checkpoint_due_reference_failure_does_not_create_checkpoint(monkeypatch):
    current = engine(checkpoint_interval=1)
    current.append_batch(bars(1))
    checkpoint_ids_before = dict(current._checkpoints)
    next_id_before = current._next_checkpoint_id
    events_before = current.get_current_state()["events"]
    error = RuntimeError("checkpoint-due reference failure")
    monkeypatch.setattr(current, "_evaluate_segment_reference", fail_with(error))

    with pytest.raises(RuntimeError, match="checkpoint-due reference failure"):
        current.append_batch(bars(1, 1))

    assert current._checkpoints == checkpoint_ids_before
    assert current._next_checkpoint_id == next_id_before
    assert current.get_current_state()["events"] == events_before
