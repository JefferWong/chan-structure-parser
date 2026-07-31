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
- fixture_ids: `FS-CROSS-SEQUENCE-REJECT-001`,
  `PRIMARY-SEQUENCE-ID-MISMATCH-REJECT-001`

### FS-003

- rule_id: `FS-003`
- classification: `ORIGINAL_CANONICAL_CORE`
- statement: Inclusion normalization converts a raw feature sequence into a
  standard feature sequence without losing ordered stroke provenance.
- input: One raw feature sequence and its inclusion decisions.
- output: Standard feature elements with complete source logical IDs.
- fail_closed_condition: Lost, duplicated, or reordered provenance.
- fixture_ids: `INCLUSION-PROVENANCE-001`,
  `PRIMARY-DUPLICATE-ELEMENT-ID-REJECT-001`,
  `PRIMARY-NONNORMALIZED-ELEMENT-REJECT-001`,
  `PRIMARY-EMPTY-PROVENANCE-REJECT-001`,
  `PRIMARY-DUPLICATE-PROVENANCE-REJECT-001`,
  `PRIMARY-PROVENANCE-MISMATCH-REJECT-001`

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
  fractal, unnormalized elements, duplicate element identity, sequence mismatch,
  or disconnected endpoints. The three immutable second-sequence elements must
  start at the pending endpoint, connect left-to-center-to-right, retain unique
  provenance, and bind by shared endpoint identity. The original canonical core
  requires the second sequence to start at the shared endpoint; endpoint IDs and
  provenance containers are engineering evidence representations.
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
  segment continues. Strictness is calculated from direction and finite prices:
  UP requires `observed > pending`, DOWN requires `observed < pending`.
- input: Pending second case derived from identified primary destruction
  evidence; separate observed price/bar/provenance evidence bound to the same
  primary evidence key and pending endpoint ID.
- output: `PENDING_DESTRUCTION_INVALIDATED`.
- fail_closed_condition: Caller-supplied boolean conclusions or duplicate
  pending price/bar inputs, nonfinite prices, invalid or non-increasing bar
  order, equality treated as strict, or evidence-key/endpoint mismatch.
- fixture_ids: `CASE2-UP-STRICT-NEW-HIGH-INVALIDATE-001`,
  `CASE2-DOWN-STRICT-NEW-LOW-INVALIDATE-001`,
  `CASE2-UP-EQUAL-HIGH-STAYS-PENDING-001`,
  `CASE2-DOWN-EQUAL-LOW-STAYS-PENDING-001`,
  `CASE2-WRONG-DIRECTION-STAYS-PENDING-001`,
  `CASE2-EXTREME-BAR-ORDER-REJECT-001`,
  `CASE2-NEGATIVE-BAR-REJECT-001`

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
  For deterministic evidence representation, the pending endpoint is one
  immutable `FeatureEndpointEvidence` holding its ID, defining-stroke IDs,
  price, and bar. Every standard feature element carries direction, complete
  start/end/high/low endpoint evidence, visibility time, sequence identity,
  normalization, and provenance. Reuse of an endpoint ID requires the entire
  endpoint evidence object to agree; high/low bars must lie within the element
  and visibility must follow all endpoint evidence. Direction requires a strict
  start-to-end price change, so a flat element cannot be labeled UP or DOWN.
  Primary destruction returns stable
  content-addressed evidence; pending context can only be constructed from that
  evidence and cannot resubmit direction or endpoint baseline.
- input: Included elements plus boundary-side and sequence-kind metadata, or a
  structurally bound three-element standard-feature window.
- output: Merge permission or
  `HYPOTHETICAL_BOUNDARY_DIFFERENT_NATURE`; for primary classification, an
  immutable `PrimaryDestructionEvidence` whose endpoint is the center TOP high
  endpoint for UP or center BOTTOM low endpoint for DOWN.
