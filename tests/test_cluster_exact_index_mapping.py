import pandas as pd

from etf_temperature.classification import classify_universe


def test_exact_and_exclusion_rules_prevent_keyword_misclassification():
    universe = pd.DataFrame(
        [
            {"etf_code": "512070", "exchange": "SSE", "etf_name": "证券保险", "expanded_name": "", "tracking_index_code": "H30035", "tracking_index_name": "沪深300非银行金融指数", "is_a_share_equity_etf": True},
            {"etf_code": "589000", "exchange": "SSE", "etf_name": "科创综指", "expanded_name": "", "tracking_index_code": "000680", "tracking_index_name": "上证科创板综合指数", "is_a_share_equity_etf": True},
            {"etf_code": "560888", "exchange": "SSE", "etf_name": "消费电子ETF", "expanded_name": "", "tracking_index_code": "930999", "tracking_index_name": "消费电子指数", "is_a_share_equity_etf": True},
            {"etf_code": "159939", "exchange": "SZSE", "etf_name": "信息技术ETF广发", "expanded_name": "信息技术ETF广发", "tracking_index_code": "", "tracking_index_name": "", "is_a_share_equity_etf": True},
            {"etf_code": "562910", "exchange": "SSE", "etf_name": "高端制造", "expanded_name": "高端制造ETF易方达", "tracking_index_code": "", "tracking_index_name": "中证装备产业指数", "is_a_share_equity_etf": True},
            {"etf_code": "159903", "exchange": "SZSE", "etf_name": "深成ETF南方", "expanded_name": "深成ETF南方", "tracking_index_code": "399001", "tracking_index_name": "深证成指", "is_a_share_equity_etf": True},
            {"etf_code": "159943", "exchange": "SZSE", "etf_name": "深证成指ETF大成", "expanded_name": "深证成指ETF大成", "tracking_index_code": "399001", "tracking_index_name": "深证成指", "is_a_share_equity_etf": True},
            {"etf_code": "159565", "exchange": "SZSE", "etf_name": "汽车零部件ETF易方达", "expanded_name": "汽车零部件ETF易方达", "tracking_index_code": "931230", "tracking_index_name": "", "is_a_share_equity_etf": True},
            {"etf_code": "159613", "exchange": "SZSE", "etf_name": "信息安全ETF嘉实", "expanded_name": "信息安全ETF嘉实", "tracking_index_code": "399994", "tracking_index_name": "信息安全", "is_a_share_equity_etf": True},
            {"etf_code": "159814", "exchange": "SZSE", "etf_name": "创业大盘ETF西部利得", "expanded_name": "创业大盘ETF西部利得", "tracking_index_code": "399293", "tracking_index_name": "创业大盘", "is_a_share_equity_etf": True},
            {"etf_code": "512190", "exchange": "SSE", "etf_name": "之江凤凰", "expanded_name": "浙商之江凤凰ETF", "tracking_index_code": "930758", "tracking_index_name": "中证浙江凤凰行动50指数", "is_a_share_equity_etf": True},
        ]
    )
    by_code = classify_universe(universe).set_index("etf_code")
    assert by_code.loc["512070", "primary_cluster"] == "C14"
    assert by_code.loc["589000", "primary_cluster"] != "C09"
    assert by_code.loc["560888", "primary_cluster"] != "C21"
    assert by_code.loc["159939", "primary_cluster"] == "C18"
    assert by_code.loc["562910", "primary_cluster"] == "C20"
    assert by_code.loc["159903", "primary_cluster"] == "C02"
    assert by_code.loc["159943", "primary_cluster"] == "C02"
    assert by_code.loc["159565", "primary_cluster"] == "C26"
    assert by_code.loc["159613", "primary_cluster"] == "C18"
    assert by_code.loc["159814", "primary_cluster"] == "C07"
    assert by_code.loc["512190", "primary_cluster"] == "C02"
