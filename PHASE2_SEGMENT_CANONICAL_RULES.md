# Phase 2 segment canonical rules

## Scope and version anchors

- Baseline commit: `f7eecdd657530f928ffbf869832e76f1dd17b92e`
- Frozen Phase 1 profile: `minimal_strict_v1`
- Frozen old contract profile: `minimal_segment_contract_v1@0.2.0`
- New rules-only profile: `minimal_segment_canonical_rules_v1@1.0.0`

This document separates `ORIGINAL_CANONICAL_CORE` from
`ENGINEERING_DETERMINISM_V1`. The second class makes underspecified boundaries
deterministic for a future implementation; it is not represented as original
theory. The accompanying reference oracle is stateless and non-production.

## ORIGINAL_CANONICAL_CORE

### FS-001

- rule_id: `FS-001`
- classification: `ORIGINAL_CANONICAL_CORE`
- statement: An UP candidate uses only DOWN strokes; a DOWN candidate uses only
  UP strokes. Order is by bar/time, identity is `logical_id`, and all provenance
  is retained.
- input: Candidate direction and ordered, uniquely identified strokes from one
  candidate sequence.
- output: Ordered opposite-direction feature elements with source logical IDs.
- fail_closed_condition: Duplicate/missing logical identity, invalid bar order,
  or cross-sequence input.
- fixture_ids: `FS-UP-001`, `FS-DOWN-001`

### FS-002

- rule_id: `FS-002`
- classification: `ORIGINAL_CANONICAL_CORE`
- statement: Inclusion is defined only between elements assumed to belong to the
  same feature sequence; confirmed boundaries separate sequences.
- input: Sequence identity and candidate direction for both elements.
- output: Permission to evaluate inclusion within one sequence.
- fail_closed_condition: Different sequence, candidate direction, or confirmed
  boundary side.
- fixture_ids: `FS-CROSS-SEQUENCE-REJECT-001`

### FS-003

- rule_id: `FS-003`
- classification: `ORIGINAL_CANONICAL_CORE`
- statement: Inclusion normalization converts a raw feature sequence into a
  standard feature sequence without losing ordered stroke provenance.
- input: One raw feature sequence and its inclusion decisions.
- output: Standard feature elements with complete source logical IDs.
- fail_closed_condition: Lost, duplicated, or reordered provenance.
- fixture_ids: `INCLUSION-PROVENANCE-001`

### FR-001

- rule_id: `FR-001`
- classification: `ORIGINAL_CANONICAL_CORE`
- statement: UP candidates consider only TOP standard-feature fractals; DOWN
  candidates consider only BOTTOM standard-feature fractals.
- input: Candidate direction and a standard-feature fractal.
- output: Direction-compatible fractal evidence or no confirmation.
- fail_closed_condition: Wrong fractal type or non-standard input.
- fixture_ids: `FRACTAL-TOP-001`, `FRACTAL-BOTTOM-001`,
  `FRACTAL-WRONG-DIRECTION-REJECT-001`

### DS-CASE1

- rule_id: `DS-CASE1`
- classification: `ORIGINAL_CANONICAL_CORE`
- statement: A complete direction-compatible three-element fractal with no gap
  between its first and center elements is first-case destruction. The endpoint
  is the TOP center high for UP or BOTTOM center low for DOWN.
- input: Three adjacent standard elements, candidate direction, and gap result.
- output: `FIRST_CASE` with endpoint.
- fail_closed_condition: Incomplete three-element window, wrong fractal, gap, or
  pen break alone.
- fixture_ids: `CASE1-UP-001`, `CASE1-DOWN-001`,
  `CASE1-PEN-ONLY-PENDING-001`

### DS-CASE2

- rule_id: `DS-CASE2`
- classification: `ORIGINAL_CANONICAL_CORE`
- statement: A complete direction-compatible primary fractal whose first and
  center elements have a gap enters `SECOND_CASE_PENDING`. Confirmation requires
  the opposite fractal in the normally normalized second feature sequence. That
  fractal has no nested case classification and need not close the original gap.
