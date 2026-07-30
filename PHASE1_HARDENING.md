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

## Review closeout additions

- GitHub Actions uses `contents: read` and does not persist checkout credentials.
- Unsupported fractal profile values fail closed; Phase 1 supports only a 3-bar merged-K window with minimum distance 1.
- Historical snapshots and checkpoints use bounded retention (`20` and `10` by default).
- Incremental data-quality status follows the same invalid/duplicate/ordering rule as full rebuild.
- Every discarded stroke candidate or unconfirmed tail receives a terminal lifecycle event.
