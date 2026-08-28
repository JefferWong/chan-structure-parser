from datetime import datetime, timedelta
from pathlib import Path
import json
import hashlib
import subprocess
import sys

import pytest
import yaml

from chan_parser.cli import DailyReleaseError, run_daily
from chan_parser.domain.raw_bar import RawBar

ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "configs/profiles/minimal_strict_v1.yaml"


def profile():
    return yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))


def write_rows(path: Path, rows: list[tuple[datetime, float, float, float, float]]) -> None:
    path.write_text(
        "date,open,high,low,close,volume\n"
        + "\n".join(
            f"{when:%Y-%m-%d},{opening},{high},{low},{closing},1000"
            for when, opening, high, low, closing in rows
        )
        + "\n",
        encoding="utf-8",
    )


def fixture_rows(start: datetime = datetime(2024, 1, 2)):
    points = [100.0]
    targets = [108, 91, 111, 88, 109, 91, 110, 92, 111, 87]
    for target in targets:
        starting = points[-1]
        step = (target - starting) / 5
        points.extend(starting + step * index for index in range(1, 6))
    return [
        (start + timedelta(days=index), value, value + 0.01, value - 0.01, value)
        for index, value in enumerate(points)
    ]


def write_fixture(tmp_path):
    path = tmp_path / "daily.csv"
    write_rows(path, fixture_rows())
    return path


def test_daily_production_output_contains_structures_segments_and_metrics(tmp_path):
    input_path = write_fixture(tmp_path)
    output_path = tmp_path / "result.json"

    state = run_daily(
        input_path=str(input_path),
        output_path=str(output_path),
        profile_path=str(PROFILE_PATH),
        symbol="600519.SH",
        now=datetime(2026, 8, 28, 16, 0),
    )

    assert output_path.exists()
    assert {"merged_bars", "fractals", "strokes", "segments"} <= set(state["structures"])
    assert state["structures"]["segments"]
    assert "segment_metrics" in state["runtime_state"]
    assert state["runtime_state"]["checkpoint_count"] >= 1
    assert state["meta"]["symbol"] == "600519.SH"
    assert state["meta"]["bar_frequency"] == "1d"
    assert state["audit"]["input_checksum"]
    assert "input_sha256" not in state["audit"]


def test_daily_output_hash_marker_matches_written_bytes_and_semantic_hash_is_stable(tmp_path, capsys):
    input_path = write_fixture(tmp_path)
    output_path = tmp_path / "hashed.json"
    run_daily(
        input_path=str(input_path), output_path=str(output_path),
        profile_path=str(PROFILE_PATH), symbol="X",
        now=datetime(2026, 8, 28, 16),
    )
    markers = dict(
        line.split("=", 1)
        for line in capsys.readouterr().out.splitlines()
        if "=" in line
    )
    assert len(markers["OUTPUT_SHA256"]) == 64
    assert markers["OUTPUT_SHA256"] == hashlib.sha256(output_path.read_bytes()).hexdigest()
    assert len(markers["OUTPUT_SEMANTIC_HASH"]) == 16

    second_path = tmp_path / "hashed-second.json"
    run_daily(
        input_path=str(input_path), output_path=str(second_path),
        profile_path=str(PROFILE_PATH), symbol="X",
        now=datetime(2026, 8, 28, 16),
    )
    second_markers = dict(
        line.split("=", 1)
        for line in capsys.readouterr().out.splitlines()
        if "=" in line
    )
    assert second_markers["OUTPUT_SEMANTIC_HASH"] == markers["OUTPUT_SEMANTIC_HASH"]


