"""R2 contract tests for reference refresh failure during checkpoint restore."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

from chan_parser.audit.event_log import EventLog
from chan_parser.domain.raw_bar import RawBar
from chan_parser.engine.incremental import IncrementalEngine


ROOT = Path(__file__).resolve().parents[2]


def profile() -> dict:
    return yaml.safe_load(
        (ROOT / "configs/profiles/minimal_strict_v1.yaml").read_text(
            encoding="utf-8"
        )
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


def prepared_engine(*, enabled: bool = True) -> IncrementalEngine:
    engine = IncrementalEngine(profile(), segment_reference_enabled=enabled)
    engine.checkpoint_interval = 0
    return engine


def restore_fixture() -> tuple[IncrementalEngine, int, dict, dict]:
    engine = prepared_engine()
    engine.append_batch(bars(2))
    checkpoint_id = engine.create_checkpoint()
    engine.append_batch(bars(2, 2))
    engine.create_checkpoint()
    pre_resume_state = engine.get_current_state()
    pre_resume_history = deepcopy(engine._historical_snapshots)
    return engine, checkpoint_id, pre_resume_state, pre_resume_history


def event_snapshot_to_list(snapshot: tuple) -> list[dict]:
    event_log = EventLog()
    event_log.restore(snapshot)
    return event_log.to_list()


def structure_projection(items: list) -> list[dict]:
    return [item.to_dict() for item in items]


def target_expectations(engine: IncrementalEngine, checkpoint_id: int) -> dict:
    checkpoint = engine._checkpoints[checkpoint_id]
    expected_history = {
        count: snapshot
        for count, snapshot in engine._historical_snapshots.items()
        if count <= checkpoint.raw_bar_count
    }
    expected_history[checkpoint.raw_bar_count] = checkpoint.historical_snapshot
    return {
        "raw_bars": structure_projection(checkpoint.raw_bars),
        "merged_bars": structure_projection(checkpoint.merged_bars),
        "fractals": structure_projection(checkpoint.fractals),
        "strokes": structure_projection(checkpoint.strokes),
        "events": event_snapshot_to_list(checkpoint.event_snapshot),
        "history": expected_history,
        "checkpoint_ids": [key for key in engine._checkpoints if key <= checkpoint_id],
        "checkpoint_digests": {
            key: value.sha256
            for key, value in engine._checkpoints.items()
            if key <= checkpoint_id
        },
        "next_checkpoint_id": engine._next_checkpoint_id,
        "rebuild_count": checkpoint.rebuild_count,
        "last_rebuild": checkpoint.last_rebuild,
        "last_engine_inputs": checkpoint.last_engine_inputs,
        "max_engine_inputs": checkpoint.max_engine_inputs,
        "output_sha256": checkpoint.historical_snapshot["output_sha256"],
    }


def assert_target_core(engine: IncrementalEngine, expected: dict) -> None:
    assert structure_projection(engine._raw_bars) == expected["raw_bars"]
    assert structure_projection(engine._merged_bars) == expected["merged_bars"]
    assert structure_projection(engine._fractals) == expected["fractals"]
    assert structure_projection(engine._strokes) == expected["strokes"]
    assert engine._event_log.to_list() == expected["events"]
    assert engine._historical_snapshots == expected["history"]
    assert sorted(engine._checkpoints) == expected["checkpoint_ids"]
    assert {
        key: value.sha256 for key, value in engine._checkpoints.items()
    } == expected["checkpoint_digests"]
    assert engine._next_checkpoint_id == expected["next_checkpoint_id"]
    assert engine._rebuild_count == expected["rebuild_count"]
    assert engine._last_rebuild == expected["last_rebuild"]
    assert engine._last_engine_inputs == expected["last_engine_inputs"]
    assert engine._max_engine_inputs == expected["max_engine_inputs"]
    assert engine._snapshot_payload()["output_sha256"] == expected["output_sha256"]


def assert_reference_cleared(engine: IncrementalEngine) -> None:
    assert engine._segment_reference_result is None
    assert engine._segment_reference_source_strokes == ()
    assert engine.get_segment_reference_result() == {
        "reason_code": None,
        "completed": False,
        "segment": None,
        "pending_second_case": False,
        "source_stroke_ids": [],
    }


def test_reference_failure_commits_restored_target_and_clears_cache(monkeypatch):
    engine, checkpoint_id, pre_resume_state, pre_resume_history = restore_fixture()
    expected = target_expectations(engine, checkpoint_id)
    error = ValueError("restore-reference-failure")
    monkeypatch.setattr(
        engine,
        "_evaluate_segment_reference",
        lambda: (_ for _ in ()).throw(error),
    )

    with pytest.raises(ValueError) as exc:
        engine.resume_from_checkpoint(checkpoint_id)

    assert exc.value is error
    assert_target_core(engine, expected)
    assert engine.get_current_state()["structures"] != pre_resume_state["structures"]
    assert engine._historical_snapshots != pre_resume_history
    assert not any(
        event["event_type"] == "CHECKPOINT_RESTORED"
        for event in engine._event_log.to_list()
    )
    assert_reference_cleared(engine)


def test_reference_failure_does_not_restore_caller_cache_or_add_event(monkeypatch):
    engine, checkpoint_id, _, _ = restore_fixture()
    marker = object()
    engine._segment_reference_result = marker
    engine._segment_reference_source_strokes = (marker,)
    expected = target_expectations(engine, checkpoint_id)
    before_restore_event_ids = [event["event_id"] for event in expected["events"]]
    monkeypatch.setattr(
        engine,
        "_evaluate_segment_reference",
        lambda: (_ for _ in ()).throw(ValueError("restore-reference-failure")),
    )

    with pytest.raises(ValueError, match="restore-reference-failure"):
        engine.resume_from_checkpoint(checkpoint_id)

    assert_target_core(engine, expected)
    assert [event["event_id"] for event in engine._event_log.to_list()] == before_restore_event_ids
    assert_reference_cleared(engine)


def test_partial_reference_failure_clears_cache_repopulated_by_evaluator(monkeypatch):
    engine, checkpoint_id, _, _ = restore_fixture()
    error = ValueError("restore-reference-failure")

    def partially_fail() -> None:
        engine._segment_reference_result = object()
        engine._segment_reference_source_strokes = ("failed-source",)
        raise error

    monkeypatch.setattr(engine, "_evaluate_segment_reference", partially_fail)

    with pytest.raises(ValueError) as exc:
        engine.resume_from_checkpoint(checkpoint_id)

    assert exc.value is error
    assert_reference_cleared(engine)


def test_failed_restore_then_retry_equals_clean_restore():
    clean, clean_id, _, _ = restore_fixture()
    retry, retry_id, _, _ = restore_fixture()
    error = ValueError("restore-reference-failure")
    original = retry._evaluate_segment_reference
    calls = 0

    def fail_once() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error
        original()

    retry._evaluate_segment_reference = fail_once
    with pytest.raises(ValueError) as exc:
        retry.resume_from_checkpoint(retry_id)
    assert exc.value is error
    retry_state = retry.resume_from_checkpoint(retry_id)
    clean_state = clean.resume_from_checkpoint(clean_id)

    assert retry_state == clean_state
    assert retry._event_log.to_list() == clean._event_log.to_list()
    assert retry._event_log._next_sequence == clean._event_log._next_sequence
    assert retry._checkpoints.keys() == clean._checkpoints.keys()


def test_successful_restore_keeps_existing_semantics_and_records_once():
    engine, checkpoint_id, _, _ = restore_fixture()
    result = engine.resume_from_checkpoint(checkpoint_id)
    restored_events = [
        event for event in result["events"]
        if event["event_type"] == "CHECKPOINT_RESTORED"
    ]
    assert len(restored_events) == 1
    assert result["audit"]["segment_reference"]["source_stroke_ids"] == []


def test_reference_disabled_restore_does_not_evaluate(monkeypatch):
    engine = prepared_engine(enabled=False)
    engine.append_batch(bars(2))
    checkpoint_id = engine.create_checkpoint()
    engine.append_batch(bars(2, 2))
    calls = []
    monkeypatch.setattr(engine, "_evaluate_segment_reference", lambda: calls.append(True))

    engine.resume_from_checkpoint(checkpoint_id)

    assert calls == []
    assert "segment_reference" not in engine.get_current_state()["audit"]


def test_empty_reference_source_success_still_records_restore():
    engine = prepared_engine()
    engine.append_batch(bars(1))
    checkpoint_id = engine.create_checkpoint()
    result = engine.resume_from_checkpoint(checkpoint_id)

    assert result["events"][-1]["event_type"] == "CHECKPOINT_RESTORED"
    assert_reference_cleared(engine)


def test_invalid_checkpoint_fails_before_reference_refresh(monkeypatch):
    engine = prepared_engine()
    calls = []
    monkeypatch.setattr(engine, "_evaluate_segment_reference", lambda: calls.append(True))

    with pytest.raises(ValueError, match="Invalid or evicted checkpoint_id"):
        engine.resume_from_checkpoint(99)

    assert calls == []


def test_core_restore_failure_does_not_enter_r2_handler(monkeypatch):
    engine, checkpoint_id, _, _ = restore_fixture()
    calls = {"refresh": 0, "restore": 0}
    original_restore = engine._event_log.restore

    def fail_restore(snapshot):
        calls["restore"] += 1
        raise RuntimeError("core-restore-failure")

    monkeypatch.setattr(engine._event_log, "restore", fail_restore)
    monkeypatch.setattr(
        engine,
        "_evaluate_segment_reference",
        lambda: calls.__setitem__("refresh", calls["refresh"] + 1),
    )

    with pytest.raises(RuntimeError, match="core-restore-failure"):
        engine.resume_from_checkpoint(checkpoint_id)

    assert calls == {"refresh": 0, "restore": 1}
    assert original_restore is not None


@pytest.mark.parametrize("downstream", ["record", "state"])
def test_post_reference_failures_do_not_enter_r2_handler(monkeypatch, downstream):
    engine, checkpoint_id, _, _ = restore_fixture()
    marker = object()
    engine._evaluate_segment_reference = lambda: setattr(
        engine, "_segment_reference_result", marker
    )
    calls = [0]
    monkeypatch.setattr(
        engine,
        "_segment_reference_result",
        marker,
        raising=False,
    )
    original_restore = engine._event_log.restore
    monkeypatch.setattr(
        engine,
        "_restore_reference_append_rollback_state",
        lambda *args: calls.__setitem__(0, calls[0] + 1),
        raising=False,
    )
    if downstream == "record":
        original_record = engine._event_log.record

        def fail_record(event):
            if event.event_type == "CHECKPOINT_RESTORED":
                raise RuntimeError("restore-event-failure")
            return original_record(event)

        monkeypatch.setattr(engine._event_log, "record", fail_record)
        expected_message = "restore-event-failure"
    else:
        monkeypatch.setattr(
            engine,
            "get_current_state",
            lambda: (_ for _ in ()).throw(RuntimeError("restore-state-failure")),
        )
        expected_message = "restore-state-failure"

    with pytest.raises(RuntimeError, match=expected_message):
        engine.resume_from_checkpoint(checkpoint_id)

    assert calls == [0]
    assert engine._segment_reference_result is marker
    assert original_restore is not None


def test_baseexception_partial_reference_cache_is_cleared_and_reraised():
    engine, checkpoint_id, _, _ = restore_fixture()
    marker = KeyboardInterrupt("base-exception")

    def partially_fail() -> None:
        engine._segment_reference_result = object()
        engine._segment_reference_source_strokes = ("failed-source",)
        raise marker

    engine._evaluate_segment_reference = partially_fail

    with pytest.raises(KeyboardInterrupt) as exc:
        engine.resume_from_checkpoint(checkpoint_id)

    assert exc.value is marker
    assert_reference_cleared(engine)
