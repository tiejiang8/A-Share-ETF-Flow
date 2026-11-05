import pandas as pd

from etf_temperature.audit import audit_cluster_concentration


def test_single_etf_cluster_flow_concentration_triggers():
    df = pd.DataFrame(
        [
            {"etf_code": "510300", "etf_name": "300ETF", "primary_cluster": "C02", "primary_cluster_name": "沪深300", "estimated_net_subscription_100m": 9, "estimated_net_subscription_5d_100m": 0},
            {"etf_code": "159919", "etf_name": "300ETF", "primary_cluster": "C02", "primary_cluster_name": "沪深300", "estimated_net_subscription_100m": 1, "estimated_net_subscription_5d_100m": 0},
        ]
    )
    out = audit_cluster_concentration(df)
    row = out[out["metric"].eq("estimated_net_subscription_100m")].iloc[0]
    assert row["max_contributor_abs_ratio"] == 0.9
    assert row["flow_validation_status"] == "triggered"
