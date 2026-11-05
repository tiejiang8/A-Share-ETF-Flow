from __future__ import annotations

from datetime import date

import pandas as pd

from etf_temperature.classification import classify_universe
from etf_temperature.classification_quality import classify_unclassified_importance
from etf_temperature.indicators import build_etf_daily
from etf_temperature.temperature import cluster_temperature_from_raw
from etf_temperature.weekly import validate_weekly_data


def test_theme_boundaries_do_not_mix_new_energy_utilities_and_tourism():
    universe = pd.DataFrame(
        [
            {"etf_code": "515700", "etf_name": "新能车", "expanded_name": "新能车ETF", "tracking_index_name": "中证新能源汽车产业指数", "tracking_index_code": "", "exchange": "SSE", "is_a_share_equity_etf": True},
            {"etf_code": "562050", "etf_name": "药ETF", "expanded_name": "制药ETF", "tracking_index_name": "中证制药指数", "tracking_index_code": "", "exchange": "SSE", "is_a_share_equity_etf": True},
            {"etf_code": "562510", "etf_name": "旅游ETF", "expanded_name": "旅游ETF", "tracking_index_name": "中证旅游主题指数", "tracking_index_code": "", "exchange": "SSE", "is_a_share_equity_etf": True},
            {"etf_code": "512140", "etf_name": "电力事业", "expanded_name": "电力事业ETF", "tracking_index_name": "中证全指电力公用事业指数", "tracking_index_code": "", "exchange": "SSE", "is_a_share_equity_etf": True},
            {"etf_code": "515790", "etf_name": "光伏ETF", "expanded_name": "光伏ETF", "tracking_index_name": "中证光伏产业指数", "tracking_index_code": "", "exchange": "SSE", "is_a_share_equity_etf": True},
            {"etf_code": "516160", "etf_name": "新能源", "expanded_name": "新能源ETF", "tracking_index_name": "中证新能源指数", "tracking_index_code": "", "exchange": "SSE", "is_a_share_equity_etf": True},
            {"etf_code": "510230", "etf_name": "金融ETF", "expanded_name": "金融ETF国泰", "tracking_index_name": "上证180金融股指数", "tracking_index_code": "", "exchange": "SSE", "is_a_share_equity_etf": True},
        ]
    )

    membership = classify_universe(universe).set_index("etf_code")

    assert membership.loc["515700", "primary_cluster"] == "C26"
    assert membership.loc["562050", "primary_cluster"] == "C23"
    assert membership.loc["562510", "primary_cluster"] == "C34"
    assert membership.loc["512140", "primary_cluster"] == "C32"
    assert membership.loc["515790", "primary_cluster"] == "C25"
    assert membership.loc["516160", "primary_cluster"] == "C35"
    assert membership.loc["510230", "primary_cluster"] == "C36"


def test_important_unclassified_uses_materiality_not_a_fixed_top200():
    rows = [
        {
            "etf_code": f"51{i:04d}",
            "etf_name": f"宽基ETF{i}",
            "expanded_name": f"宽基ETF{i}",
            "tracking_index_name": "普通指数",
            "primary_cluster": "C01",
            "fund_size_100m": 1_000 - i,
            "amount": 1_000_000_000 - i,
            "estimated_net_subscription_100m": 1.0,
            "estimated_net_subscription_5d_100m": 1.0,
        }
        for i in range(999)
    ]
    rows.append(
        {
            "etf_code": "599999",
            "etf_name": "普通小盘ETF",
            "expanded_name": "普通小盘ETF",
            "tracking_index_name": "普通指数",
            "primary_cluster": "",
            "fund_size_100m": 0.5,
            "amount": 0.0,
            "estimated_net_subscription_100m": 0.0,
            "estimated_net_subscription_5d_100m": 0.0,
        }
    )
    frame = pd.DataFrame(rows)

    result = classify_unclassified_importance(frame).iloc[0]
    assert result["unclassified_bucket"] != "重要未分类"

    frame.loc[frame["etf_code"].eq("599999"), "etf_name"] = "新能源ETF"
    result = classify_unclassified_importance(frame).iloc[0]
    assert result["unclassified_bucket"] == "重要未分类"


def test_low_flow_coverage_shrinks_cluster_temperature_toward_neutral():
    base = {
        "cluster_id": "C01",
        "cluster_name": "上证50",
        "total_size_100m": 100.0,
        "estimated_net_subscription_100m": 3.0,
        "estimated_net_subscription_5d_100m": 8.0,
        "weighted_pct_change": 5.0,
        "weighted_return_5d": 0.08,
        "amount_vs_5d": 2.0,
        "share_increase_member_ratio": 1.0,
        "median_premium_discount": 0.0,
    }
    raw = pd.DataFrame(
        [
            {**base, "one_day_flow_size_coverage": 1.0, "five_day_flow_size_coverage": 1.0},
            {**base, "one_day_flow_size_coverage": 0.25, "five_day_flow_size_coverage": 0.25},
        ]
    )

    result = cluster_temperature_from_raw(raw)
    full = result.iloc[0]
    partial = result.iloc[1]

    assert abs(partial["cluster_temperature"] - 50) < abs(full["cluster_temperature"] - 50)
    assert partial["cluster_temperature_confidence"] < full["cluster_temperature_confidence"]