- input: Primary fractal/gap evidence and later second-sequence fractal evidence.
- output: `SECOND_CASE_PENDING` or `SECOND_CASE_CONFIRMED`.
- fail_closed_condition: Missing primary fractal, missing required second
  fractal, or unnormalized second sequence.
- fixture_ids: `CASE2-UP-PENDING-001`, `CASE2-DOWN-PENDING-001`,
  `CASE2-SECOND-FRACTAL-CONFIRM-001`,
  `CASE2-GAP-NOT-CLOSED-CONFIRM-001`,
  `CASE2-SECOND-SEQUENCE-INCLUSION-001`

### DS-PEN-001

- rule_id: `DS-PEN-001`
- classification: `ORIGINAL_CANONICAL_CORE`
- statement: A pen break is provisional evidence and cannot by itself confirm
  segment destruction.
- input: A single reverse pen-break observation.
- output: Provisional evidence only.
- fail_closed_condition: Any attempt to return
  `SEGMENT_DESTRUCTION_CONFIRMED` without a complete first or second case.
- fixture_ids: `CASE1-PEN-ONLY-PENDING-001`

### DS-CASE2-FAIL

- rule_id: `DS-CASE2-FAIL`
- classification: `ORIGINAL_CANONICAL_CORE`
- statement: Before the second-sequence fractal confirms, a strict new extreme
  in the original direction invalidates the pending endpoint and the original
  segment continues.
- input: Pending second case, pending endpoint, original direction, and latest
  strict-extreme evidence.
- output: `PENDING_DESTRUCTION_INVALIDATED`.
- fail_closed_condition: Equality treated as a strict extreme, or confirmation
  of the hypothetical reverse segment after invalidation.
- fixture_ids: `CASE2-NEW-EXTREME-INVALIDATE-001`

### DS-CASE1-FAIL

- rule_id: `DS-CASE1-FAIL`
- classification: `ORIGINAL_CANONICAL_CORE`
- statement: If a pen break never forms the required standard-feature fractal
  and a later original-direction move negates it, invalidate that pen evidence;
  the original segment continues.
- input: Provisional pen-break evidence, absent required fractal, and negating
  continuation evidence.
- output: Invalidated pen evidence and no confirmed reverse segment.
- fail_closed_condition: Confirming a reverse segment from the failed pen break.
- fixture_ids: `CASE1-FAILED-CONTINUATION-001`

### SG-001

- rule_id: `SG-001`
- classification: `ORIGINAL_CANONICAL_CORE`
- statement: A segment has direction, starts and ends with strokes of that
  direction, adjacent confirmed segments alternate and share the boundary
  endpoint, and a same-direction candidate cannot destroy it.
- input: Candidate direction, endpoint strokes, and adjacent confirmed boundary.
- output: Direction/connection eligibility.
- fail_closed_condition: Direction mismatch, disconnected boundary, or
  same-direction destruction claim.
- fixture_ids: `SG-CONNECTION-001`

## ENGINEERING_DETERMINISM_V1

### EQ-INTERVAL-001

- rule_id: `EQ-INTERVAL-001`
- classification: `ENGINEERING_DETERMINISM_V1`
- statement: Feature price ranges are closed intervals `[low, high]`.
- input: Numeric low and high.
- output: Immutable closed interval.
- fail_closed_condition: `low > high`.
- fixture_ids: `GAP-TOUCHING-NOGAP-001`, `INCLUSION-EQUAL-001`

### EQ-INCLUSION-001

- rule_id: `EQ-INCLUSION-001`
- classification: `ENGINEERING_DETERMINISM_V1`
- statement: Containment uses inclusive boundaries in either direction;
  identical intervals are included.
- input: Two closed intervals from one feature sequence.
- output: `CONTAINS`, `CONTAINED_BY`, or `EQUAL` where applicable.
- fail_closed_condition: Cross-sequence inputs or treating equality as
  non-inclusion.
- fixture_ids: `INCLUSION-UP-001`, `INCLUSION-DOWN-001`,
  `INCLUSION-EQUAL-001`

