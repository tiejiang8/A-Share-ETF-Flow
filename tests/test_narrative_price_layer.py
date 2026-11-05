import pandas as pd

from etf_temperature.narrative import price_layer


def test_price_layer_identifies_defensive_outperformance():
    df = pd.DataFrame(
        [
            {"cluster_id": "C11", "cluster_name": "红利", "weighted_pct_change": 1, "total_size_100m": 100},
            {"cluster_id": "C16", "cluster_name": "半导体", "weighted_pct_change": -5, "total_size_100m": 100},
            {"cluster_id": "C01", "cluster_name": "上证50", "weighted_pct_change": 0, "total_size_100m": 100},
            {"cluster_id": "C05", "cluster_name": "中证1000", "weighted_pct_change": -1, "total_size_100m": 100},
        ]
    )
    text = price_layer(df)
    assert "红利防御相对占优" in text
    assert "价格偏科技成长" not in text