def test_creation_redemption_unit_is_propagated_as_auxiliary_flow_check():
    universe = pd.DataFrame(
        [
            {
                "etf_code": "510050",
                "etf_name": "50ETF",
                "expanded_name": "上证50ETF",
                "tracking_index_name": "上证50指数",
                "tracking_index_code": "000016",
                "exchange": "SSE",
                "is_a_share_equity_etf": True,
            }
        ]
    )
    membership = pd.DataFrame(
        [
            {
                "etf_code": "510050",
                "primary_cluster": "C01",
                "primary_cluster_name": "上证50",
                "secondary_tags": "",
                "classification_basis": "index_code:000016",
                "confidence": 1.0,
            }
        ]
    )
    quotes = pd.DataFrame(
        [
            {"etf_code": "510050", "date": "2026-07-01", "close": 1.0, "open": 1.0, "high": 1.0, "low": 1.0, "amount": 1_000_000, "volume": 1_000},
            {"etf_code": "510050", "date": "2026-07-02", "close": 1.0, "open": 1.0, "high": 1.0, "low": 1.0, "amount": 1_000_000, "volume": 1_000},
        ]
    )
    shares = pd.DataFrame(
        [
            {"etf_code": "510050", "date": "2026-07-01", "shares": 1_000_000},
            {"etf_code": "510050", "date": "2026-07-02", "shares": 1_100_000},
        ]
    )
    nav = pd.DataFrame(
        [
            {"etf_code": "510050", "date": "2026-07-02", "data_date": "2026-07-02", "nav": 1.0, "creation_redemption_unit": 100_000, "nav_per_creation_unit": 100_000, "source": "sse_official:申购赎回清单"}
        ]
    )

    row = build_etf_daily(universe, membership, quotes, shares, nav, "2026-07-02").iloc[0]

    assert row["creation_redemption_unit"] == 100_000
    assert row["estimated_creation_redemption_units_1d"] == 1.0
    assert row["creation_unit_rounding_gap"] == 0.0
    assert row["creation_unit_source"] == "sse_official:申购赎回清单"


def test_weekly_weighted_flow_gap_blocks_when_count_coverage_still_passes(monkeypatch):
    import etf_temperature.weekly as weekly_module

    codes = [f"51{i:04d}" for i in range(20)]
    universe = pd.DataFrame(
        [{"etf_code": code, "is_a_share_equity_etf": True} for code in codes]
    )
    membership = pd.DataFrame(
        [
            {
                "etf_code": code,
                "etf_name": f"50ETF{i}",
                "expanded_name": f"上证50ETF{i}",
                "tracking_index_code": "000016",
                "tracking_index_name": "上证50指数",
                "primary_cluster": "C01",
                "primary_cluster_name": "上证50",
                "classification_basis": "index_code:000016",
                "source": "config/cluster_rules.yaml",
            }
            for i, code in enumerate(codes)
        ]
    )

    def fake_read(path):
        return membership if str(path).endswith("etf_cluster_membership.parquet") else universe

    monkeypatch.setattr(weekly_module, "read_dataframe", fake_read)
    monkeypatch.setattr(
        weekly_module,
        "load_core_anchors",
        lambda: {"C01": {"required_codes": [codes[0]]}},
    )
    weekly_etf = pd.DataFrame(
        [
            {
                "week": "2026-W27",
                "etf_code": code,
                "etf_name": f"50ETF{i}",
                "primary_cluster": "C01",
                "trading_days": 5,
                "share_days": 5,
                "flow_days": 0 if i == 19 else 5,
                "week_last_trading_day": "2026-07-03",
                "week_close": 1.0,
                "week_end_shares": 100,
                "week_end_size_100m": 981.0 if i == 19 else 1.0,
                "weekly_amount_100m": 1.0,
                "weekly_net_subscription_100m": float("nan") if i == 19 else 1.0,
            }
            for i, code in enumerate(codes)
        ]
    )
    weekly_cluster = pd.DataFrame(
        [
            {
                "week": "2026-W27",
                "cluster_id": f"C{i:02d}",
                "cluster_name": f"集群{i:02d}",
                "member_count": 20 if i == 1 else 0,
                "total_size_100m": 1_000.0 if i == 1 else 0.0,
                "flow_size_coverage": 0.019 if i == 1 else 0.0,
            }
            for i in range(1, 37)
        ]
    )

    validation = validate_weekly_data(
        "2026-W27", weekly_etf, weekly_cluster, today=date(2026, 7, 4)
    )

    assert validation["weekly_flow_coverage"] == 0.95
    assert validation["weekly_flow_size_coverage"] == 0.019
    assert "资金流只数覆盖率不足" not in validation["formal_blockers"]
    assert "资金流有效规模覆盖率不足" in validation["formal_blockers"]
    assert "重要集群资金流有效规模覆盖率不足" in validation["formal_blockers"]
