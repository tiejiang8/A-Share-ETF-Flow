import pandas as pd

from etf_temperature.classification import classify_universe


def test_deep_szse_core_anchors_assign_to_expected_clusters():
    universe = pd.DataFrame(
        [
            {"etf_code": "159915", "exchange": "SZSE", "etf_name": "创业板ETF易方达", "expanded_name": "", "tracking_index_code": "399006", "tracking_index_name": "创业板指数", "is_a_share_equity_etf": True},
            {"etf_code": "159949", "exchange": "SZSE", "etf_name": "创业板50ETF华安", "expanded_name": "", "tracking_index_code": "399673", "tracking_index_name": "创业板50指数", "is_a_share_equity_etf": True},
            {"etf_code": "510050", "exchange": "SSE", "etf_name": "50ETF", "expanded_name": "", "tracking_index_code": "000016", "tracking_index_name": "上证50指数", "is_a_share_equity_etf": True},
        ]
    )
    by_code = classify_universe(universe).set_index("etf_code")
    assert by_code.loc["159915", "primary_cluster"] == "C07"
    assert by_code.loc["159949", "primary_cluster"] == "C08"
    assert by_code.loc["510050", "primary_cluster"] == "C01"
