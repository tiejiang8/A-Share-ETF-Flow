from __future__ import annotations

from pathlib import Path

import pandas as pd

from etf_temperature import analysis_backfill


def _universe() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "etf_code": "510300",
                "etf_name": "300ETF",
                "exchange": "SSE",
                "listing_date": "2020-01-01",
                "is_a_share_equity_etf": True,
            }
        ]
    )


def test_discover_trading_calendar_uses_quote_coverage_not_weekdays() -> None:
    quotes = pd.DataFrame(
        [
            {"date": "2026-07-13", "etf_code": "510300", "close": 4.0},
            {"date": "2026-07-15", "etf_code": "510300", "close": 4.1},
        ]
    )

    result = analysis_backfill.discover_trading_calendar(
        quotes,
        _universe(),
        "2026-07-13",
        "2026-07-15",
    )

    assert result["date"].tolist() == ["2026-07-13", "2026-07-15"]
    assert result["quote_coverage"].tolist() == [1.0, 1.0]


def test_valid_timeseries_rows_drops_collector_failure_markers() -> None:
    frame = pd.DataFrame(
        [
            {"etf_code": "510300", "date": "20260717", "close": 4.2},
            {"etf_code": "159919", "validation_status": "quote_fetch_failed"},
        ]
    )

    result = analysis_backfill._valid_timeseries_rows(frame)

    assert result[["etf_code", "date"]].to_dict("records") == [
        {"etf_code": "510300", "date": "2026-07-17"}
    ]


def test_build_analysis_from_cache_writes_only_analysis_artifacts(monkeypatch, tmp_path: Path) -> None:
    universe = _universe()
    membership = pd.DataFrame(
        [
            {
                "etf_code": "510300",
                "primary_cluster": "C02",
                "primary_cluster_name": "大盘宽基",
                "secondary_tags": "",
                "classification_basis": "test",
                "confidence": 1.0,
            }
        ]
    )
    quotes = pd.DataFrame(
        [
            {"date": "2026-07-13", "etf_code": "510300", "close": 4.0},
            {"date": "2026-07-15", "etf_code": "510300", "close": 4.1},
        ]
    )

    monkeypatch.setattr(analysis_backfill, "ANALYSIS_DIR", tmp_path / "analysis")
    monkeypatch.setattr(analysis_backfill, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(analysis_backfill, "ensure_dirs", lambda: None)
    monkeypatch.setattr(
        analysis_backfill,
        "read_dataframe",
        lambda path: universe.copy() if Path(path).name == "etf_universe.parquet" else pd.DataFrame(),
    )
    monkeypatch.setattr(
        analysis_backfill,
        "select_timeseries_cache",
        lambda kind, **kwargs: quotes.copy() if kind == "quotes" else pd.DataFrame(),
    )
    monkeypatch.setattr(analysis_backfill, "apply_anchor_supplements", lambda frame: frame)
    monkeypatch.setattr(analysis_backfill, "apply_a_share_filter", lambda frame: frame)
    monkeypatch.setattr(analysis_backfill, "classify_universe", lambda frame: membership.copy())
    monkeypatch.setattr(analysis_backfill, "_a_share_universe_as_of", lambda frame, day, quote_history: frame.copy())

    def fake_daily(active, active_membership, quote_frame, shares, nav, day, aum=None):
        return pd.DataFrame(
            [
                {
                    "date": day,
                    "etf_code": "510300",
                    "etf_name": "300ETF",
                    "primary_cluster": "C02",
                    "close": 4.0,
                }
            ]
        )

    monkeypatch.setattr(analysis_backfill, "build_etf_daily", fake_daily)
    monkeypatch.setattr(analysis_backfill, "apply_adjusted_history_features", lambda frame, history: frame)
    monkeypatch.setattr(analysis_backfill, "_apply_flow_audit_status", lambda frame: (frame, pd.DataFrame()))
    monkeypatch.setattr(analysis_backfill, "add_flow_speed_columns", lambda frame: frame)
    monkeypatch.setattr(analysis_backfill, "compute_product_temperature", lambda frame: frame)
    monkeypatch.setattr(analysis_backfill, "add_product_temperature_history_features", lambda frame, history: frame)
    monkeypatch.setattr(
        analysis_backfill,
        "build_cluster_daily",
        lambda frame: pd.DataFrame([{"cluster_id": "C02", "cluster_name": "大盘宽基"}]),
    )
    monkeypatch.setattr(analysis_backfill, "add_cluster_temperature_history_features", lambda frame, history: frame)
    monkeypatch.setattr(
        analysis_backfill,
        "coverage_metrics",
        lambda *args, **kwargs: {
            "a_share_equity_etf_count": 1,
            "quote_coverage": 1.0,
            "share_coverage": 1.0,
            "nav_coverage": 1.0,
            "flow_size_coverage": 1.0,
            "important_unclassified_count": 0,
            "extreme_flow_unresolved_count": 0,
            "formal_report_allowed": True,
            "formal_blockers": [],
        },
    )

    result = analysis_backfill.build_analysis_from_cache("2026-07-13", "2026-07-15")

    assert result["analysis_ready"]
    assert result["trading_date_count"] == 2
    assert result["etf_rows"] == 2
    assert Path(result["etf_path"]).exists()
    assert Path(result["cluster_path"]).exists()
    assert not list(tmp_path.rglob("*.md"))


def test_cli_backfill_market_data_only_collects(monkeypatch, capsys) -> None:
    from etf_temperature import cli

    captured = {}

    def fake_backfill(start, end, **kwargs):
        captured.update({"start": start, "end": end, **kwargs})
        return {"market_data_ready": True, "quote_cache_rows": 100}

    monkeypatch.setattr(cli, "backfill_market_data", fake_backfill)

    exit_code = cli.main(
        [
            "backfill-market-data",
            "--start",
            "20260101",
            "--end",
            "20260717",
        ]
    )

    assert exit_code == 0
    assert captured["warmup_days"] == 45
    assert captured["skip_szse_shares"] is False
    assert captured["skip_fund_company_aum"] is False
    assert '"market_data_ready": true' in capsys.readouterr().out
