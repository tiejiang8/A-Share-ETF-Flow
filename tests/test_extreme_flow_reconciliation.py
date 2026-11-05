import pandas as pd

from etf_temperature.audit import audit_extreme_flows
from etf_temperature.pipeline import _apply_flow_audit_status


def test_extreme_flow_reconciliation_statuses():
    df = pd.DataFrame(
        [
            {"date": "2026-07-02", "etf_code": "510050", "etf_name": "50ETF", "primary_cluster": "C01", "primary_cluster_name": "上证50", "fund_size_100m": 100, "estimated_net_subscription_100m": 8, "estimated_net_subscription_5d_100m": 0, "share_daily_change_pct": 0.01, "share_5d_change_pct": 0.01, "close": 1, "latest_shares": 1, "nav": 1, "share_flow_excluded": False},
            {"date": "2026-07-02", "etf_code": "512480", "etf_name": "半导体", "primary_cluster": "C16", "primary_cluster_name": "半导体/芯片", "fund_size_100m": 100, "estimated_net_subscription_100m": None, "estimated_net_subscription_5d_100m": None, "share_daily_change_pct": 1.0, "share_5d_change_pct": 1.0, "close": 1, "latest_shares": 1, "nav": 1, "share_flow_excluded": True},
        ]
    )
    df.loc[0, ["quote_data_date", "share_data_date", "nav_data_date"]] = "2026-07-02"
    out = audit_extreme_flows(df)
    statuses = set(out["flow_validation_status"])
    assert "resolved_by_source_reconciliation" in statuses
    assert "excluded_corporate_action" in statuses


def test_extreme_flow_reconciliation_accepts_dated_official_suspension():
    df = pd.DataFrame(
        [
            {
                "date": "2026-07-30",
                "etf_code": "159515",
                "etf_name": "国企红利ETF鹏扬",
                "primary_cluster": "C11",
                "primary_cluster_name": "红利",
                "fund_size_100m": 0.54,
                "estimated_net_subscription_100m": 0.011,
                "estimated_net_subscription_5d_100m": 0.036,
                "share_daily_change_pct": 0.02,
                "share_5d_change_pct": 0.07,
                "close": None,
                "latest_shares": 44_786_600,
                "nav": 1.2122,
                "quote_data_date": None,
                "share_data_date": "2026-07-30",
                "nav_data_date": "2026-07-30",
                "share_flow_excluded": False,
            }
        ]
    )

    out = audit_extreme_flows(df)

    assert out.iloc[0]["flow_validation_status"] == "resolved_official_suspension"
    assert "disc.static.szse.cn" in out.iloc[0]["suspension_source_url"]


def test_zero_share_change_is_resolved_despite_lagging_nav():
    """当日份额无变化时净申购恒为 0，净值晚一天不应判为未解决。"""

    df = pd.DataFrame(
        [
            {
                "date": "2026-09-09",
                "etf_code": "512390",
                "etf_name": "MSCI低波",
                "primary_cluster": "C99",
                "primary_cluster_name": "其他",
                "fund_size_100m": 0.118,
                "estimated_net_subscription_100m": 0.0,
                "estimated_net_subscription_5d_100m": -0.037,
                "share_daily_change": 0.0,
                "share_daily_change_pct": 0.0,
                "share_5d_change_pct": -0.2366,
                "close": 1.227,
                "latest_shares": 9_682_000,
                "previous_shares": 9_682_000,
                "nav": 1.2208,
                "quote_data_date": "2026-09-09",
                "share_data_date": "2026-09-09",
                "nav_data_date": "2026-09-08",
                "share_flow_excluded": False,
            }
        ]
    )

    out = audit_extreme_flows(df)

    assert out.iloc[0]["flow_validation_status"] == "resolved_zero_share_change"
    assert "恒等式" in out.iloc[0]["reconciliation_note"]


def test_zero_share_change_without_quote_bar_is_resolved():
    """当日无成交（行情源无 K 线）且份额零变动时，净申购恒为 0，不应判为未解决。"""

    df = pd.DataFrame(
        [
            {
                "date": "2026-10-19",
                "etf_code": "159291",
                "etf_name": "创业板综增强ETF招商",
                "primary_cluster": "C05",
                "primary_cluster_name": "创业板",
                "fund_size_100m": 0.174,
                "estimated_net_subscription_100m": 0.0,
                "estimated_net_subscription_5d_100m": -0.019984,
                "share_daily_change": 0.0,
                "share_daily_change_pct": 0.0,
                "share_5d_change_pct": -0.1028,
                "close": None,
                "latest_shares": 17_457_388,
                "previous_shares": 17_457_388,
                "nav": 0.9992,
                "quote_data_date": None,
                "share_data_date": "2026-10-19",
                "nav_data_date": "2026-10-19",
                "share_flow_excluded": False,
            }
        ]
    )

    out = audit_extreme_flows(df)

    assert out.iloc[0]["flow_validation_status"] == "resolved_zero_share_change"
    assert "无成交行情" in out.iloc[0]["reconciliation_note"]


