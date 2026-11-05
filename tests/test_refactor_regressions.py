from types import SimpleNamespace

import pandas as pd

from etf_temperature.classification import classify_universe
from etf_temperature.corporate_actions import apply_corporate_action_adjustments
from etf_temperature.official_endpoints import write_dataframe
from etf_temperature.pipeline import _a_share_universe_as_of, _apply_flow_audit_status
from etf_temperature.report import _fmt_value
from etf_temperature.snapshot import SnapshotStore
from etf_temperature.universe import classify_a_share_equity
from etf_temperature.validation import _field_freshness
from etf_temperature.indicators import compute_quote_features
from etf_temperature.history import upsert_history_file


def test_snapshot_store_binds_validation_to_requested_date(tmp_path):
    write_dataframe(
        pd.DataFrame(
            [
                {"date": "2026-07-01", "etf_code": "510050", "primary_cluster": "C01"},
                {"date": "2026-07-02", "etf_code": "510300", "primary_cluster": "C02"},
            ]
        ),
        tmp_path / "etf_daily_history.parquet",
    )
    write_dataframe(
        pd.DataFrame(
            [
                {"date": "2026-07-01", "cluster_id": "C01"},
                {"date": "2026-07-02", "cluster_id": "C02"},
            ]
        ),
        tmp_path / "cluster_daily_history.parquet",
    )

    snapshot = SnapshotStore(tmp_path).load("2026-07-01")

    assert snapshot.report_date == "2026-07-01"
    assert snapshot.etf_daily["etf_code"].tolist() == ["510050"]
    assert snapshot.cluster_daily["cluster_id"].tolist() == ["C01"]


def test_history_file_replaces_entire_date_partition(tmp_path):
    path = tmp_path / "history.parquet"
    write_dataframe(
        pd.DataFrame(
            [
                {"date": "2026-07-09", "etf_code": "510050", "value": 1},
                {"date": "2026-07-09", "etf_code": "588999", "value": 1},
                {"date": "2026-07-08", "etf_code": "510050", "value": 1},
            ]
        ),
        path,
    )

    combined = upsert_history_file(
        path,
        pd.DataFrame([{"date": "2026-07-09", "etf_code": "510050", "value": 2}]),
        ["date", "etf_code"],
    )

    target = combined[combined["date"].eq("2026-07-09")]
    assert target[["etf_code", "value"]].to_dict("records") == [{"etf_code": "510050", "value": 2}]
    assert len(combined[combined["date"].eq("2026-07-08")]) == 1


def test_historical_universe_excludes_funds_listed_after_report_date():
    universe = pd.DataFrame(
        [
            {"etf_code": "510050", "is_a_share_equity_etf": True, "listing_date": "2005-02-23"},
            {"etf_code": "588999", "is_a_share_equity_etf": True, "listing_date": "2026-07-08"},
            {"etf_code": "159999", "is_a_share_equity_etf": True, "listing_date": None},
        ]
    )
    quotes = pd.DataFrame(
        [
            {"etf_code": "510050", "date": "2026-06-01"},
            {"etf_code": "159999", "date": "2026-07-09"},
        ]
    )

    active = _a_share_universe_as_of(universe, "2026-07-02", quotes)

    assert active["etf_code"].tolist() == ["510050"]


def test_normalize_date_accepts_pandas_missing_value():
    from etf_temperature.official_endpoints import normalize_date

    assert normalize_date(pd.NA) is None


def test_stale_nav_is_not_counted_as_fresh_coverage():
    daily = pd.DataFrame(
        [
            {
                "date": "2026-07-09",
                "etf_code": "510050",
                "close": 2.5,
                "latest_shares": 100,
                "nav": 2.4,
                "quote_data_date": "2026-07-09",
                "share_data_date": "2026-07-09",
                "nav_data_date": "2026-07-02",
            }
        ]
    )

    metrics = _field_freshness(daily)

    assert metrics["quote_fresh_coverage"] == 1.0
    assert metrics["share_fresh_coverage"] == 1.0
    assert metrics["nav_fresh_coverage"] == 0.0
    assert metrics["stale_nav_count"] == 1
    assert metrics["max_nav_lag_days"] == 7


def test_price_only_five_day_jump_is_not_auto_adjusted():
    daily = pd.DataFrame(
        [
            {
                "etf_code": "588170",
                "latest_shares": 310.0,
                "previous_shares": 309.0,
                "shares_5d_ago": 100.0,
                "share_daily_change": 1.0,
                "share_daily_change_pct": 1 / 309,
                "share_5d_change": 210.0,
                "share_5d_change_pct": 2.1,
                "close": 1.1,
                "prev_close": 1.1,
                "close_5d_ago": 3.3,
                "nav": 1.0,
            }
        ]
    )

    adjusted = apply_corporate_action_adjustments(daily).iloc[0]

    assert adjusted["corporate_action_status"] == "normal"
    assert adjusted["share_5d_change"] == 210.0


def test_quote_features_do_not_adjust_price_history_without_share_and_nav_confirmation():
    quotes = pd.DataFrame(
        [
            {"etf_code": "588170", "date": day, "close": close, "amount": 1.0, "data_date": day}
            for day, close in [
                ("2026-07-02", 3.706),
                ("2026-07-03", 3.579),
                ("2026-07-06", 1.192),
                ("2026-07-07", 1.232),
                ("2026-07-08", 1.271),
                ("2026-07-09", 1.397),
            ]
        ]
    )

    row = compute_quote_features(quotes, "2026-07-09").iloc[0]

    assert row["price_adjustment_factor_5d"] == 1.0
    assert round(row["close_5d_ago"], 6) == 3.706
    assert round(row["return_5d"], 6) == round(1.397 / 3.706 - 1, 6)


