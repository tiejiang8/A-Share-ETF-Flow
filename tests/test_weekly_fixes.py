from __future__ import annotations

from datetime import date

import pandas as pd

from etf_temperature.classification import classify_universe, semantic_membership_issues
from etf_temperature.weekly import build_weekly_cluster, build_weekly_etf, validate_weekly_data
from etf_temperature.weekly_report import _fmt_money, generate_weekly_report


def test_star_market_growth_is_not_classified_as_chinext():
    universe = pd.DataFrame([
        {
            "etf_code": "588110",
            "etf_name": "科创成长",
            "expanded_name": "科创成长ETF广发",
            "exchange": "SSE",
            "tracking_index_code": "000690",
            "tracking_index_name": "上证科创板成长指数",
            "is_a_share_equity_etf": True,
        }
    ])
    membership = classify_universe(universe)
    row = membership.iloc[0]
    assert row["primary_cluster"] == "C10"
    assert row["primary_cluster_name"] == "科创100/科创成长"
    assert semantic_membership_issues(membership).empty


def test_star_chinext_products_require_explicit_index_semantics():
    universe = pd.DataFrame(
        [
            {
                "etf_code": "159781",
                "etf_name": "科创创业ETF易方达",
                "expanded_name": "科创创业ETF易方达",
                "exchange": "SZSE",
                "tracking_index_code": "931643",
                "tracking_index_name": "中证科创创业50指数",
                "is_a_share_equity_etf": True,
            },
            {
                "etf_code": "159915",
                "etf_name": "创业板ETF",
                "expanded_name": "创业板ETF",
                "exchange": "SZSE",
                "tracking_index_code": "399006",
                "tracking_index_name": "创业板指数",
                "is_a_share_equity_etf": True,
            },
            {
                "etf_code": "159949",
                "etf_name": "创业板50ETF",
                "expanded_name": "创业板50ETF",
                "exchange": "SZSE",
                "tracking_index_code": "399673",
                "tracking_index_name": "创业板50指数",
                "is_a_share_equity_etf": True,
            },
            {
                "etf_code": "560001",
                "etf_name": "成长ETF",
                "expanded_name": "成长ETF",
                "exchange": "SSE",
                "tracking_index_code": "999999",
                "tracking_index_name": "示例成长指数",
                "is_a_share_equity_etf": True,
            },
            {
                "etf_code": "159363",
                "etf_name": "科创创业人工智能ETF",
                "expanded_name": "科创创业人工智能ETF",
                "exchange": "SZSE",
                "tracking_index_code": "932456",
                "tracking_index_name": "中证科创创业人工智能指数",
                "is_a_share_equity_etf": True,
            },
        ]
    )
    membership = classify_universe(universe).set_index("etf_code")
    assert membership.loc["159781", "primary_cluster"] == "C10"
    assert membership.loc["159781", "primary_cluster_name"] == "科创100/科创成长"
    assert str(membership.loc["159781", "classification_basis"]) == "index_code:931643"
    assert membership.loc["159915", "primary_cluster"] == "C07"
    assert membership.loc["159949", "primary_cluster"] == "C08"
    assert membership.loc["560001", "primary_cluster"] != "C10"
    assert membership.loc["159363", "primary_cluster"] == "C17"