### EQ-GAP-001

- rule_id: `EQ-GAP-001`
- classification: `ENGINEERING_DETERMINISM_V1`
- statement: A gap exists only when two closed intervals are strictly separated;
  touching at one point is not a gap.
- input: Two closed intervals.
- output: Boolean gap classification.
- fail_closed_condition: Treating touching or overlap as a gap.
- fixture_ids: `GAP-STRICT-UP-001`, `GAP-STRICT-DOWN-001`,
  `GAP-TOUCHING-NOGAP-001`, `GAP-OVERLAP-NOGAP-001`

### EQ-SEED-001

- rule_id: `EQ-SEED-001`
- classification: `ENGINEERING_DETERMINISM_V1`
- statement: The latest strict non-inclusion pair seeds UP only when both high
  and low increase, and DOWN only when both decrease. Equality, containment,
  one-boundary movement, or absence of such a pair remains unseeded and deferred.
- input: Previous/current standard intervals from one sequence.
- output: `UP`, `DOWN`, or `UNSEEDED`.
- fail_closed_condition: Guessing from candidate direction or index order.
- fixture_ids: `INCLUSION-UP-001`, `INCLUSION-DOWN-001`,
  `INCLUSION-UNSEEDED-001`

### EQ-MERGE-001

- rule_id: `EQ-MERGE-001`
- classification: `ENGINEERING_DETERMINISM_V1`
- statement: UP merge uses max high/max low; DOWN merge uses min high/min low.
  Provenance order is stable and logical identity is content-derived.
- input: Included intervals, valid seed, and ordered provenance.
- output: Deterministic merged interval and provenance.
- fail_closed_condition: Unseeded direction, non-included input, random/runtime
  identity, or provenance loss.
- fixture_ids: `INCLUSION-UP-001`, `INCLUSION-DOWN-001`,
  `INCLUSION-PROVENANCE-001`

### EQ-BOUNDARY-001

- rule_id: `EQ-BOUNDARY-001`
- classification: `ENGINEERING_DETERMINISM_V1`
- statement: The last pre-turn feature and first post-turn feature in first-case
  evaluation are different-nature boundary elements and never merge. Later
  same-side elements may merge. A second feature sequence uses normal inclusion.
- input: Included elements plus boundary-side and sequence-kind metadata.
- output: Merge permission or
  `HYPOTHETICAL_BOUNDARY_DIFFERENT_NATURE`.
- fail_closed_condition: Cross-boundary first-case merge or suppressed
  second-sequence normalization.
- fixture_ids: `INCLUSION-FIRST-BOUNDARY-NOMERGE-001`,
  `INCLUSION-SECOND-SEQUENCE-MERGE-001`,
  `CASE2-SECOND-SEQUENCE-INCLUSION-001`

### EQ-FRACTAL-001

- rule_id: `EQ-FRACTAL-001`
- classification: `ENGINEERING_DETERMINISM_V1`
- statement: TOP requires center high and low both strictly above both
  neighbors; BOTTOM requires both strictly below. Any critical equality remains
  provisional as `EQUAL_EXTREMA_UNRESOLVED`.
- input: Three adjacent standard intervals.
- output: `TOP`, `BOTTOM`, or `NONE`.
- fail_closed_condition: Equality tie-break or non-three-element evidence.
- fixture_ids: `FRACTAL-TOP-001`, `FRACTAL-BOTTOM-001`,
  `FRACTAL-EQUAL-HIGH-REJECT-001`, `FRACTAL-EQUAL-LOW-REJECT-001`

### EQ-TIME-001

- rule_id: `EQ-TIME-001`
- classification: `ENGINEERING_DETERMINISM_V1`
- statement: Confirmation time is the latest bar at which all source strokes of
  the required right standard element are first visible in the required status.
  Endpoint time is never used to backfill confirmation.
- input: Endpoint bar and all right-element source visibility bars.
- output: Maximum visibility bar.
- fail_closed_condition: Missing visibility evidence or result before endpoint.
- fixture_ids: `FREEZE-APPEND-001`

