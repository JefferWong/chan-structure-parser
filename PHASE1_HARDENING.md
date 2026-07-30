# Phase 1 hardening

This branch remains limited to inclusion processing, fractals, and strict strokes.

Implemented gates:

- deterministic append-only lifecycle events;
- strict stroke rejection before object creation;
- immutable historical snapshots and checkpoint restore;
- frozen confirmed prefix with bounded tail recomputation;
- engine-input instrumentation proving that inclusion, fractal, and stroke engines receive only the bounded window;
- full/incremental structural consistency;
- Python 3.10–3.12 hosted test matrix.

No segment, center, CZSC, Chan.py, position, or trading-signal functionality is included.

## Incremental boundary regressions

- Preserve the actual carried inclusion direction across equal-high/equal-low boundaries.
- Use globally stable, unique fractal and stroke IDs derived from structure coordinates.
- Record newly triggered candidate-rejection and replacement diagnostics during tail reconciliation.
- Differential replay covers discrete-price ties and multiple append chunk sizes.
