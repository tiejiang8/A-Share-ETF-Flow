import pandas as pd

from etf_temperature.validation import _classification_metrics


def test_important_unclassified_uses_full_market_top_rank():
    rows = [{"etf_code": f"510{i:03d}", "primary_cluster": "C01", "fund_size_100m": 1000 - i, "amount": 1, "estimated_net_subscription_100m": 0, "estimated_net_subscription_5d_100m": 0, "etf_name": "已分类", "expanded_name": "", "tracking_index_name": ""} for i in range(210)]
    rows.append({"etf_code": "599999", "primary_cluster": "", "fund_size_100m": 1, "amount": 1, "estimated_net_subscription_100m": 0, "estimated_net_subscription_5d_100m": 0, "etf_name": "边缘产品", "expanded_name": "", "tracking_index_name": ""})
    metrics = _classification_metrics(pd.DataFrame(rows))
    assert metrics["important_unclassified_count"] == 0
