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