- fail_closed_condition: Cross-boundary first-case merge, suppressed
  second-sequence normalization, duplicate pending baselines, bare intervals,
  non-adjacent endpoints, cross-sequence elements, or incomplete provenance.
- fixture_ids: `INCLUSION-FIRST-BOUNDARY-NOMERGE-001`,
  `INCLUSION-SECOND-SEQUENCE-MERGE-001`,
  `CASE2-SECOND-SEQUENCE-INCLUSION-001`,
  `PRIMARY-LEFT-CENTER-DISCONNECTED-REJECT-001`,
  `PRIMARY-CENTER-RIGHT-DISCONNECTED-REJECT-001`,
  `PRIMARY-ENDPOINT-PRICE-MISMATCH-REJECT-001`,
  `PRIMARY-ENDPOINT-BAR-MISMATCH-REJECT-001`,
  `PENDING-CONTEXT-ENDPOINT-DERIVED-001`,
  `PRIMARY-EVIDENCE-KEY-DETERMINISTIC-001`

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
  Endpoint time is never used to backfill confirmation. Secondary confirmation
  and original-direction extreme evidence must bind the same primary evidence
  key and pending endpoint ID. A single pure arbitration function selects the
  earlier evidence; confirmation wins when both occur on the same bar.
- input: Bound immutable secondary-confirmation and/or extreme evidence.
- output: `SECOND_CASE_PENDING`, `SECOND_CASE_CONFIRMED`, or `INVALIDATED`.
- fail_closed_condition: Missing visibility evidence, bool/float/negative bar
  index, result before endpoint, malformed evidence, or binding mismatch.
- fixture_ids: `TIMING-NO-BACKFILL-001`,
  `CASE2-ARBITRATION-CONFIRM-BEFORE-EXTREME-001`,
  `CASE2-ARBITRATION-EXTREME-BEFORE-CONFIRM-001`,
  `CASE2-ARBITRATION-SAME-BAR-CONFIRM-WINS-001`,
  `CASE2-ARBITRATION-NONSTRICT-THEN-CONFIRM-001`,
  `CASE2-ARBITRATION-EVIDENCE-KEY-MISMATCH-REJECT-001`,
  `CASE2-ARBITRATION-ENDPOINT-MISMATCH-REJECT-001`,
  `CASE2-ARBITRATION-ORDER-INDEPENDENT-001`

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
- statement: A minimum window with no later evidence is `CANDIDATE`; a candidate
  with explicit incomplete evidence is `PROVISIONAL`; complete case evidence is
  `CONFIRMED`; never-confirmed failures are `INVALIDATED`; a confirmed active
  segment terminated by a newly confirmed reverse segment becomes `REPLACED`,
  with `replaced_by` set to the reverse segment logical ID. No lifecycle object
  exists without a minimum candidate window, and no `DESTROYED` state is added.
- input: Candidate-window presence, explicit provisional/complete/invalidated
  evidence, prior confirmation state, and optional confirmed reverse logical ID.
- output: Existing lifecycle state and optional `replaced_by`.
- fail_closed_condition: Evidence without a candidate, attempting a lifecycle
  with no candidate/evidence, contradictory evidence flags, invalidating a
  confirmed segment, replacing an unconfirmed candidate, or an incomplete
  confirmed-reverse-ID pair.
- fixture_ids: `LIFECYCLE-INVALIDATED-001`, `LIFECYCLE-REPLACED-001`,
  `LIFECYCLE-NO-CANDIDATE-REJECT-001`, `LIFECYCLE-CANDIDATE-001`,
  `LIFECYCLE-PROVISIONAL-001`,
  `LIFECYCLE-EVIDENCE-WITHOUT-CANDIDATE-REJECT-001`,
  `LIFECYCLE-CONTRADICTORY-EVIDENCE-REJECT-001`

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
- fixture_ids: `FREEZE-APPEND-001`

## Decision flow