def test_stale_unresolved_extreme_flow_is_removed_from_aggregation_input():
    daily = pd.DataFrame(
        [
            {
                "date": "2026-07-09",
                "etf_code": "510050",
                "fund_size_100m": 100.0,
                "estimated_net_subscription_100m": 10.0,
                "estimated_net_subscription_5d_100m": 10.0,
                "share_daily_change_pct": 0.10,
                "share_5d_change_pct": 0.10,
                "close": 2.5,
                "latest_shares": 100.0,
                "nav": 2.4,
                "quote_data_date": "2026-07-09",
                "share_data_date": "2026-07-09",
                "nav_data_date": "2026-07-02",
            }
        ]
    )

    filtered, audit = _apply_flow_audit_status(daily)

    assert audit.iloc[0]["flow_validation_status"] == "unresolved"
    assert filtered.iloc[0]["raw_estimated_net_subscription_100m"] == 10.0
    assert pd.isna(filtered.iloc[0]["estimated_net_subscription_100m"])


def test_stale_non_extreme_flow_is_also_removed_from_aggregation_input():
    daily = pd.DataFrame(
        [
            {
                "date": "2026-07-09",
                "etf_code": "510050",
                "fund_size_100m": 100.0,
                "estimated_net_subscription_100m": 1.0,
                "estimated_net_subscription_5d_100m": 1.0,
                "share_daily_change_pct": 0.01,
                "share_5d_change_pct": 0.01,
                "close": 2.5,
                "latest_shares": 100.0,
                "nav": 2.4,
                "quote_data_date": "2026-07-09",
                "share_data_date": "2026-07-09",
                "nav_data_date": "2026-07-02",
            }
        ]
    )

    filtered, audit = _apply_flow_audit_status(daily)

    assert audit.empty
    assert filtered.iloc[0]["flow_validation_status"] == "stale_source_excluded"
    assert pd.isna(filtered.iloc[0]["estimated_net_subscription_100m"])


def test_broad_index_rules_do_not_swallow_unrelated_etfs():
    universe = pd.DataFrame(
        [
            {"etf_code": "159780", "exchange": "SZSE", "etf_name": "50ETF", "expanded_name": "50ETF", "tracking_index_code": "930780", "tracking_index_name": "", "is_a_share_equity_etf": True},
            {"etf_code": "512010", "exchange": "SSE", "etf_name": "A股ETF", "expanded_name": "A股ETF", "tracking_index_code": "930010", "tracking_index_name": "", "is_a_share_equity_etf": True},
            {"etf_code": "516110", "exchange": "SSE", "etf_name": "汽车ETF", "expanded_name": "汽车ETF", "tracking_index_code": "930997", "tracking_index_name": "汽车产业指数", "is_a_share_equity_etf": True},
            {"etf_code": "159901", "exchange": "SZSE", "etf_name": "深证100ETF", "expanded_name": "深证100ETF", "tracking_index_code": "399330", "tracking_index_name": "深证100", "is_a_share_equity_etf": True},
            {"etf_code": "159389", "exchange": "SZSE", "etf_name": "数字经济ETF", "expanded_name": "数字经济ETF", "tracking_index_code": "931529", "tracking_index_name": "", "is_a_share_equity_etf": True},
            {"etf_code": "159301", "exchange": "SZSE", "etf_name": "公用事业ETF", "expanded_name": "公用事业ETF", "tracking_index_code": "000995", "tracking_index_name": "公用事业指数", "is_a_share_equity_etf": True},
        ]
    )

    membership = classify_universe(universe).set_index("etf_code")

    assert membership.loc["159780", "primary_cluster"] != "C01"
    assert membership.loc["512010", "primary_cluster"] != "C02"
    assert membership.loc["516110", "primary_cluster"] == "C26"
    assert membership.loc["159901", "primary_cluster"] == "C02"
    assert membership.loc["159389", "primary_cluster"] == "C18"
    assert membership.loc["159301", "primary_cluster"] == "C25"


def test_cross_border_country_fund_is_excluded_before_a_share_hints():
    allowed, reason = classify_a_share_equity(
        pd.Series(
            {
                "etf_code": "159999",
                "exchange": "SZSE",
                "etf_name": "巴西ETF",
                "expanded_name": "巴西股票ETF",
                "tracking_index_name": "中证巴西指数",
            }
        )
    )

    assert not allowed
    assert reason == "excluded_keyword:巴西"


def test_stored_ratio_is_multiplied_when_rendered_as_percentage():
    assert _fmt_value(0.1554, "share_daily_change_pct") == "15.54%"
    assert _fmt_value(0.0312, "premium_discount") == "3.12%"


def test_rebuild_cli_returns_nonzero_when_any_day_is_preview(monkeypatch):
    from etf_temperature import cli

    monkeypatch.setattr(
        cli,
        "rebuild_history",
        lambda *args, **kwargs: [
            SimpleNamespace(report_date="2026-07-09", metrics={"formal_report_allowed": False})
        ],
    )

    assert cli.main(["rebuild", "--start", "2026-07-09", "--end", "2026-07-09", "--recompute"]) == 2
