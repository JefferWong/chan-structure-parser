# Phase 2 segment contract and gates

## Scope

Phase 1 is frozen at squash commit
`de1b7f589ebe3c2a41fa6501d793200a7b595426`.

This phase defines the evidence contract, lifecycle schema, stable identity, and
acceptance gates required before a line-segment construction engine may be
implemented. It does **not** construct segments and does not modify the Phase 1
full or incremental parser paths.

## Canonical boundary

The following statements are treated as hard boundaries:

- a line segment is not a collection of strokes with the same direction;
- three strokes are necessary for a minimal candidate window but are not
  sufficient to confirm a segment;
- strokes in the candidate window must alternate direction and share endpoints;
- the feature sequence is derived from strokes opposite to the candidate
  segment direction;
- confirmation cannot be inferred from stroke count alone;
- confirmation requires explicit later destruction evidence;
- the confirmation timestamp cannot precede the evidence that confirms it.

The exact feature-sequence fractal, gap handling, first/second destruction cases,
and segment replacement algorithm remain unimplemented. They require a separate
implementation PR and differential acceptance suite.

## Contract-only artifacts

- `configs/profiles/minimal_segment_contract_v1.yaml`
- `src/chan_parser/domain/segment.py`
- `src/chan_parser/contracts/segment.py`
- contract and non-integration tests

`SegmentContractValidator` validates whether a stroke window is eligible input
for a future segment algorithm. An accepted result explicitly reports:

- `contract_only: true`
- `segment_constructed: false`
- `segment_confirmed: false`

It must never be interpreted as a segment output.

## Lifecycle contract

A future segment engine must use the existing lifecycle states:

- `CANDIDATE`
- `PROVISIONAL`
- `CONFIRMED`
- `INVALIDATED`
- `REPLACED`

Every created segment object must eventually remain active or receive a terminal
invalidated/replaced event. Confirmation requires explicit evidence stroke IDs
and an `earliest_confirmation_bar` at or after that evidence.

## Identity contract

Segment candidate identity is content-based and includes:

- profile ID and version;
- ordered stroke logical identities and content hashes;
- ordered destruction-evidence identities and content hashes.

Sequential numbering alone is forbidden because bounded tail recomputation can
recreate different structures at the same position.

## Phase 2 contract gates

The contract PR passes only when:

1. the Phase 1 baseline remains unchanged;
2. no `segments` key appears in full or incremental parser output;
3. unsupported profile values fail closed;
4. fewer than three strokes are rejected;
5. even-length candidate windows are rejected;
6. non-alternating or disconnected strokes are rejected;
7. only the tail may be provisional for candidate/provisional validation;
8. confirmed eligibility requires all window strokes confirmed;
9. confirmed eligibility requires explicit later confirmed destruction evidence;
10. stable identity is deterministic and changes with evidence content;
11. Python 3.10–3.12 hosted tests pass;
12. no center, CZSC, Chan.py, signal, position, or execution code is introduced.

## Next implementation PR

Only after this contract PR is reviewed and merged may the segment algorithm be
implemented. That PR must separately cover:

- feature-sequence extraction;
- feature-sequence inclusion handling;
- top/bottom feature-sequence fractals;
- gap and no-gap destruction cases;
- first and second segment-destruction cases;
- lifecycle replacement/invalidation events;
- frozen-prefix bounded tail recomputation;
- full/incremental differential replay and checkpoint restore.

Center or Zhongshu work remains prohibited until the segment implementation and
its hosted acceptance gates pass.