def test_installed_declared_chan_parse_daily_command(tmp_path):
    input_path = write_fixture(tmp_path)
    output_path = tmp_path / "installed.json"
    completed = subprocess.run(
        [
            str(Path(sys.executable).with_name("chan-parse")), "daily", "--input", str(input_path),
            "--output", str(output_path), "--profile", str(PROFILE_PATH),
            "--symbol", "600519.SH",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "DAILY_RELEASE_STATUS=PASS" in completed.stdout
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["structures"]["segments"]
    assert "segment_metrics" in payload["runtime_state"]


def test_daily_replay_is_semantically_deterministic(tmp_path):
    input_path = write_fixture(tmp_path)
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    kwargs = {
        "input_path": str(input_path),
        "profile_path": str(PROFILE_PATH),
        "symbol": "600519.SH",
        "now": datetime(2026, 8, 28, 16),
    }
    run_daily(output_path=str(first_path), **kwargs)
    run_daily(output_path=str(second_path), **kwargs)
    first = json.loads(first_path.read_text(encoding="utf-8"))
    second = json.loads(second_path.read_text(encoding="utf-8"))
    first["meta"].pop("generated_at", None)
    second["meta"].pop("generated_at", None)
    assert first == second


def test_daily_rejects_multiple_intraday_rows_for_one_session(tmp_path):
    rows = fixture_rows()[:50]
    same_day = rows[-1]
    rows.append((same_day[0].replace(hour=12), *same_day[1:]))
    input_path = tmp_path / "intraday.csv"
    write_rows(input_path, rows)
    with pytest.raises(DailyReleaseError, match="DAILY_DUPLICATE_SESSION"):
        run_daily(
            input_path=str(input_path), output_path=str(tmp_path / "result.json"),
            profile_path=str(PROFILE_PATH), symbol="X",
            now=datetime(2026, 8, 28, 16),
        )


@pytest.mark.parametrize(
    ("now", "latest", "reason"),
    [
        (datetime(2024, 2, 21, 14, 59), datetime(2024, 2, 21), "DAILY_BAR_NOT_CLOSED"),
        (datetime(2024, 2, 21, 15, 0), datetime(2024, 2, 21), None),
        (datetime(2024, 2, 21, 15, 1), datetime(2024, 2, 21), None),
        (datetime(2024, 2, 21, 14, 0), datetime(2024, 2, 20), None),
        (datetime(2024, 2, 21, 16, 0), datetime(2024, 2, 22), "DAILY_BAR_FROM_FUTURE"),
    ],
)
def test_daily_close_gate(tmp_path, now, latest, reason):
    rows = fixture_rows(start=latest - timedelta(days=50))[:51]
    input_path = tmp_path / "gate.csv"
    output_path = tmp_path / "result.json"
    rows[-1] = (latest, rows[-1][1], rows[-1][2], rows[-1][3], rows[-1][4])
    write_rows(input_path, rows)

    if reason:
        with pytest.raises(DailyReleaseError, match=reason):
            run_daily(
                input_path=str(input_path), output_path=str(output_path),
                profile_path=str(PROFILE_PATH), symbol="X", now=now,
            )
        assert not output_path.exists()
    else:
        run_daily(
            input_path=str(input_path), output_path=str(output_path),
            profile_path=str(PROFILE_PATH), symbol="X", now=now,
        )
        assert output_path.exists()


def test_daily_rejects_duplicate_session_and_preserves_existing_output(tmp_path):
    rows = fixture_rows()[:50]
    rows.append(rows[-1])
    input_path = tmp_path / "duplicate.csv"
    output_path = tmp_path / "result.json"
    output_path.write_text("sentinel", encoding="utf-8")
    write_rows(input_path, rows)

    with pytest.raises(DailyReleaseError, match="DAILY_DUPLICATE_SESSION"):
        run_daily(
            input_path=str(input_path), output_path=str(output_path),
            profile_path=str(PROFILE_PATH), symbol="X", now=datetime(2026, 8, 28, 16),
        )
    assert output_path.read_text(encoding="utf-8") == "sentinel"


def test_daily_rejects_invalid_input_profile_and_minimum(tmp_path):
    rows = fixture_rows()[:50]
    input_path = tmp_path / "invalid.csv"
    output_path = tmp_path / "result.json"
    rows[10] = (rows[10][0], 5, 1, 2, 3)
    write_rows(input_path, rows)
    with pytest.raises(DailyReleaseError, match="DAILY_INPUT_INVALID"):
        run_daily(input_path=str(input_path), output_path=str(output_path), profile_path=str(PROFILE_PATH), symbol="X", now=datetime(2026, 8, 28, 16))

    short_path = tmp_path / "short.csv"
    write_rows(short_path, fixture_rows()[:49])
    with pytest.raises(DailyReleaseError, match="DAILY_MIN_BARS_NOT_MET"):
        run_daily(input_path=str(short_path), output_path=str(output_path), profile_path=str(PROFILE_PATH), symbol="X", now=datetime(2026, 8, 28, 16))

    close_only = profile()
    close_only["runtime"]["close_only"] = False
    profile_path = tmp_path / "not-close-only.yaml"
    profile_path.write_text(yaml.safe_dump(close_only), encoding="utf-8")
    with pytest.raises(DailyReleaseError, match="DAILY_PROFILE_NOT_CLOSE_ONLY"):
        run_daily(input_path=str(short_path), output_path=str(output_path), profile_path=str(profile_path), symbol="X", now=datetime(2026, 8, 28, 16))


@pytest.mark.parametrize("profile_text", ["null\n", "[]\n", "abc\n"])
def test_daily_rejects_malformed_top_level_profiles(tmp_path, profile_text):
    input_path = write_fixture(tmp_path)
    profile_path = tmp_path / "malformed.yaml"
    profile_path.write_text(profile_text, encoding="utf-8")
    with pytest.raises(DailyReleaseError, match="DAILY_INPUT_INVALID"):
        run_daily(input_path=str(input_path), output_path=str(tmp_path / "result.json"), profile_path=str(profile_path), symbol="X", now=datetime(2026, 8, 28, 16))


@pytest.mark.parametrize("field", ["runtime", "data_quality"])
def test_daily_rejects_non_mapping_profile_sections(tmp_path, field):
    input_path = write_fixture(tmp_path)
    malformed = profile()
    malformed[field] = []
    profile_path = tmp_path / f"malformed-{field}.yaml"
    profile_path.write_text(yaml.safe_dump(malformed), encoding="utf-8")
    with pytest.raises(DailyReleaseError, match="DAILY_INPUT_INVALID"):
        run_daily(input_path=str(input_path), output_path=str(tmp_path / "result.json"), profile_path=str(profile_path), symbol="X", now=datetime(2026, 8, 28, 16))


@pytest.mark.parametrize("close_time", ["15:00+08:00", "not-a-time"])
def test_daily_rejects_invalid_close_time(tmp_path, close_time):
    input_path = write_fixture(tmp_path)
    with pytest.raises(DailyReleaseError, match="DAILY_INPUT_INVALID"):
        run_daily(input_path=str(input_path), output_path=str(tmp_path / "result.json"), profile_path=str(PROFILE_PATH), symbol="X", close_time=close_time, now=datetime(2026, 8, 28, 16))


@pytest.mark.parametrize("collision", ["input", "profile"])
def test_daily_rejects_output_path_collisions_without_changing_sources(tmp_path, collision):
    input_path = write_fixture(tmp_path)
    profile_path = PROFILE_PATH
    output_path = input_path if collision == "input" else profile_path
    input_bytes = input_path.read_bytes()
    profile_bytes = profile_path.read_bytes()
    with pytest.raises(DailyReleaseError, match="DAILY_OUTPUT_PATH_CONFLICT"):
        run_daily(input_path=str(input_path), output_path=str(output_path), profile_path=str(profile_path), symbol="X", now=datetime(2026, 8, 28, 16))
    assert input_path.read_bytes() == input_bytes
    assert profile_path.read_bytes() == profile_bytes


def test_main_blocks_malformed_profile_with_machine_readable_marker(tmp_path, capsys):
    input_path = write_fixture(tmp_path)
    profile_path = tmp_path / "null.yaml"
    profile_path.write_text("null\n", encoding="utf-8")
    from chan_parser.cli import main
    result = main([
        "daily", "--input", str(input_path), "--output", str(tmp_path / "result.json"),
        "--profile", str(profile_path), "--symbol", "X",
    ])
    assert result != 0
    assert capsys.readouterr().out.splitlines() == [
        "DAILY_RELEASE_STATUS=BLOCKED", "BLOCK_REASON=DAILY_INPUT_INVALID"
    ]


def test_main_blocks_output_write_failure(monkeypatch, tmp_path, capsys):
    input_path = write_fixture(tmp_path)
    from chan_parser.cli import main

    def fail_save(self, output, filepath, pretty=True):
        raise OSError("write denied")

    monkeypatch.setattr("chan_parser.cli.Serializer.save", fail_save)
    result = main([
        "daily", "--input", str(input_path), "--output", str(tmp_path / "result.json"),
        "--profile", str(PROFILE_PATH), "--symbol", "X",
    ])
    assert result != 0
    assert capsys.readouterr().out.splitlines() == [
        "DAILY_RELEASE_STATUS=BLOCKED", "BLOCK_REASON=DAILY_OUTPUT_WRITE_FAILED"
    ]


def test_daily_second_case_is_explicitly_blocked(monkeypatch, tmp_path):
    class RaisingEngine:
        def __init__(self, *args, **kwargs):
            pass

        def append_one(self, bar):
            raise ValueError("SEGMENT_SECOND_CASE_PENDING")

    monkeypatch.setattr("chan_parser.cli.IncrementalEngine", RaisingEngine)
    input_path = write_fixture(tmp_path)
    with pytest.raises(DailyReleaseError, match="SEGMENT_SECOND_CASE_PENDING"):
        run_daily(input_path=str(input_path), output_path=str(tmp_path / "result.json"), profile_path=str(PROFILE_PATH), symbol="X", now=datetime(2026, 8, 28, 16))