def test_zero_share_change_without_quote_bar_keeps_flow_for_aggregation():
    df = pd.DataFrame(
        [
            {
                "date": "2026-10-19",
                "etf_code": "159291",
                "fund_size_100m": 0.174,
                "estimated_net_subscription_100m": 0.0,
                "estimated_net_subscription_5d_100m": -0.019984,
                "share_daily_change": 0.0,
                "share_daily_change_pct": 0.0,
                "share_5d_change_pct": -0.1028,
                "close": None,
                "latest_shares": 17_457_388,
                "nav": 0.9992,
                "quote_data_date": None,
                "share_data_date": "2026-10-19",
                "nav_data_date": "2026-10-19",
                "share_flow_excluded": False,
            }
        ]
    )

    out, _ = _apply_flow_audit_status(df)

    assert out.iloc[0]["flow_validation_status"] == "resolved_zero_share_change"
    assert out.iloc[0]["estimated_net_subscription_100m"] == 0.0
    assert out.iloc[0]["estimated_net_subscription_5d_100m"] == -0.019984


def test_dormant_fund_with_frozen_nav_is_not_rescued():
    """净值长期不更新属终止/休眠标的，不是发布滞后，不能靠份额零变动的恒等式救援。"""

    df = pd.DataFrame(
        [
            {
                "date": "2026-08-31",
                "etf_code": "560650",
                "etf_name": "核心50",
                "primary_cluster": "C99",
                "primary_cluster_name": "其他",
                "fund_size_100m": 0.0846,
                "estimated_net_subscription_100m": 0.0,
                "estimated_net_subscription_5d_100m": -0.0846,
                "share_daily_change": 0.0,
                "share_daily_change_pct": 0.0,
                "share_5d_change_pct": -1.0,
                "close": 1.068,
                "latest_shares": 0.0,
                "previous_shares": 0.0,
                "nav": 1.0407,
                "quote_data_date": "2026-08-31",
                "share_data_date": "2026-08-31",
                "nav_data_date": "2026-07-02",
                "share_flow_excluded": False,
            }
        ]
    )

    out = audit_extreme_flows(df)

    assert out.iloc[0]["flow_validation_status"] == "unresolved"


def test_nonzero_share_change_with_lagging_nav_stays_unresolved():
    """份额确有新变化时，净值晚一天仍然无法对账，必须保持未解决。"""

    df = pd.DataFrame(
        [
            {
                "date": "2026-09-09",
                "etf_code": "510300",
                "etf_name": "300ETF",
                "primary_cluster": "C01",
                "primary_cluster_name": "大盘宽基",
                "fund_size_100m": 100.0,
                "estimated_net_subscription_100m": -13.15,
                "estimated_net_subscription_5d_100m": 15.2,
                "share_daily_change": -100_000_000,
                "share_daily_change_pct": -0.0121,
                "share_5d_change_pct": 0.01,
                "close": 4.0,
                "latest_shares": 8_000_000_000,
                "previous_shares": 8_100_000_000,
                "nav": 4.0,
                "quote_data_date": "2026-09-09",
                "share_data_date": "2026-09-09",
                "nav_data_date": "2026-09-08",
                "share_flow_excluded": False,
            }
        ]
    )

    out = audit_extreme_flows(df)

    assert out.iloc[0]["flow_validation_status"] == "unresolved"


def test_official_suspension_keeps_share_nav_flow_for_aggregation():
    df = pd.DataFrame(
        [
            {
                "date": "2026-07-30",
                "etf_code": "159515",
                "fund_size_100m": 0.54,
                "estimated_net_subscription_100m": 0.011,
                "estimated_net_subscription_5d_100m": 0.036,
                "share_daily_change_pct": 0.02,
                "share_5d_change_pct": 0.07,
                "close": None,
                "latest_shares": 44_786_600,
                "nav": 1.2122,
                "quote_data_date": None,
                "share_data_date": "2026-07-30",
                "nav_data_date": "2026-07-30",
                "share_flow_excluded": False,
            }
        ]
    )

    out, _ = _apply_flow_audit_status(df)

    assert out.iloc[0]["flow_validation_status"] == "resolved_official_suspension"
    assert out.iloc[0]["estimated_net_subscription_100m"] == 0.011
    assert out.iloc[0]["estimated_net_subscription_5d_100m"] == 0.036