The reference oracle first normalizes immutable closed intervals, applies
same-sequence inclusion only after a strict seed exists, and classifies only a
strict three-element standard-feature window. Primary and secondary classifiers
accept only adjacent, normalized elements from one declared sequence with
complete, non-overlapping provenance and fully equal shared endpoint evidence.
Primary feature directions are opposite the candidate direction; secondary
feature directions equal the original segment direction. A direction-compatible primary fractal
without a first-to-center gap yields first-case evidence. With a gap it remains
second-case pending until the required reverse-candidate feature fractal appears.
The primary evidence key binds ordered element IDs, direction, sequence, case,
fractal, and derived endpoint. It is the only source for the pending context.

Pen break and canonical segment destruction remain distinct. Canonical
destruction is evidence classification; `REPLACED` is a later engineering
lifecycle mapping for an already confirmed object. The primary classifier accepts
only a complete three-element fractal window and gap evidence; the separate
pen-break oracle handles provisional one-pen evidence, so no meaningless
`pen_break_observed` flag exists on primary classification.

All public bar indexes are exact, nonnegative integers: bool, float, and negative
values fail closed. Pending price and bar exist only in the immutable
`FeatureEndpointEvidence`; strict-extreme evidence cannot resubmit or replace
that baseline. Extreme observation must follow its pending endpoint, and
confirmation cannot precede its endpoint. Equality at the pending price and
movement in the wrong direction remain `SECOND_CASE_PENDING`.

Secondary confirmation is immutable evidence bound to the primary evidence key,
pending endpoint ID, secondary sequence, ordered element IDs, right-element
visibility time, embedded immutable elements, and rule version. The arbiter
revalidates those elements and derives confirmation time from the right
element, rather than trusting a detached bar. Extreme evidence binds the same
primary key and endpoint ID. `resolve_second_case_outcome` is the sole final arbitration
path: earlier strict extreme invalidates, earlier confirmation confirms, a
non-strict extreme cannot defeat confirmation, and same-bar confirmation wins.
`resolve_second_case_evidence_sequence` normalizes immutable confirmation-first
or extreme-first arrival tuples into that sole arbiter, proving arrival order
cannot change the outcome. These evidence objects,
content hashes, and the same-bar tie-break are
`ENGINEERING_DETERMINISM_V1`, not claims about original textual formalization.

A second sequence is not established by caller booleans. Its three immutable
elements must share the pending sequence ID, be individually normalized, connect
by endpoint identity, begin at the pending endpoint, preserve unique provenance,
and match the secondary context provenance. Endpoint defining-stroke IDs are
engineering audit evidence and need not occur in the left element provenance;
the original theory does not prescribe such a provenance coupling.

## Fail-closed profile and fixtures

The rules profile accepts no unknown or missing keys, no null/non-mapping
sections, no wrong primitive types, and no unsupported enum value. It requires
`CANONICAL_RULES_ONLY`, `implementation_enabled: false`, parser integration
disabled, and every prohibition enabled.

The five JSON fixture files under `tests/fixtures/segment_rules/` form the
versioned decision table. Every fixture records `rule_ids`, `classification`,
`input`, `expected`, and `reason_code`. The original fixture set contained 36
rows; review hardening expanded the executable table to 61 rows, including
strict-extreme, lifecycle, primary structural, second-sequence continuity, and
evidence-chain arbitration gates. The final executable table contains 80 rows.

## Explicit non-implementation gates

This PR does not implement a `SegmentEngine`, create a Segment domain object,
add `structures.segments`, or connect the rules profile to either Phase 1 parser.
It does not implement bounded tail recomputation, checkpoints, full/incremental
segment replay, or equivalence claims. Center/Zhongshu, trend types, divergence,
buy/sell points, CZSC, Chan.py, trading signals, position sizing, and execution
remain prohibited. The next gate is independent review of these canonical and
engineering decisions before any segment-engine implementation restarts.