### EQ-WINNER-001

- rule_id: `EQ-WINNER-001`
- classification: `ENGINEERING_DETERMINISM_V1`
- statement: Simultaneously confirmable candidates are ordered by endpoint bar,
  then start bar, then logical ID. The winner is confirmed, parsing restarts at
  its endpoint, and mutually exclusive losers are invalidated.
- input: Non-empty unique candidate logical IDs with endpoint/start bars.
- output: Winner logical ID and deterministically ordered invalidated IDs.
- fail_closed_condition: Duplicate/missing ID or reliance on creation order.
- fixture_ids: `WINNER-LEFTMOST-001`, `WINNER-SAME-ENDPOINT-001`

### EQ-LIFECYCLE-001

- rule_id: `EQ-LIFECYCLE-001`
- classification: `ENGINEERING_DETERMINISM_V1`
- statement: Minimum windows are `CANDIDATE`; incomplete evidence is
  `PROVISIONAL`; complete case evidence is `CONFIRMED`; never-confirmed failures
  are `INVALIDATED`; a confirmed active segment terminated by a newly confirmed
  reverse segment becomes `REPLACED`, with `replaced_by` set to the reverse
  segment logical ID. No `DESTROYED` state is introduced.
- input: Prior confirmation state, evidence state, and optional reverse logical ID.
- output: Existing lifecycle state and optional `replaced_by`.
- fail_closed_condition: Invalidating a confirmed segment, replacing an
  unconfirmed candidate, or missing replacement logical ID.
- fixture_ids: `LIFECYCLE-INVALIDATED-001`, `LIFECYCLE-REPLACED-001`

### EQ-FREEZE-001

- rule_id: `EQ-FREEZE-001`
- classification: `ENGINEERING_DETERMINISM_V1`
- statement: Once complete evidence confirms a candidate, canonical segmentation
  before its endpoint freezes. Corrections require append-only lifecycle events;
  old events cannot be deleted and confirmation cannot be backfilled.
- input: Confirmed endpoint, lifecycle history, and later evidence.
- output: Frozen-prefix constraint for a future implementation.
- fail_closed_condition: Silent prefix rewrite, event deletion, or earlier
  confirmation-time rewrite.
- fixture_ids: `TIMING-NO-BACKFILL-001`

## Decision flow

The reference oracle first normalizes immutable closed intervals, applies
same-sequence inclusion only after a strict seed exists, and classifies only a
strict three-element feature fractal. A direction-compatible primary fractal
without a first-to-center gap yields first-case evidence. With a gap it remains
second-case pending until the required reverse-candidate feature fractal appears.
A strict new original-direction extreme invalidates that pending boundary.

Pen break and canonical segment destruction remain distinct. Canonical
destruction is evidence classification; `REPLACED` is a later engineering
lifecycle mapping for an already confirmed object.

## Fail-closed profile and fixtures

The rules profile accepts no unknown or missing keys, no null/non-mapping
sections, no wrong primitive types, and no unsupported enum value. It requires
`CANONICAL_RULES_ONLY`, `implementation_enabled: false`, parser integration
disabled, and every prohibition enabled.

The five JSON fixture files under `tests/fixtures/segment_rules/` form the
versioned decision table. Every fixture records `rule_ids`, `classification`,
`input`, `expected`, and `reason_code`; contract tests require all 34 requested
fixture IDs plus dedicated SG and frozen-prefix cases, 36 total.

## Explicit non-implementation gates

This PR does not implement a `SegmentEngine`, create a Segment domain object,
add `structures.segments`, or connect the rules profile to either Phase 1 parser.
It does not implement bounded tail recomputation, checkpoints, full/incremental
segment replay, or equivalence claims. Center/Zhongshu, trend types, divergence,
buy/sell points, CZSC, Chan.py, trading signals, position sizing, and execution
remain prohibited. The next gate is independent review of these canonical and
engineering decisions before any segment-engine implementation restarts.
