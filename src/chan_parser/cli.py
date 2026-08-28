"""Supported close-only production command line entry point."""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from .adapters import CSVAdapter
from .engine.incremental import IncrementalEngine
from .output import Serializer


class DailyReleaseError(ValueError):
    """A daily release preflight or production gate failed closed."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _local_timestamp(value: datetime, timezone: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone)
    return value.astimezone(timezone)


def _validate_daily_input(
    bars,
    quality: dict,
    *,
    profile: dict,
    timezone: ZoneInfo,
    now: datetime,
    close: time,
) -> date:
    if profile.get("runtime", {}).get("close_only") is not True:
        raise DailyReleaseError("DAILY_PROFILE_NOT_CLOSE_ONLY")
    if quality.get("parse_errors", 0):
        raise DailyReleaseError("DAILY_INPUT_INVALID")
    if not bars or any(not bar.is_valid for bar in bars):
        if quality.get("duplicate_count", 0):
            raise DailyReleaseError("DAILY_DUPLICATE_SESSION")
        if any("non-monotonic timestamp" in error for bar in bars for error in bar.validation_errors):
            raise DailyReleaseError("DAILY_TIMESTAMP_NON_MONOTONIC")
        raise DailyReleaseError("DAILY_INPUT_INVALID")
    if any(a.timestamp >= b.timestamp for a, b in zip(bars, bars[1:])):
        raise DailyReleaseError("DAILY_TIMESTAMP_NON_MONOTONIC")

    local_dates = [_local_timestamp(bar.timestamp, timezone).date() for bar in bars]
    if len(local_dates) != len(set(local_dates)):
        raise DailyReleaseError("DAILY_DUPLICATE_SESSION")

    minimum = profile.get("data_quality", {}).get("min_bars_required")
    if type(minimum) is not int or len(bars) < minimum:
        raise DailyReleaseError("DAILY_MIN_BARS_NOT_MET")

    current = _local_timestamp(now, timezone)
    latest = local_dates[-1]
    today = current.date()
    if latest > today:
        raise DailyReleaseError("DAILY_BAR_FROM_FUTURE")
    if latest == today and current.time().replace(tzinfo=None) < close:
        raise DailyReleaseError("DAILY_BAR_NOT_CLOSED")
    return latest


def run_daily(
    *,
    input_path: str,
    output_path: str,
    profile_path: str,
    symbol: str,
    market_tz: str = "Asia/Shanghai",
    close_time: str = "15:00",
    now: datetime | None = None,
) -> dict:
    """Run the close-only daily production pipeline.

    ``now`` is injectable for deterministic tests; the command uses the
    current clock when it is omitted.
    """
    try:
        timezone = ZoneInfo(market_tz)
    except ZoneInfoNotFoundError as error:
        raise DailyReleaseError("DAILY_INPUT_INVALID") from error
    try:
        close = time.fromisoformat(close_time)
        profile = yaml.safe_load(Path(profile_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, yaml.YAMLError) as error:
        raise DailyReleaseError("DAILY_INPUT_INVALID") from error

    adapter = CSVAdapter(input_path)
    try:
        bars, quality = adapter.load()
    except (OSError, ValueError) as error:
        raise DailyReleaseError("DAILY_INPUT_INVALID") from error
    latest_closed_date = _validate_daily_input(
        bars,
        quality,
        profile=profile,
        timezone=timezone,
        now=now or datetime.now(timezone),
        close=close,
    )

    engine = IncrementalEngine(profile, segment_production_enabled=True)
    try:
        state = None
        for bar in bars:
            state = engine.append_one(bar)
    except ValueError as error:
        if str(error) == "SEGMENT_SECOND_CASE_PENDING":
            raise DailyReleaseError("SEGMENT_SECOND_CASE_PENDING") from error
        raise DailyReleaseError("DAILY_INPUT_INVALID") from error

    state["meta"]["symbol"] = symbol
    state["meta"]["bar_frequency"] = "1d"
    state["meta"]["analysis_mode"] = "close_only"
    state["audit"]["input_sha256"] = adapter.input_checksum
    output_hash = Serializer().compute_content_hash(state)
    Serializer().save(state, output_path)
    print("DAILY_RELEASE_STATUS=PASS")
    print(f"SYMBOL={symbol}")
    print("FREQUENCY=1d")
    print(f"LATEST_CLOSED_DATE={latest_closed_date.isoformat()}")
    print(f"INPUT_BAR_COUNT={len(bars)}")
    print(f"INPUT_CHECKSUM={adapter.input_checksum}")
    print(f"SEGMENT_COUNT={len(state['structures'].get('segments', []))}")
    print(f"CHECKPOINT_COUNT={state['runtime_state']['checkpoint_count']}")
    print(f"OUTPUT_SHA256={output_hash}")
    print("SECOND_CASE_PENDING=NO")
    return state


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chan-parse")
    subparsers = parser.add_subparsers(dest="command")
    daily = subparsers.add_parser("daily", help="run the close-only daily parser")
    daily.add_argument("--input", required=True)
    daily.add_argument("--output", required=True)
    daily.add_argument("--profile", required=True)
    daily.add_argument("--symbol", required=True)
    daily.add_argument("--market-tz", default="Asia/Shanghai")
    daily.add_argument("--close-time", default="15:00")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command != "daily":
        parser.print_help()
        return 0
    try:
        run_daily(
            input_path=args.input,
            output_path=args.output,
            profile_path=args.profile,
            symbol=args.symbol,
            market_tz=args.market_tz,
            close_time=args.close_time,
        )
    except DailyReleaseError as error:
        print("DAILY_RELEASE_STATUS=BLOCKED")
        print(f"BLOCK_REASON={error.reason_code}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
