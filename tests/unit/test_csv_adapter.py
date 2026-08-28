from pathlib import Path

from chan_parser.adapters.csv_adapter import CSVAdapter


def write_csv(path: Path, rows: list[str]) -> None:
    path.write_text(
        "date,open,high,low,close,volume\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )


def test_csv_assigns_contiguous_identity_and_preserves_source_line(tmp_path):
    path = tmp_path / "bars.csv"
    write_csv(path, [
        "2024-01-01,1,2,0,1.5,10",
        "bad-date,1,2,0,1.5,10",
        "2024-01-02,2,3,1,2.5,10",
    ])

    bars, quality = CSVAdapter(str(path)).load()

    assert quality["parse_errors"] == 1
    assert [bar.bar_id for bar in bars] == ["bar_000001", "bar_000002"]
    assert [bar.bar_index for bar in bars] == [0, 1]
    assert [bar.source_line for bar in bars] == [2, 4]
    assert bars[0].content_hash() != bars[1].content_hash()


def test_csv_invalid_materialized_row_keeps_identity_sequence(tmp_path):
    path = tmp_path / "invalid.csv"
    write_csv(path, [
        "2024-01-01,1,2,0,1.5,10",
        "2024-01-02,3,1,2,2.5,10",
        "2024-01-03,2,4,1,3.5,10",
    ])

    bars, quality = CSVAdapter(str(path)).load()

    assert quality["parse_errors"] == 0
    assert [bar.bar_id for bar in bars] == [
        "bar_000001", "bar_000002", "bar_000003"
    ]
    assert [bar.bar_index for bar in bars] == [0, 1, 2]
    assert bars[1].is_valid is False
    assert [bar.source_line for bar in bars] == [2, 3, 4]
