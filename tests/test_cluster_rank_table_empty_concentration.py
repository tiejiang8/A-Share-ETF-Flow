import pandas as pd

from etf_temperature.report import _cluster_rank_table, _concentration_row_mask


def _cluster_row(cluster_id: str, ratio: float | None, size: float = 100.0, flow: float = 10.0) -> dict:
    return {
        "cluster_id": cluster_id,
        "cluster_name": f"集群{cluster_id}",
        "member_count": 12,
        "total_size_100m": size,
        "max_contributor_ratio_1d": ratio,
        "top3_contributor_ratio_1d": ratio,
        "weighted_pct_change": 0.01,
        "estimated_net_subscription_100m": flow,
        "estimated_net_subscription_5d_100m": flow * 2,
        "share_increase_member_ratio": 0.5,
        "amount_vs_5d": 1.1,
        "cluster_temperature": 55.0,
        "temperature_direction": "升温",
        "temperature_stage": "升温中段",
        "max_contributor_code_1d": "510300",
        "max_contributor_name_1d": "300ETF",
        "structure_judgement": "均衡",
        "concentration_sample_valid": True,
        "one_day_flow_to_size": flow / size if size else float("nan"),
    }


def test_all_nan_concentration_column_does_not_raise():
    """份额/资金流整体缺失时，集中度列全为 NaN，排行榜必须降级而不是抛 KeyError。"""
    table = pd.DataFrame([_cluster_row("C01", None), _cluster_row("C02", None)])
    for column in ("max_contributor_ratio_1d", "top3_contributor_ratio_1d"):
        assert _cluster_rank_table(table, column, "标题", False, 10, None) == "无数据。"


def test_empty_cluster_frame_does_not_raise():
    table = pd.DataFrame([_cluster_row("C01", 0.6)]).iloc[0:0]
    for column in ("max_contributor_ratio_1d", "top3_contributor_ratio_1d"):
        assert _cluster_rank_table(table, column, "标题", False, 10, None) == "无数据。"


def test_concentration_mask_always_boolean_and_keeps_columns():
    table = pd.DataFrame([_cluster_row("C01", 0.6), _cluster_row("C02", 0.2)])
    mask = _concentration_row_mask(table)
    assert mask.dtype == bool
    assert len(table.loc[mask]) == 2
    assert "max_contributor_ratio_1d" in table.loc[mask].columns

    empty = table.iloc[0:0]
    empty_mask = _concentration_row_mask(empty)
    assert empty_mask.dtype == bool
    assert empty.loc[empty_mask].shape == (0, len(empty.columns))


def test_concentration_rank_still_orders_valid_clusters():
    table = pd.DataFrame([_cluster_row("C01", 0.20), _cluster_row("C02", 0.75)])
    rendered = _cluster_rank_table(table, "max_contributor_ratio_1d", "最大同向ETF贡献比例前10集群", False, 10, None)
    assert "集群C02" in rendered
    assert rendered.index("集群C02") < rendered.index("集群C01") if "集群C01" in rendered else True
