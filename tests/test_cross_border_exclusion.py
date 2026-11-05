import pandas as pd

from etf_temperature.universe import build_universe


def test_gold_cross_border_bond_money_are_excluded():
    universe = build_universe(
        pd.DataFrame(
            [
                {"etf_code": "513310", "etf_name": "中韩芯片ETF", "exchange": "SSE", "etf_type": "stock_etf", "tracking_index_name": "中证韩交所中韩半导体指数"},
                {"etf_code": "518600", "etf_name": "上海金ETF", "exchange": "SSE", "etf_type": "stock_etf", "tracking_index_name": "上海金"},
                {"etf_code": "511880", "etf_name": "银华日利", "exchange": "SSE", "etf_type": "stock_etf", "tracking_index_name": "货币"},
            ]
        ),
        pd.DataFrame(),
    )
    assert universe["is_a_share_equity_etf"].sum() == 0
