import pandas as pd

from etf_temperature.narrative import flow_layer


def test_flow_layer_distinguishes_reverse_subscription_from_turnover():
    df = pd.DataFrame(
        [
            {"cluster_id": "C16", "cluster_name": "半导体", "weighted_pct_change": -8, "estimated_net_subscription_100m": 20, "total_size_100m": 100},
            {"cluster_id": "C11", "cluster_name": "红利", "weighted_pct_change": 1, "estimated_net_subscription_100m": 5, "total_size_100m": 100},
        ]
    )
    text = flow_layer(df)
    assert "逆向申购" in text
    assert "成交额显示资金流入" not in text
