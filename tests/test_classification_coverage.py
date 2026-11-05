import pandas as pd

from etf_temperature.validation import _classification_metrics


def test_classification_coverage_weights_scale_turnover_and_flow():
    df = pd.DataFrame(
        [
            {"etf_code": "510050", "primary_cluster": "C01", "fund_size_100m": 95, "amount": 90, "estimated_net_subscription_100m": 9, "estimated_net_subscription_5d_100m": 0, "etf_name": "", "expanded_name": "", "tracking_index_name": ""},
            {"etf_code": "599999", "primary_cluster": "", "fund_size_100m": 5, "amount": 10, "estimated_net_subscription_100m": 1, "estimated_net_subscription_5d_100m": 0, "etf_name": "", "expanded_name": "", "tracking_index_name": ""},
        ]
    )
    metrics = _classification_metrics(df)
    assert metrics["classified_scale_coverage"] == 0.95
    assert metrics["classified_turnover_coverage"] == 0.9
    assert metrics["classified_flow_coverage"] == 0.9