def test_weekly_etf_uses_current_membership_not_stale_history_cluster():
    history = pd.DataFrame([
        {"date": "2026-06-26", "etf_code": "588110", "etf_name": "科创成长", "primary_cluster": "C07", "primary_cluster_name": "创业板", "open": 1, "high": 1, "low": 1, "close": 1, "amount": 1, "latest_shares": 100, "fund_size_100m": 1, "estimated_net_subscription_100m": 0, "temperature": 50},
        {"date": "2026-06-29", "etf_code": "588110", "etf_name": "科创成长", "primary_cluster": "C07", "primary_cluster_name": "创业板", "open": 1, "high": 1, "low": 1, "close": 1, "amount": 1, "latest_shares": 100, "fund_size_100m": 1, "estimated_net_subscription_100m": 0, "temperature": 50},
        {"date": "2026-06-30", "etf_code": "588110", "etf_name": "科创成长", "primary_cluster": "C07", "primary_cluster_name": "创业板", "open": 1, "high": 1, "low": 1, "close": 1, "amount": 1, "latest_shares": 100, "fund_size_100m": 1, "estimated_net_subscription_100m": 0, "temperature": 50},
        {"date": "2026-07-01", "etf_code": "588110", "etf_name": "科创成长", "primary_cluster": "C07", "primary_cluster_name": "创业板", "open": 1, "high": 1, "low": 1, "close": 1, "amount": 1, "latest_shares": 100, "fund_size_100m": 1, "estimated_net_subscription_100m": 0, "temperature": 50},
        {"date": "2026-07-02", "etf_code": "588110", "etf_name": "科创成长", "primary_cluster": "C07", "primary_cluster_name": "创业板", "open": 1, "high": 1, "low": 1, "close": 1, "amount": 1, "latest_shares": 100, "fund_size_100m": 1, "estimated_net_subscription_100m": 0, "temperature": 50},
        {"date": "2026-07-03", "etf_code": "588110", "etf_name": "科创成长", "primary_cluster": "C07", "primary_cluster_name": "创业板", "open": 1, "high": 1, "low": 1, "close": 1, "amount": 1, "latest_shares": 100, "fund_size_100m": 1, "estimated_net_subscription_100m": 0, "temperature": 50},
    ])
    membership = pd.DataFrame([{"etf_code": "588110", "primary_cluster": "C10", "primary_cluster_name": "科创100/科创成长"}])
    weekly = build_weekly_etf("2026-W27", history, membership)
    assert weekly.iloc[0]["primary_cluster"] == "C10"
    assert weekly.iloc[0]["week_start"] == "2026-06-29"
    assert weekly.iloc[0]["week_end"] == "2026-07-05"
    assert weekly.iloc[0]["week_first_trading_day"] == "2026-06-29"
    assert weekly.iloc[0]["week_last_trading_day"] == "2026-07-03"
    assert "flow_prev4_daily_avg" in weekly.columns
    assert "flow_acceleration_vs_prev4" in weekly.columns
    assert weekly.iloc[0]["temperature_level"] == "中性"
    assert weekly.iloc[0]["temperature_stage"] in {"中温转强", "中温走弱", "剧烈分化"}


def test_weekly_validation_includes_week_date_range():
    weekly_etf = pd.DataFrame([
        {
            "week": "2026-W27",
            "etf_code": "510050",
            "primary_cluster": "C01",
            "trading_days": 5,
            "week_first_trading_day": "2026-06-29",
            "week_last_trading_day": "2026-07-03",
            "week_close": 1.0,
            "week_end_shares": 100,
            "weekly_net_subscription_100m": 1.0,
        }
    ])
    weekly_cluster = pd.DataFrame([{"week": "2026-W27", "cluster_id": "C01"}])

    result = validate_weekly_data("2026-W27", weekly_etf, weekly_cluster, today=date(2026, 7, 6))

    assert result["week_start"] == "2026-06-29"
    assert result["week_end"] == "2026-07-05"
    assert result["week_first_trading_day"] == "2026-06-29"
    assert result["week_last_trading_day"] == "2026-07-03"


def test_missing_money_does_not_render_dash_yi():
    assert _fmt_money(float("nan")) == "-"


def test_cluster_return_consistency_uses_percentage_point_units():
    weekly_etf = pd.DataFrame([
        {
            "etf_code": "510050",
            "etf_name": "50ETF",
            "primary_cluster": "C01",
            "week_end_size_100m": 100.0,
            "weekly_pct_change": 3.21,
            "weekly_net_subscription_100m": 1.0,
            "weekly_amount_100m": 1.0,
            "weekly_amount_vs_4w": 1.0,
            "weekly_share_change": 1.0,
            "week_end_temperature": 50.0,
            "weekly_avg_temperature": 50.0,
            "first_temperature": 49.0,
            "week_max_temperature": 51.0,
            "hot_days": 0,
            "flow_positive_days": 5,
            "flow_negative_days": 0,
            "weekly_temperature_change": 1.0,
            "corporate_action_days": 0,
            "corporate_action_suspect_days": 0,
        },
        {
            "etf_code": "510100",
            "etf_name": "SZ50ETF",
            "primary_cluster": "C01",
            "week_end_size_100m": 100.0,
            "weekly_pct_change": 2.99,
            "weekly_net_subscription_100m": 1.0,
            "weekly_amount_100m": 1.0,
            "weekly_amount_vs_4w": 1.0,
            "weekly_share_change": 1.0,
            "week_end_temperature": 50.0,
            "weekly_avg_temperature": 50.0,
            "first_temperature": 49.0,
            "week_max_temperature": 51.0,
            "hot_days": 0,
            "flow_positive_days": 5,
            "flow_negative_days": 0,
            "weekly_temperature_change": 1.0,
            "corporate_action_days": 0,
            "corporate_action_suspect_days": 0,
        },
    ])

    cluster = build_weekly_cluster("2026-W28", weekly_etf)
    row = cluster[cluster["cluster_id"].eq("C01")].iloc[0]
    assert round(row["weighted_pct_change"], 2) == 3.10
    assert bool(row["cluster_return_valid"])
    assert "flow_prev4_daily_avg" in cluster.columns
    assert "flow_acceleration_vs_prev4" in cluster.columns
    assert "temperature_level" in cluster.columns
    assert "temperature_stage" in cluster.columns
