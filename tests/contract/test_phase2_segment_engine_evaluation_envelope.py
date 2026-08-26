"""Contract tests for the producer-side SegmentEngine evaluation envelope."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from collections.abc import Iterator, Sequence
import inspect

import pytest

from chan_parser.adapters.segment_engine_evaluation import (
    SegmentEngineEvaluationEnvelope,
    evaluate_segment_engine_with_source_context,
)
import chan_parser.adapters.segment_engine_evaluation as evaluation_module
from chan_parser.contracts.segment_incremental_source_continuity import (
    SegmentIncrementalSourceContinuityError,
    SegmentIncrementalSourceStrokeBinding,
    bind_incremental_segment_source_strokes,
)
from chan_parser.domain.lifecycle import StructureStatus, StrokeDirection
from chan_parser.domain.stroke import Stroke
from chan_parser.engine.segment import SegmentEngine, SegmentEngineCoreError, SegmentEngineResult


def make_strokes(points: list[float]) -> list[Stroke]:
    result: list[Stroke] = []
    for index, (start, end) in enumerate(zip(points, points[1:])):
        result.append(
            Stroke(
                object_id=f"stroke_{index:06d}_r1",
                logical_id=f"stroke:{index}",
                revision=1,
                status=StructureStatus.CONFIRMED,
                created_at_bar=index + 1,
                confirmed_at_bar=index + 1,
                rule_profile="minimal_strict_v1",
                rule_version="1.0.0",
                stroke_id=f"stroke_{index:06d}",
                direction=(
                    StrokeDirection.UP if start < end else StrokeDirection.DOWN
                ),
                start_fractal_id=f"fx:{index}",
                end_fractal_id=f"fx:{index + 1}",
                start_price=start,
                end_price=end,
                start_bar_index=index,
                end_bar_index=index + 1,
                merged_bar_count=2,
                max_price=max(start, end),
                min_price=min(start, end),
                price_range=abs(end - start),
                confirmation_requirements=[],
                repaint_risk="NONE",
                created_at_raw_bar_index=None,
                confirmed_at_raw_bar_index=None,
            )
        )
    return result


def canonical_source() -> list[Stroke]:
    return make_strokes([0, 10, 4, 12, 6, 11, 5])


def extended_source() -> list[Stroke]:
    return make_strokes([0, 10, 4, 12, 6, 11, 5, 13])


def test_public_api_and_profile_authority() -> None:
    signature = inspect.signature(evaluate_segment_engine_with_source_context)
    assert tuple(signature.parameters) == ("strokes", "sequence_id")
    assert signature.parameters["sequence_id"].kind is inspect.Parameter.KEYWORD_ONLY

    envelope = evaluate_segment_engine_with_source_context(
        canonical_source(), sequence_id="evaluation:test"
    )
    assert envelope.engine_profile_id == SegmentEngine.PROFILE_ID
    assert envelope.engine_profile_version == SegmentEngine.PROFILE_VERSION
    assert envelope.canonical_rules_profile_id == SegmentEngine.CANONICAL_PROFILE_ID
    assert envelope.canonical_rules_profile_version == SegmentEngine.CANONICAL_PROFILE_VERSION
    assert envelope.canonical_rules_baseline_commit == SegmentEngine.CANONICAL_BASELINE
    assert type(envelope.current_source_binding) is tuple


@pytest.mark.parametrize(
    "source",
    [iter(canonical_source()), "strokes", b"strokes", bytearray(b"strokes"), 1],
)
def test_non_sequence_shape_is_rejected_before_normalization(source: object) -> None:
    with pytest.raises(SegmentIncrementalSourceContinuityError) as error:
        evaluate_segment_engine_with_source_context(source, sequence_id="test")  # type: ignore[arg-type]
    assert error.value.reason_code == "SEGMENT_SOURCE_BINDING_REQUIRED"


def test_custom_sequence_is_read_once_and_shared_by_binder_and_engine(monkeypatch) -> None:
    source = tuple(canonical_source())

    class OneReadSequence(Sequence[Stroke]):
        def __init__(self) -> None:
            self.reads = 0

        def __len__(self) -> int:
            return len(source)

        def __getitem__(self, index):
            raise AssertionError("adapter must snapshot through one iteration")

        def __iter__(self) -> Iterator[Stroke]:
            self.reads += 1
            if self.reads > 1:
                raise AssertionError("caller sequence was re-read")
            yield from source

    caller = OneReadSequence()
    events: list[tuple[str, tuple[Stroke, ...]]] = []
    real_binder = evaluation_module.bind_incremental_segment_source_strokes
    real_process = SegmentEngine.process_primary

    def binder(values):
        events.append(("bind", values))
        return real_binder(values)

    def process(self, values, *, sequence_id):
        events.append(("engine", values))
        return real_process(self, values, sequence_id=sequence_id)

    monkeypatch.setattr(evaluation_module, "bind_incremental_segment_source_strokes", binder)
    monkeypatch.setattr(SegmentEngine, "process_primary", process)
    evaluate_segment_engine_with_source_context(caller, sequence_id="evaluation:test")

    assert caller.reads == 1
    assert [name for name, _ in events] == ["bind", "engine"]
    assert events[0][1] is events[1][1]
    assert type(events[0][1]) is tuple


def test_binder_and_engine_errors_are_propagated_without_wrapping(monkeypatch) -> None:
    binder_error = SegmentIncrementalSourceContinuityError("binder-error")

    def failing_binder(_values):
        raise binder_error

    monkeypatch.setattr(evaluation_module, "bind_incremental_segment_source_strokes", failing_binder)
    with pytest.raises(SegmentIncrementalSourceContinuityError) as raised:
        evaluate_segment_engine_with_source_context(canonical_source(), sequence_id="test")
    assert raised.value is binder_error

    monkeypatch.undo()
    engine_error = SegmentEngineCoreError("engine-error")

    def failing_process(self, _values, *, sequence_id):
        raise engine_error

    monkeypatch.setattr(SegmentEngine, "process_primary", failing_process)
    with pytest.raises(SegmentEngineCoreError) as raised:
        evaluate_segment_engine_with_source_context(canonical_source(), sequence_id="test")
    assert raised.value is engine_error


@pytest.mark.parametrize(
    ("points", "reason"),
    [
        ([0, 10, 4, 12, 6], "SEGMENT_FEATURE_WINDOW_INCOMPLETE"),
        ([0, 10, 4, 11, 5, 12, 6], "SEGMENT_PRIMARY_FRACTAL_NOT_FOUND"),
        ([0, 3, 1, 8, 5, 7, 4], "SEGMENT_SECOND_CASE_PENDING"),
        ([0, 10, 4, 12, 6, 11, 5], "SEGMENT_FIRST_CASE_CONFIRMED"),
    ],
)
def test_all_engine_outcomes_are_bound(points: list[float], reason: str) -> None:
    envelope = evaluate_segment_engine_with_source_context(
        make_strokes(points), sequence_id="evaluation:outcome"
    )
    assert envelope.result.reason_code == reason
    assert envelope.current_source_binding


def test_binder_is_not_segment_engine_semantics_authority() -> None:
    nonalternating = canonical_source()
    nonalternating[1] = replace(nonalternating[1], direction=StrokeDirection.UP)
    noncontiguous = canonical_source()
    noncontiguous[1] = replace(noncontiguous[1], start_fractal_id="other")
    # These sources remain valid source evidence, while the engine rejects them.
    assert bind_incremental_segment_source_strokes(nonalternating)
    assert bind_incremental_segment_source_strokes(noncontiguous)


def test_different_suffixes_change_only_context_when_result_is_same() -> None:
    first = evaluate_segment_engine_with_source_context(
        extended_source(), sequence_id="evaluation:suffix"
    )
    changed = extended_source()
    changed[-1] = replace(changed[-1], object_id="suffix-object-b")
    second = evaluate_segment_engine_with_source_context(
        changed, sequence_id="evaluation:suffix"
    )
    assert first.result == second.result
    assert first.current_source_binding != second.current_source_binding
    assert first != second


def test_suffix_revision_and_content_change_context() -> None:
    original = extended_source()
    revision_changed = extended_source()
    revision_changed[-1] = replace(revision_changed[-1], revision=2)
    content_changed = extended_source()
    content_changed[-1] = replace(
        content_changed[-1], end_price=14, max_price=14, price_range=1
    )
    base = evaluate_segment_engine_with_source_context(original, sequence_id="test")
    revised = evaluate_segment_engine_with_source_context(revision_changed, sequence_id="test")
    content = evaluate_segment_engine_with_source_context(content_changed, sequence_id="test")
    assert base.result == revised.result == content.result
    assert base != revised
    assert base != content


def test_determinism_and_source_binding_detachment() -> None:
    source = extended_source()
    envelope = evaluate_segment_engine_with_source_context(source, sequence_id="test")
    copied = evaluate_segment_engine_with_source_context(deepcopy(source), sequence_id="test")
    assert envelope == copied
    assert envelope == evaluate_segment_engine_with_source_context(source, sequence_id="test")
    binding = envelope.current_source_binding
    source[-1] = replace(source[-1], object_id="later", revision=7, stroke_id="later-id")
    assert envelope.current_source_binding == binding
    assert not any(value is source or value is tuple(source) for value in envelope.__dict__.values())


def test_sequence_difference_changes_envelope_and_invalid_sequence_is_engine_error() -> None:
    first = evaluate_segment_engine_with_source_context(canonical_source(), sequence_id="a")
    second = evaluate_segment_engine_with_source_context(canonical_source(), sequence_id="b")
    assert first.sequence_id != second.sequence_id
    assert first != second
    with pytest.raises(SegmentEngineCoreError):
        evaluate_segment_engine_with_source_context(canonical_source(), sequence_id=True)  # type: ignore[arg-type]


def test_direct_envelope_construction_fails_closed_and_is_frozen() -> None:
    valid = evaluate_segment_engine_with_source_context(canonical_source(), sequence_id="test")
    with pytest.raises(ValueError):
        replace(valid, current_source_binding=list(valid.current_source_binding))
    with pytest.raises(ValueError):
        replace(valid, sequence_id="")
    with pytest.raises(ValueError):
        replace(valid, engine_profile_id="alternate")
    with pytest.raises(ValueError):
        replace(valid, result=object())
    with pytest.raises(FrozenInstanceError):
        valid.sequence_id = "changed"  # type: ignore[misc]


def test_public_binding_is_the_pr17_binding_authority() -> None:
    source = canonical_source()
    direct = bind_incremental_segment_source_strokes(source)
    envelope = evaluate_segment_engine_with_source_context(source, sequence_id="test")
    assert envelope.current_source_binding == direct
    assert type(direct) is tuple
    assert all(type(binding) is SegmentIncrementalSourceStrokeBinding for binding in direct)


def test_adapter_adds_no_content_hash_calls(monkeypatch) -> None:
    source = canonical_source()
    counts: dict[int, int] = {}
    original = Stroke.content_hash

    def counted(stroke: Stroke) -> str:
        counts[id(stroke)] = counts.get(id(stroke), 0) + 1
        return original(stroke)

    monkeypatch.setattr(Stroke, "content_hash", counted)
    evaluate_segment_engine_with_source_context(source, sequence_id="test")
    assert set(counts) == {id(stroke) for stroke in source}
    assert set(counts.values()) == {2}
