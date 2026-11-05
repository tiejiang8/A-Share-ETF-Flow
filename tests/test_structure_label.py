from etf_temperature.aggregation import judge_cluster


def test_structure_label_uses_price_flow_and_turnover():
    assert judge_cluster(3, 2, 1.0, 1.1) == "价格与资金同步增强"
    assert judge_cluster(3, 2, -1.0, 1.1) == "持续逆向承接"
    assert judge_cluster(-3, -2, -1.0, 1.1) == "价格与资金同步降温"
    assert judge_cluster(0.1, 0.0, 0.0, 1.5) == "高分歧换手"
